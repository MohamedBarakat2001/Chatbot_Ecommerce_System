import os
import sys
import mysql.connector
from mysql.connector import Error
import difflib
import logging
import re
import requests
from dotenv import load_dotenv

# Load environment variables early
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

# --- Helper for Input with Immediate Exit and Validation ---
def get_input(prompt):
    value = input(prompt)
    if value.strip().lower() == "exit":
        print("Exiting conversation.")
        sys.exit(0)
    return value

def get_phone_input(prompt):
    """Prompt the user for a phone number in the correct format (e.g., +201111111111, 13 characters)."""
    while True:
        phone = get_input(prompt)
        # Remove any spaces
        phone = phone.strip()
        if len(phone) == 13 and phone.startswith('+'):
            return phone
        else:
            print("Please enter a valid phone number in the format: +201111111111 (13 characters, including '+').")

# --- Database Connection Setup ---
def get_db_connection():
    """Connect to the database 'ecommerce_chatbot_gpt-4'."""
    try:
        connection = mysql.connector.connect(
            host="localhost",
            user="root",         # Adjust if needed
            password="",         # Adjust if needed
            database="ecommerce_chatbot_gpt-4"
        )
        return connection
    except Error as e:
        logging.error("Error connecting to database: %s", e)
        return None

# --- Get Distinct Product Categories ---
def get_product_categories():
    """Retrieve a list of distinct product categories from the products table."""
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        cursor = conn.cursor()
        query = "SELECT DISTINCT category FROM products"
        cursor.execute(query)
        categories = [row[0] for row in cursor.fetchall()]
        return categories
    except Error as e:
        logging.error("Error fetching product categories: %s", e)
        return []
    finally:
        cursor.close()
        conn.close()

# --- Get Available Colors for a Category ---
def get_available_colors(category):
    """Return a list of distinct colors for products in the given category."""
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        cursor = conn.cursor()
        query = "SELECT DISTINCT color FROM products WHERE category = %s"
        cursor.execute(query, (category,))
        colors = [row[0] for row in cursor.fetchall()]
        return colors
    except Error as e:
        logging.error("Error fetching available colors: %s", e)
        return []
    finally:
        cursor.close()
        conn.close()

# --- Get Available Sizes for a Category ---
def get_available_sizes(category):
    """Return a list of distinct sizes for products in the given category."""
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        cursor = conn.cursor()
        query = "SELECT DISTINCT size FROM products WHERE category = %s"
        cursor.execute(query, (category,))
        sizes = [row[0] for row in cursor.fetchall()]
        return sizes
    except Error as e:
        logging.error("Error fetching available sizes: %s", e)
        return []
    finally:
        cursor.close()
        conn.close()

# --- Get Available Styles for a Category ---
def get_available_styles(category):
    """Return a list of distinct styles for products in the given category."""
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        cursor = conn.cursor()
        query = "SELECT DISTINCT style FROM products WHERE category = %s"
        cursor.execute(query, (category,))
        styles = [row[0] for row in cursor.fetchall()]
        return styles
    except Error as e:
        logging.error("Error fetching available styles: %s", e)
        return []
    finally:
        cursor.close()
        conn.close()

# --- Search Product by Attributes ---
def search_product_by_attributes(category, color, size, style):
    """
    Search for a product that matches the given category, color, size, and style.
    Returns the product dict if found, or {"result": "Product not found"}.
    If found but stock is zero, returns {"result": "Product sold out"}.
    """
    conn = get_db_connection()
    if conn is None:
        return {"error": "Database connection failed"}
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
        else:
            return {"result": "Product not found"}
    except Error as e:
        logging.error("Error in search_product_by_attributes: %s", e)
        return {"error": "Error searching product"}
    finally:
        cursor.close()
        conn.close()

# --- Suggest Alternatives by Category ---
def suggest_alternatives_by_category(category):
    """
    Suggest alternative products in the given category from the database.
    """
    conn = get_db_connection()
    if conn is None:
        return {"error": "Database connection failed"}
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

