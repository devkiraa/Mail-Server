import os
import logging
import re
from flask import Flask, request, jsonify, abort
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv, set_key
from datetime import datetime
import uuid
from concurrent.futures import ThreadPoolExecutor
import csv
import time

# Suppress Flask's request log messages
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# Suppress Flask's 'Serving Flask app' and 'Debug mode' messages
flask_log = logging.getLogger('flask')
flask_log.setLevel(logging.ERROR)

# Load environment variables
load_dotenv()

# Flask app
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "default_secret_key")

# Dictionary to track the status of email requests
email_status = {}

# Email validation pattern
EMAIL_REGEX = re.compile(r"[^@]+@[^@]+\.[^@]+")

# Create a thread pool executor to manage the email sending asynchronously
executor = ThreadPoolExecutor(max_workers=5)

# CSV file path
CSV_FILE_PATH = "email_log.csv"

def initialize_csv_log():
    """Create a CSV log file if it doesn't exist and add headers."""
    if not os.path.exists(CSV_FILE_PATH):
        with open(CSV_FILE_PATH, mode="w", newline='') as file:
            writer = csv.writer(file)
            writer.writerow(["Request ID", "Sender Email", "Recipient", "Subject", "Date", "Status"])

def log_email_to_csv(request_id, sender_email, recipient, subject, status, error_details=None):
    """Log email request details to a CSV file with detailed error info."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(CSV_FILE_PATH, mode="a", newline='') as file:
        writer = csv.writer(file)
        if error_details:
            writer.writerow([request_id, sender_email, recipient, subject, now, status, error_details])
        else:
            writer.writerow([request_id, sender_email, recipient, subject, now, status])

def send_email(subject, recipient, body, is_html, request_id, sender_email, sender_password, sender_name, cc=None, bcc=None):
    """Send an email to multiple recipients and log the action in a CSV file."""
    try:
        message = MIMEMultipart()
        message["From"] = f"{sender_name} <{sender_email}>"
        message["To"] = recipient
        if cc:
            message["Cc"] = cc
        if bcc:
            message["Bcc"] = bcc
        message["Subject"] = subject
        message.attach(MIMEText(body, "html" if is_html else "plain"))

        # Send the email
        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(message)

        # Log success to the CSV file
        log_email_to_csv(request_id, sender_email, recipient, subject, "sent")
        email_status[request_id] = "sent"  # Update the status
    except Exception as e:
        # Log failure to the CSV file
        log_email_to_csv(request_id, sender_email, recipient, subject, f"failed ({e})")
        email_status[request_id] = f"failed ({e})"  # Update the status

def send_email_with_retry(subject, recipients, body, is_html, request_id, sender_email, sender_password, sender_name, cc=None, bcc=None, retries=3, delay=5):
    """Send an email with retry mechanism using exponential backoff."""
    attempt = 0
    while attempt < retries:
        try:
            send_email(subject, recipients, body, is_html, request_id, sender_email, sender_password, sender_name, cc, bcc)
            return
        except Exception as e:
            attempt += 1
            if attempt < retries:
                print(f"Attempt {attempt} failed. Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                print(f"Failed to send email after {retries} attempts.")
                email_status[request_id] = f"failed ({e})"

def validate_email_data(data):
    """Validate the email data."""
    subject = data.get("subject")
    recipient = data.get("recipient")
    body = data.get("body")
    
    if not all([subject, recipient, body]):
        abort(400, description="Missing required fields")
    
    if not EMAIL_REGEX.match(recipient):
        abort(400, description="Invalid email format")
    
    if len(subject) > 255 or len(body) > 10000:
        abort(400, description="Subject or body exceeds character limits")

@app.route("/send-email", methods=["POST"])
def handle_send_email():
    data = request.json
    validate_email_data(data)
    
    subject = data["subject"]
    recipient = data["recipient"]
    body = data["body"]
    is_html = data.get("is_html", False)
    cc = data.get("cc", None)
    bcc = data.get("bcc", None)

    # Get the email, password, and sender name from the environment variables
    sender_email = os.getenv("USER_EMAIL")
    sender_password = os.getenv("USER_APP_PASSWORD")
    sender_name = os.getenv("EMAIL_FROM_NAME")

    if not sender_email or not sender_password:
        return jsonify({"error": "Missing user credentials in .env"}), 400

    # Generate a unique request ID for this email request
    request_id = str(uuid.uuid4())

    # Send the email asynchronously using the thread pool
    executor.submit(send_email_with_retry, subject, recipient, body, is_html, request_id, sender_email, sender_password, sender_name, cc, bcc)

    return jsonify({"message": "Email request processed", "request_id": request_id}), 200

@app.route("/email-status/<request_id>", methods=["GET"])
def get_email_status(request_id):
    """Check the status of an email request."""
    status = email_status.get(request_id, "Request ID not found")
    return jsonify({"request_id": request_id, "status": status})

if __name__ == "__main__":
    # Initialize CSV log
    initialize_csv_log()

    # Get the port from the environment variable, defaulting to 10000 if not set
    port = int(os.getenv("PORT", 10000))

    # Run the Flask server on the specified port, bound to 0.0.0.0 to allow external access
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

