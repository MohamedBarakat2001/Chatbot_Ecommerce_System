import os
import sys
import mysql.connector
from mysql.connector import Error
import difflib
import logging
import re
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

gemini_api_key = os.environ.get("GEMINI_API_KEY")
gemini_api_url = os.environ.get("GEMINI_API_URL",
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent")
# Uncomment for debugging if needed:
# print("DEBUG: GEMINI_API_KEY =", gemini_api_key)
# print("DEBUG: GEMINI_API_URL =", gemini_api_url)

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Synonym sets for confirming single-option prompts
SYNONYMS_YES = {"yes", "y", "ok", "okay", "sure", "choose it", "chooseit", "yeah", "yep", "accept"}
SYNONYMS_NO  = {"no", "n", "nah", "nope", "cancel"}

# --- Helper Functions ---

def get_input(prompt):
    value = input(prompt)
    if value.strip().lower() == "exit":
        print("Exiting conversation.")
        sys.exit(0)
    return value

def get_phone_input(prompt):
    """Force the user to enter a valid phone number (13 characters, starting with '+')."""
    while True:
        phone = get_input(prompt).strip()
        if phone and len(phone) == 13 and phone.startswith('+'):
            return phone
        print("Please enter a valid phone number in the format: +201111111111 (13 characters, including '+').")

def get_email_input(prompt):
    """Prompt until a valid email is entered."""
    pattern = r"^[^@]+@[^@]+\.[^@]+$"
    while True:
        email = get_input(prompt).strip()
        if re.match(pattern, email):
            return email
        print("Please enter a valid E-mail address.")

def get_db_connection():
    """Connect to the 'ecommerce_chatbot_gpt-4' database."""
    try:
        return mysql.connector.connect(
            host="localhost",
            user="root",         # Adjust as needed
            password="",         # Adjust as needed
            database="ecommerce_chatbot_gpt-4"
        )
    except Error as e:
        logging.error("Error connecting to database: %s", e)
        return None

def get_product_categories():
    """Return a list of distinct product categories from products."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT category FROM products")
        categories = [row[0] for row in cursor.fetchall()]
        return categories
    except Error as e:
        logging.error("Error fetching categories: %s", e)
        return []
    finally:
        cursor.close()
        conn.close()

def get_distinct_values_for_category(column_name, category):
    """
    Return distinct values for a given column (color, size, style) from products where category matches.
    """
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        query = f"SELECT DISTINCT {column_name} FROM products WHERE category = %s"
        cursor.execute(query, (category,))
        values = [row[0] for row in cursor.fetchall()]
        return values
    except Error as e:
        logging.error(f"Error fetching {column_name} for {category}: %s", e)
        return []
    finally:
        cursor.close()
        conn.close()

def handle_single_option(option_list, user_input):
    """For a single-option attribute, accept synonyms for confirmation."""
    if len(option_list) == 1:
        lowered = user_input.lower().strip()
        if lowered in SYNONYMS_YES:
            return option_list[0]
        elif lowered in SYNONYMS_NO:
            return None
        else:
            return "invalid"
    return "invalid"

def prompt_for_attribute(attribute_name, options):
    """
    Prompt the user to choose an attribute (color, size, style) from available options.
    If only one option exists and the user responds negatively, cancel the order process.
    """
    if not options:
        return None
    while True:
        if len(options) == 1:
            single = options[0]
            user_input = get_input(f"What {attribute_name} would you like? Only option is '{single}' (type 'ok' to confirm or 'no' to cancel order): ")
            result = handle_single_option(options, user_input)
            if result is None:
                print(f"Chatbot: You chose 'no'. Cancelling the order process.")
                return None
            elif result == "invalid":
                print("Chatbot: Invalid input. Please type 'ok' or 'no'.")
                continue
            else:
                return single
        else:
            joined = ", ".join(options)
            user_input = get_input(f"What {attribute_name} would you like? Available options: {joined}: ")
            lowered = user_input.strip().lower()
            for opt in options:
                if opt.lower() == lowered:
                    return opt
            print(f"Chatbot: Invalid {attribute_name}. Please choose from: {joined}.")

def search_product(product_name):
    """
    Search for a product by name in products.
    Returns the product dict if found; if not found returns {"result": "Product not found"};
    if found but stock is 0, returns {"result": "Product sold out"}.
    """
    conn = get_db_connection()
    if not conn:
        return {"error": "DB connection failed"}
    try:
        cursor = conn.cursor(dictionary=True)
        query = "SELECT * FROM products WHERE LOWER(name) LIKE %s"
        cursor.execute(query, ("%" + product_name.lower() + "%",))
        product = cursor.fetchone()
        if product:
            if product.get("quantity", 0) <= 0:
                return {"result": "Product sold out"}
            return product
        else:
            return {"result": "Product not found"}
    except Error as e:
        logging.error("Error in search_product: %s", e)
        return {"error": "Error searching product"}
    finally:
        cursor.close()
        conn.close()

def search_product_by_attributes(category, color, size, style):
    """
    Search for a product matching the given category, color, size, and style exactly.
    If an exact match is not found, perform a fallback search:
      - Retrieve all products in the category with the confirmed style.
      - Score them based on whether color and size exactly match (0 for match, 1 for mismatch).
      - Return the product with the lowest score.
    Returns the best candidate product or {"result": "Product not found"}.
    """
    conn = get_db_connection()
    if not conn:
        return {"error": "DB connection failed"}
    try:
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT * FROM products
            WHERE category = %s AND LOWER(color) = %s AND LOWER(size) = %s AND LOWER(style) = %s
        """
        cursor.execute(query, (category, color.lower(), size.lower(), style.lower()))
        product = cursor.fetchone()
        if product:
            if product.get("quantity", 0) <= 0:
                return {"result": "Product sold out"}
            return product
        # Fallback: search for products with matching category and style
        query = "SELECT * FROM products WHERE category = %s AND LOWER(style) = %s"
        cursor.execute(query, (category, style.lower()))
        candidates = cursor.fetchall()
        candidates = [p for p in candidates if p.get("quantity", 0) > 0]
        if not candidates:
            return {"result": "Product not found"}
        def score(prod):
            s = 0
            s += 0 if prod["color"].lower() == color.lower() else 1
            s += 0 if prod["size"].lower() == size.lower() else 1
            return s
        candidates.sort(key=score)
        best = candidates[0]
        return best
    except Error as e:
        logging.error("Error in search_product_by_attributes: %s", e)
        return {"error": "Error searching product"}
    finally:
        cursor.close()
        conn.close()

def suggest_alternatives_by_category(category):
    """Return all products in a given category or {"result": "No alternatives found"} if none exist."""
    conn = get_db_connection()
    if not conn:
        return {"error": "DB connection failed"}
    try:
        cursor = conn.cursor(dictionary=True)
        query = "SELECT * FROM products WHERE category = %s"
        cursor.execute(query, (category,))
        products = cursor.fetchall()
        return products if products else {"result": "No alternatives found"}
    except Error as e:
        logging.error("Error in suggest_alternatives_by_category: %s", e)
        return {"error": "Error suggesting alternatives"}
    finally:
        cursor.close()
        conn.close()

def format_product(product):
    """Return a nicely formatted string for a product."""
    return (f"Name: {product['name']}, Category: {product['category']}, Color: {product['color']}, "
            f"Material: {product['material']}, Price: ${float(product['price']):.2f}, "
            f"Style: {product['style']}, Size: {product['size']}")

def get_order_status(order_id):
    conn = get_db_connection()
    if not conn:
        return {"error": "DB connection failed"}
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
        order = cursor.fetchone()
        return order if order else {"result": "Order not found"}
    except Error as e:
        logging.error("Error in get_order_status: %s", e)
        return {"error": "Error retrieving order status"}
    finally:
        cursor.close()
        conn.close()

def cancel_order(order_id):
    conn = get_db_connection()
    if not conn:
        return {"error": "DB connection failed"}
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT status FROM orders WHERE id = %s", (order_id,))
        result = cursor.fetchone()
        if not result:
            return {"result": "Order not found"}
        current_status = result.get("status", "").lower()
        if current_status == "on delivery":
            return {"result": "Order is on delivery and cannot be cancelled"}
        cursor.execute("UPDATE orders SET status = 'Cancelled' WHERE id = %s", (order_id,))
        conn.commit()
        return {"status": "Order cancelled"}
    except Error as e:
        logging.error("Error in cancel_order: %s", e)
        return {"error": "Error cancelling order"}
    finally:
        cursor.close()
        conn.close()

def place_order(order_details):
    """
    Insert an order into the orders table.
    Expects columns: product_id, product_name, color, material, style, size, price, quantity,
             shipping_address, customer_name, email, phone, payment_info, status.
    """
    conn = get_db_connection()
    if not conn:
        return {"error": "DB connection failed"}
    try:
        cursor = conn.cursor()
        query = """
            INSERT INTO orders 
            (product_id, product_name, color, material, style, size, price, quantity, 
             shipping_address, customer_name, email, phone, payment_info, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        data = (
            order_details.get("product_id"),
            order_details.get("product_name"),
            order_details.get("color"),
            order_details.get("material"),
            order_details.get("style"),
            order_details.get("size"),
            order_details.get("price"),
            order_details.get("quantity"),
            order_details.get("shipping_address"),
            order_details.get("customer_name"),
            order_details.get("email"),
            order_details.get("phone"),
            order_details.get("payment_info"),
            "Processing"
        )
        cursor.execute(query, data)
        conn.commit()
        return {"status": "Order placed", "order_id": cursor.lastrowid}
    except Error as e:
        logging.error("Error in place_order: %s", e)
        return {"error": "Error placing order"}
    finally:
        cursor.close()
        conn.close()

def determine_intent(user_input):
    lower_input = user_input.lower()
    if "cancel order" in lower_input or "cancel my order" in lower_input:
        return "cancel_order"
    if ("order status" in lower_input or "track my order" in lower_input or 
        "status of my order" in lower_input or "status of an order" in lower_input):
        return "order_status"
    if ("do you have" in lower_input or ("i want" in lower_input and not re.search(r'\border\b|\bbuy\b|\bpurchase\b', lower_input))):
        return "inquire_product"
    if ("show me your products" in lower_input or "list your products" in lower_input or 
        ("products" in lower_input and ("show" in lower_input or "list" in lower_input))):
        return "list_products"
    if re.match(r'^(how|what|where|when|why|which)\b', user_input.strip(), re.IGNORECASE):
        return "general"
    if "add it to the cart" in lower_input:
        return "add_to_cart"
    if re.search(r'\border\b|\bbuy\b|\bpurchase\b', user_input, re.IGNORECASE):
        return "place_order"
    elif re.search(r'\bfind\b|\bavailable\b|\bsearch\b', user_input, re.IGNORECASE):
        return "search_product"
    else:
        return "general"

def extract_product_name(user_input):
    cleaned = re.sub(r'(?i)i want to order', '', user_input).strip()
    cleaned = re.sub(r'(?i)^(a|an|the)\s+', '', cleaned).strip()
    return cleaned

def generate_response(prompt):
    if not gemini_api_key:
        logging.error("GEMINI_API_KEY not set.")
        return "I'm sorry, the text generation service is not configured."
    url_with_key = f"{gemini_api_url}?key={gemini_api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        response = requests.post(url_with_key, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        generated_text = None
        if "candidates" in result and len(result["candidates"]) > 0:
            candidate = result["candidates"][0]
            if ("content" in candidate and "parts" in candidate["content"] and 
                len(candidate["content"]["parts"]) > 0):
                generated_text = candidate["content"]["parts"][0].get("text")
            else:
                logging.error("Candidate output missing: %s", candidate)
        else:
            logging.error("No candidates found: %s", result)
        if not generated_text:
            generated_text = "I'm sorry, I couldn't generate a response."
        return generated_text
    except Exception as e:
        logging.error("Error generating response via Gemini: %s", e)
        return "I'm sorry, I couldn't generate a response."

def chat():
    print("Welcome to the automated e-commerce chatbot! Type 'exit' at any prompt to quit; either you should choose from the choices.")
    conversation_history = "Conversation with an e-commerce chatbot:\n"
    
    while True:
        user_input = get_input("You: ")
        if user_input.lower() == "exit":
            print("Exiting conversation.")
            break
        conversation_history += "User: " + user_input + "\n"
        intent = determine_intent(user_input)
        
        if intent == "order_status":
            order_id_input = get_input("Please enter your order ID to check its status: ")
            try:
                order_id = int(order_id_input)
            except ValueError:
                print("Chatbot: Please enter a valid order ID.")
                continue
            order = get_order_status(order_id)
            if order.get("error") or order.get("result") == "Order not found":
                response_text = "Order not found."
            else:
                response_text = f"Your order status is: {order.get('status', 'Unknown')}"
        
        elif intent == "cancel_order":
            order_id_input = get_input("Please enter your order ID to cancel: ")
            try:
                order_id = int(order_id_input)
            except ValueError:
                print("Chatbot: Please enter a valid order ID.")
                continue
            cancel_result = cancel_order(order_id)
            if cancel_result.get("error"):
                response_text = "There was an error cancelling your order."
            elif cancel_result.get("result"):
                response_text = cancel_result.get("result")
            else:
                response_text = "Your order has been cancelled successfully."
        
        elif intent == "list_products":
            conn = get_db_connection()
            if not conn:
                response_text = "Database connection error."
            else:
                try:
                    cursor = conn.cursor(dictionary=True)
                    cursor.execute("SELECT * FROM products")
                    products = cursor.fetchall()
                    if products:
                        formatted = "\n".join(format_product(p) for p in products)
                        response_text = f"Here are our products:\n{formatted}"
                    else:
                        response_text = "No products available."
                except Error as e:
                    logging.error("Error listing products: %s", e)
                    response_text = "Error retrieving products."
                finally:
                    cursor.close()
                    conn.close()
        
        elif intent == "inquire_product":
            inquiry_query = re.sub(r'(do you have|i want)', '', user_input, flags=re.IGNORECASE).strip()
            inquiry_query = re.sub(r'\b(any|all|the)\b', '', inquiry_query, flags=re.IGNORECASE).strip()
            result = search_product(inquiry_query)
            if result.get("error"):
                response_text = "There was an error checking our inventory."
            elif result.get("result") == "Product not found":
                categories = get_product_categories()
                matches = difflib.get_close_matches(inquiry_query, categories, n=1, cutoff=0.3)
                matching_category = matches[0] if matches else None
                if matching_category:
                    alt_products = suggest_alternatives_by_category(matching_category)
                    if isinstance(alt_products, dict) and alt_products.get("result") == "No alternatives found":
                        response_text = f"Sorry, we do not have any '{inquiry_query}' available."
                    else:
                        formatted_alts = "\n".join(format_product(p) for p in alt_products)
                        response_text = f"Sorry, we do not have '{inquiry_query}' available. However, here are alternatives in the '{matching_category}' category:\n{formatted_alts}"
                else:
                    available_categories = get_product_categories()
                    response_text = f"Sorry, we do not have '{inquiry_query}' available. Our available categories are: {', '.join(available_categories)}."
            else:
                response_text = f"Yes, we have {format_product(result)} available."
        
        elif intent == "search_product":
            product_query = re.sub(r'\b(find|search|available)\b', '', user_input, flags=re.IGNORECASE).strip()
            result = search_product(product_query)
            if result.get("error"):
                response_text = "There was an error searching for the product."
            elif result.get("result") == "Product not found":
                response_text = f"Sorry, we do not have '{product_query}' in our inventory."
            elif result.get("result") == "Product sold out":
                response_text = f"Sorry, '{product_query}' is sold out."
            else:
                response_text = f"Found product: {format_product(result)}"
        
        elif intent == "add_to_cart":
            response_text = "We currently support placing orders directly, not a cart-based flow. Please use 'order' or 'buy'."
        
        elif intent == "place_order":
            available_categories = get_product_categories()
            if not available_categories:
                print("Chatbot: No product categories available.")
                continue
            cat_input = get_input(f"Please specify the product category from the following options: {', '.join(available_categories)}: ")
            while cat_input.strip().lower() not in [c.lower() for c in available_categories]:
                cat_input = get_input(f"Invalid category. Please choose from: {', '.join(available_categories)}: ")
            for c in available_categories:
                if c.lower() == cat_input.strip().lower():
                    category = c
                    break
            
            colors = get_distinct_values_for_category("color", category)
            if not colors:
                print("Chatbot: Sorry, no colors available for this category.")
                continue
            color = prompt_for_attribute("color", colors)
            if color is None:
                print("Chatbot: Order cancelled.")
                continue
            
            sizes = get_distinct_values_for_category("size", category)
            if not sizes:
                print("Chatbot: Sorry, no sizes available for this category.")
                continue
            size = prompt_for_attribute("size", sizes)
            if size is None:
                print("Chatbot: Order cancelled.")
                continue
            
            styles = get_distinct_values_for_category("style", category)
            if not styles:
                print("Chatbot: Sorry, no styles available for this category.")
                continue
            style = prompt_for_attribute("style", styles)
            if style is None:
                print("Chatbot: Order cancelled.")
                continue
            
            existing_product = search_product_by_attributes(category, color, size, style)
            if existing_product.get("result") in ["Product not found", "Product sold out"]:
                alt_products = suggest_alternatives_by_category(category)
                if isinstance(alt_products, dict) and alt_products.get("result") == "No alternatives found":
                    response_text = f"Sorry, we do not have a product matching that configuration in '{category}', and no alternatives are available."
                else:
                    formatted_alts = "\n".join(format_product(p) for p in alt_products)
                    response_text = (f"Sorry, we do not have a product matching that configuration in '{category}'.\n"
                                     f"Available alternatives in this category:\n{formatted_alts}")
                print("Chatbot:", response_text)
                conversation_history += "Assistant: " + response_text + "\n"
                continue
            elif existing_product.get("error"):
                print("Chatbot: Error searching for product. Try again later.")
                continue
            
            product_price = existing_product["price"]
            available_stock = existing_product["quantity"]
            print(f"Chatbot: We have '{existing_product['name']}' available in {existing_product['color']}, "
                  f"material: {existing_product['material']}, style: {existing_product['style']}, size: {existing_product['size']}, "
                  f"priced at ${float(product_price):.2f}.")
            print(f"Chatbot: Available stock: {available_stock} unit(s).")
            
            while True:
                qty_input = get_input("How many would you like to order? (enter a number): ")
                try:
                    quantity = int(qty_input)
                except ValueError:
                    print("Chatbot: Please enter a valid number.")
                    continue
                if quantity > available_stock:
                    print(f"Chatbot: Sorry, only {available_stock} unit(s) are available. Please choose a quantity ≤ {available_stock}.")
                else:
                    break
            
            try:
                total_price = float(product_price) * quantity
            except (TypeError, ValueError):
                total_price = 0.0
            
            confirm = get_input(f"The total price for {quantity} unit(s) of '{existing_product['name']}' is ${total_price:.2f}. Do you accept this price? (yes/no): ").strip().lower()
            if confirm not in SYNONYMS_YES:
                response_text = "Order cancelled."
                print("Chatbot:", response_text)
                conversation_history += "Assistant: " + response_text + "\n"
                continue
            
            customer_name = get_input("Please enter your full name: ")
            shipping_address = get_input("Please enter your address (street, city, state, zip): ")
            email = get_email_input("Please enter your email address (for order confirmation and tracking): ")
            phone = get_phone_input("Please enter your phone number (e.g., +201111111111): ")
            payment_info = get_input("Please enter your payment information (enter card details or type 'cash' for cash on delivery): ")
            
            order_details = {
                "product_id": existing_product["id"],
                "product_name": existing_product["name"],
                "color": existing_product["color"],
                "material": existing_product["material"],
                "style": existing_product["style"],
                "size": existing_product["size"],
                "price": existing_product["price"],
                "quantity": quantity,
                "shipping_address": shipping_address,
                "customer_name": customer_name,
                "email": email,
                "phone": phone,
                "payment_info": payment_info
            }
            insert_result = place_order(order_details)
            if insert_result.get("error"):
                response_text = "There was an error placing your order."
            else:
                response_text = f"Order placed successfully with order ID: {insert_result.get('order_id')}"
        
        else:
            prompt = conversation_history + "Assistant:"
            response_text = generate_response(prompt)
        
        print("Chatbot:", response_text)
        conversation_history += "Assistant: " + response_text + "\n"

if __name__ == "__main__":
    chat()