# --- Format Product for Display ---
def format_product(product):
    """Return a friendly formatted string for a product."""
    return (f"Name: {product['name']}, Category: {product['category']}, Color: {product['color']}, "
            f"Material: {product['material']}, Price: ${float(product['price']):.2f}, "
            f"Style: {product['style']}, Size: {product['size']}")

# --- Get Order Status ---
def get_order_status(order_id):
    """Retrieve the order record by order ID."""
    conn = get_db_connection()
    if conn is None:
        return {"error": "Database connection failed"}
    try:
        cursor = conn.cursor(dictionary=True)
        query = "SELECT * FROM orders WHERE id = %s"
        cursor.execute(query, (order_id,))
        order = cursor.fetchone()
        return order if order else {"result": "Order not found"}
    except Error as e:
        logging.error("Error in get_order_status: %s", e)
        return {"error": "Error retrieving order status"}
    finally:
        cursor.close()
        conn.close()

# --- Cancel Order ---
def cancel_order(order_id):
    """
    Cancel an order by updating its status to 'Cancelled', only if its current status is not 'On Delivery'.
    """
    conn = get_db_connection()
    if conn is None:
        return {"error": "Database connection failed"}
    try:
        cursor = conn.cursor(dictionary=True)
        query = "SELECT status FROM orders WHERE id = %s"
        cursor.execute(query, (order_id,))
        result = cursor.fetchone()
        if not result:
            return {"result": "Order not found"}
        current_status = result.get("status", "").lower()
        if current_status == "on delivery":
            return {"result": "Order is on delivery and cannot be cancelled"}
        update_query = "UPDATE orders SET status = 'Cancelled' WHERE id = %s"
        cursor.execute(update_query, (order_id,))
        conn.commit()
        return {"status": "Order cancelled"}
    except Error as e:
        logging.error("Error in cancel_order: %s", e)
        return {"error": "Error cancelling order"}
    finally:
        cursor.close()
        conn.close()

