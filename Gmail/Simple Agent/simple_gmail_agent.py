"""
    Gmail Agent - simple version (one-by-one deletion with confirmation)

    This agent retrieves emails within a specified date range (["start", "end")),  
    retrieval being capped at max_emails = 20 (parameter can be changed within the 
    script), in order to prevent excessive API usage.

    Each email is analyzed by Claude using its sender, subject, and the first 300 
    characters of the plain-text body. The model classifies each email as either 
    KEEP or DELETE.

    For emails marked DELETE, the system prompts the user for confirmation before
    moving the message to Trash (where it will be automatically removed after 30
    days). KEEP emails are skipped without action.

    At the end of execution, the agent displays the total number of deleted emails.

    IMPORTANT: 
    Claude analyzes the first 300 characters of each email's body. If some of your
    emails contain sensitive information, such as passwords or banking details, 
    you should remove those emails from your inbox before running this script.

    Alternatively, you can adjust the date range in the script to exclude the 
    days during which those sensitive emails were received.
"""

import imaplib
import email
from email.header import decode_header
import anthropic
from dotenv import load_dotenv
import os
from datetime import datetime

load_dotenv()


# Establish IMAP connection using Gmail credentials from .env
def connect_to_gmail():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(os.getenv("GMAIL_ADDRESS"), os.getenv("GMAIL_APP_PASSWORD"))
    return mail


# Extract the first available plain-text part of the email body
# (ignores HTML for simplicity and consistency)
def get_email_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode()
                except:
                    return ""
    else:
        try:
            return msg.get_payload(decode=True).decode()
        except:
            return ""
    return ""


# Generator: streams emails one-by-one instead of loading everything into memory
def fetch_emails(mail, start_date, end_date, max_emails=None):
    mail.select("inbox")

    # Convert datetime objects to IMAP-compatible date format (e.g. 20-Apr-2026)
    start = start_date.strftime("%d-%b-%Y")
    end = end_date.strftime("%d-%b-%Y")

    # IMAP query: SINCE is inclusive, BEFORE is exclusive → [start, end)
    query = f'SINCE "{start}" BEFORE "{end}"'

    _, messages = mail.uid("search", None, query)

    # Safely extract email IDs (handles empty inbox results)
    email_ids = messages[0].split() if messages and messages[0] else []

    # Reverse so newest emails are processed first
    email_ids = email_ids[::-1]

    # Optional safety cap to avoid excessive API calls
    if max_emails is not None:
        email_ids = email_ids[:max_emails]

    for email_id in email_ids:
        _, msg_data = mail.uid("fetch", email_id, "(RFC822)")

        # Skip malformed or empty responses
        if not msg_data or not msg_data[0]:
            continue
        
        msg = email.message_from_bytes(msg_data[0][1])
        
        # Decode subject safely (handles encoded headers and missing values)
        raw_subject = msg["Subject"] or ""
        subject_parts = decode_header(raw_subject)

        subject = ""
        for part, encoding in subject_parts:
            if isinstance(part, bytes):
                subject += part.decode(encoding or "utf-8", errors="ignore")
            else:
                subject += part

        sender = msg["From"]
        body = get_email_body(msg)

        yield {
            "id": email_id,
            "subject": subject,
            "sender": sender,
            "body": body[:300],  # truncate to reduce token usage
        }


# Uses Claude to classify a single email
def classify_email(client, subject, sender, body):
    prompt = f"""Classify this email as either 'DELETE' or 'KEEP'.

DELETE if it is any of these:
- Promotional or sale emails
- Newsletters
- Social media notifications (Facebook, Instagram, Twitter, LinkedIn)
- Google security alerts (sign-in notifications, new device alerts)
- Payment processing notifications ("your payment is being processed")
- Shipping updates ("your order is on its way", "out for delivery") - Old or completed shipping updates
- App notifications (Just Eat, Uber, etc.)
- "Welcome to..." onboarding emails
- Survey or feedback requests
- Subscription renewal reminders
- Travel notifications/ updates 

KEEP if it is any of these:
- Order confirmations with order numbers
- Payment receipts or proof of payment
- Invoices
- Bank statements
- Important personal emails
- Work related emails
- Legal or official documents
- Account creation confirmations
- Travel bookings (flights, hotels)

Sender: {sender}
Subject: {subject}
Body: {body}

Respond with ONLY one word: DELETE or KEEP. Then a brief reason after a dash."""
    
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text


# Main execution loop: processes emails one-by-one with user confirmation
def run_email_agent():
    print("🔌 Connecting to Gmail...")
    mail = connect_to_gmail()
    print("✅ Connected!\n")
    print("📬 Streaming emails between the given dates...\n")
    
    client = anthropic.Anthropic()

    # Define 👇HERE the time window for cleanup
    start_date = datetime(2026, 4, 20)
    end_date = datetime(2026, 4, 28)
    
    print(f"📅 Date range: {start_date.date()} → {end_date.date()}\n")
    
    print("🤖 Claude is classifying your emails...\n")
    print("-" * 60)
    
    deleted_count = 0

    # Set HERE the max_emails value 
    for e in fetch_emails(mail, start_date, end_date, max_emails=20):
        classification = classify_email(client, e["subject"], e["sender"], e["body"])

        raw = classification.strip()

        # Split model output into decision + explanation
        decision = raw.split("-", 1)[0].strip().upper()
        reason = raw.split("-", 1)[1].strip() if "-" in raw else ""

        # Safety guard: skip unexpected model outputs
        if decision not in ["DELETE", "KEEP"]:
            print(f"⚠️ Unexpected model output: {classification}")
            print("          Skipping this email.\n")
            continue

        if decision == "DELETE":
            print(f"🗑️  DELETE | From: {e['sender'][:40]}")
            print(f"          | Subject: {e['subject'][:50]}")
            print(f"          | Reason: {reason}")

            # Normalize user input to avoid casing/whitespace issues
            print(f"          Delete this? (y/n): ", end="")
            answer = input().strip().lower()

            if answer in ["yes", "y"]:
                # Move email to Trash (not permanent deletion)
                status1, _ = mail.uid("store", e["id"], "-X-GM-LABELS", "\\Inbox")
                status2, _ = mail.uid("store", e["id"], "+X-GM-LABELS", "\\Trash")

                if status1 == "OK" and status2 == "OK":
                    deleted_count += 1
                    print(f"          ✅ Deleted!\n")
                else:
                    print(f"          ❌ Failed to delete\n")
            else:
                print(f"          ✅ Kept!\n")
        else:
            print(f"✅ KEEP   | From: {e['sender'][:40]}")
            print(f"          | Subject: {e['subject'][:50]}\n")
    
    print("-" * 60)
    print(f"\n📊 Done! {deleted_count} emails deleted.")
    
    mail.logout()


if __name__ == "__main__":
   run_email_agent()