# --- Place Order ---
def place_order(order_details):
    """
    Insert an order into the orders table.
    Expects columns: product_id, product_name, color, material, style, size, price, quantity,
    shipping_address, customer_name, email, phone, payment_info, status.
    """
    conn = get_db_connection()
    if conn is None:
        return {"error": "Database connection failed"}
    try:
        cursor = conn.cursor()
        query = """
            INSERT INTO orders 
            (product_id, product_name, color, material, style, size, price, quantity, shipping_address, customer_name, email, phone, payment_info, status)
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
            "Processing"  # Default status
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

# --- Intent Determination ---
def determine_intent(user_input):
    """
    Determine the user's intent.
      - If input contains phrases for order cancellation, return "cancel_order".
      - If input contains phrases for order status, return "order_status".
      - If input starts with question words, treat as general.
      - If it contains "add it to the cart", return "add_to_cart".
      - If it contains order-related keywords, return "place_order".
      - If it indicates search intent, return "search_product".
      - Otherwise, default to "general".
    """
    lower_input = user_input.lower()
    if "cancel order" in lower_input or "cancel my order" in lower_input:
        return "cancel_order"
    if "order status" in lower_input or "track my order" in lower_input or "status of my order" in lower_input:
        return "order_status"
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

# --- Helper to Extract Product Name ---
def extract_product_name(user_input):
    """
    Extract the core product name from the user's query.
    E.g., "I want to order a hoodie" becomes "hoodie".
    """
    cleaned = re.sub(r'(?i)i want to order', '', user_input).strip()
    cleaned = re.sub(r'(?i)^(a|an|the)\s+', '', cleaned).strip()
    return cleaned

# --- Gemini API Text Generation ---
def generate_response(prompt):
    """
    Generate a response using the Gemini API.
    Expects GEMINI_API_URL and GEMINI_API_KEY to be set.
    """
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    gemini_api_url = os.environ.get("GEMINI_API_URL", 
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent")
    
    if not gemini_api_key:
        logging.error("GEMINI_API_KEY environment variable not set.")
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
            if ("content" in candidate and "parts" in candidate["content"] and len(candidate["content"]["parts"]) > 0):
                generated_text = candidate["content"]["parts"][0].get("text")
            else:
                logging.error("Candidate output missing: %s", candidate)
        else:
            logging.error("No candidates found in Gemini response: %s", result)
        
        if not generated_text:
            generated_text = "I'm sorry, I couldn't generate a response."
        return generated_text
    except Exception as e:
        logging.error("Error generating response via Gemini: %s", e)
        return "I'm sorry, I couldn't generate a response."

# --- Main Chat Loop ---
def chat():
    print("Welcome to the automated e-commerce chatbot! Type 'exit' at any prompt to quit.")
    conversation_history = "Conversation with an e-commerce chatbot:\n"
    
    while True:
        user_input = get_input("You: ")
        if user_input.lower() == "exit":
            print("Exiting conversation.")
            break
        
        conversation_history += "User: " + user_input + "\n"
        intent = determine_intent(user_input)
        
        # --- Order Status Query Flow ---
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
        
        # --- Order Cancellation Flow ---
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
        
        # --- Search Product Flow ---
        elif intent == "search_product":
            product_query = re.sub(r'\b(find|search|available)\b', '', user_input, flags=re.IGNORECASE).strip()
            result = search_product(product_query)
            if result.get("result") == "Product not found":
                alternatives = suggest_alternatives(product_query)
                if isinstance(alternatives, dict) and alternatives.get("result") == "No alternatives found":
                    response_text = f"Sorry, we did not find any '{product_query}' in our inventory, and no similar products were found."
                else:
                    formatted_alts = "\n".join(format_product(p) for p in alternatives)
                    response_text = f"Sorry, we did not find any '{product_query}' in our inventory. Did you mean:\n{formatted_alts}"
            elif result.get("error"):
                response_text = "There was an error searching for the product."
            elif result.get("result") == "Product sold out":
                response_text = f"Sorry, '{product_query}' is sold out."
            else:
                response_text = f"Found product: {format_product(result)}"
        
        # --- Add to Cart Flow ---
        elif intent == "add_to_cart":
            product_query = extract_product_name(user_input)
            existing_product = search_product(product_query)
            if isinstance(existing_product, dict) and existing_product.get("result") in ["Product not found", "Product sold out"]:
                response_text = "Sorry, we don't have that product available."
            else:
                response_text = (f"Okay, '{existing_product.get('name')}' in {existing_product.get('color')}, "
                                 f"material: {existing_product.get('material')}, style: {existing_product.get('style')}, size: {existing_product.get('size')}, "
                                 f"priced at ${float(existing_product.get('price')):.2f} has been added to your cart. "
                                 f"Is there anything else I can help you with?")
        
        # --- Place Order Flow ---
        elif intent == "place_order":
            # Ask for product category first
            available_categories = get_product_categories()
            category = get_input(f"Please specify the product category from the following options: {', '.join(available_categories)}: ").strip().lower()
            while category not in [c.lower() for c in available_categories]:
                category = get_input(f"Invalid category. Please choose from: {', '.join(available_categories)}: ").strip().lower()
            
            # Ask for desired color
            colors = get_available_colors(category)
            if not colors:
                print("Chatbot: Sorry, no colors available for this category.")
                continue
            color = get_input(f"What color would you like? Available options: {', '.join(colors)}: ").strip().lower()
            while color not in [c.lower() for c in colors]:
                color = get_input(f"Invalid color. Please choose from: {', '.join(colors)}: ").strip().lower()
            
            # Ask for desired size
            sizes = get_available_sizes(category)
            if not sizes:
                print("Chatbot: Sorry, no sizes available for this category.")
                continue
            size = get_input(f"What size would you like? Available options: {', '.join(sizes)}: ").strip().lower()
            while size not in [s.lower() for s in sizes]:
                size = get_input(f"Invalid size. Please choose from: {', '.join(sizes)}: ").strip().lower()
            
            # Ask for desired style
            styles = get_available_styles(category)
            if not styles:
                print("Chatbot: Sorry, no styles available for this category.")
                continue
            style = get_input(f"What style would you like? Available options: {', '.join(styles)}: ").strip().lower()
            while style not in [s.lower() for s in styles]:
                style = get_input(f"Invalid style. Please choose from: {', '.join(styles)}: ").strip().lower()
            
            # Search for product by attributes
            existing_product = search_product_by_attributes(category, color, size, style)
            if isinstance(existing_product, dict) and existing_product.get("result") in ["Product not found", "Product sold out"]:
                alternatives = suggest_alternatives_by_category(category)
                if isinstance(alternatives, dict) and alternatives.get("result") == "No alternatives found":
                    response_text = f"Sorry, we do not have a product matching that configuration in '{category}', and no alternatives are available."
                else:
                    formatted_alts = "\n".join(format_product(p) for p in alternatives)
                    response_text = (f"Sorry, we do not have a product matching that configuration in '{category}'.\n"
                                     f"Available alternatives in this category:\n{formatted_alts}")
                print("Chatbot:", response_text)
                conversation_history += "Assistant: " + response_text + "\n"
                continue
            
            # Display product details from the database.
            product_price = existing_product.get("price")
            available_stock = existing_product.get("quantity")
            print(f"Chatbot: We have '{existing_product.get('name')}' available in {existing_product.get('color')}, "
                  f"material: {existing_product.get('material')}, style: {existing_product.get('style')}, size: {existing_product.get('size')}, "
                  f"priced at ${float(product_price):.2f}.")
            print(f"Chatbot: Available stock: {available_stock} unit(s).")
            
            # Ask for quantity ensuring it does not exceed available stock.
            while True:
                quantity_input = get_input("How many would you like to order? (enter a number): ")
                try:
                    quantity = int(quantity_input)
                except ValueError:
                    print("Chatbot: Please enter a valid number.")
                    continue
                if quantity > available_stock:
                    print(f"Chatbot: Sorry, only {available_stock} unit(s) are available. Please choose a quantity less than or equal to {available_stock}.")
                else:
                    break
            
            try:
                total_price = float(product_price) * quantity
            except (TypeError, ValueError):
                total_price = 0.0
            
            confirmation_price = get_input(f"The total price for {quantity} unit(s) of '{existing_product.get('name')}' is ${total_price:.2f}. Do you accept this price? (yes/no): ").strip().lower()
            if confirmation_price != "yes":
                response_text = "Order cancelled."
                print("Chatbot:", response_text)
                conversation_history += "Assistant: " + response_text + "\n"
                continue
            
            # --- Proceed to Checkout ---
            print("Okay, let's proceed to checkout. To complete your order, I'll need the following information:")
            shipping_info = get_input("1. Shipping Address (full name, street, city, state, zip): ")
            # Extract customer name from the shipping info (assume full name is before the first comma)
            customer_name = shipping_info.split(",")[0].strip() if "," in shipping_info else shipping_info.strip()
            email = get_input("2. Email Address (for order confirmation and tracking): ")
            phone = get_phone_input("3. Phone Number (enter the key first then the rest of the number, ex: +201111111111): ")
            payment_info = get_input("4. Payment Information (enter card details or type 'cash' for cash on delivery): ")
            
            order_details = {
                "product_id": existing_product.get("id"),
                "product_name": existing_product.get("name"),
                "color": existing_product.get("color"),
                "material": existing_product.get("material"),
                "style": existing_product.get("style"),
                "size": existing_product.get("size"),
                "price": existing_product.get("price"),
                "quantity": quantity,
                "shipping_address": shipping_info,
                "customer_name": customer_name,
                "email": email,
                "phone": phone,
                "payment_info": payment_info
            }
            result = place_order(order_details)
            if result.get("error"):
                response_text = "There was an error placing your order."
            else:
                response_text = f"Order placed successfully with order ID: {result.get('order_id')}"
        
        # --- General Query Flow ---
        else:
            prompt = conversation_history + "Assistant:"
            response_text = generate_response(prompt)
        
        print("Chatbot:", response_text)
        conversation_history += "Assistant: " + response_text + "\n"

if __name__ == "__main__":
    chat()
