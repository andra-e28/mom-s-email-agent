"""
    Gmail Agent - complex version:
    Batch-Based Email Classifier with Confidence-Driven Deletion

    
    This agent retrieves emails within a specified date range (["start", "end")),
    processing them in a streaming (lazy) fashion. Instead of loading all emails 
    at once, it yields one email at a time as it is requested. 

    Emails are grouped into batches (default size: 20, configurable). For each 
    batch, the agent makes a single LLM API call, significantly reducing cost and
    latency compared to the per-email classification from the simpler version of 
    the email agent.

    The model returns a structured response in JSON format, containing the email's
    index, the decision, a confidence score and the reason. Thus, each email is 
    assigned a decision of either “DELETE” or “KEEP”, a confidence score ranging 
    from 0 to 100 indicating how certain the model is about its choice, as well as
    a short explanation describing the reasoning behind the decision.

    This JSON response is then parsed by the program so that the results can be 
    used automatically in later steps.

    When it comes to deletion, the system uses the confidence score as a threshold.
    If the confidence is greater than or equal to 90 (default value, configurable),
    the email is automatically moved to the Trash without requiring user input. 
    If the confidence is below 90, the system asks the user to manually confirm 
    whether the email should be deleted. Any emails that are not selected for 
    deletion are kept in the inbox.

    After each batch is processed, a summary of the number of deleted and auto-
    deleted emails in that batch is printed. At the end of execution, the total 
    number of deleted and auto-deleted emails is printed. 

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
import json
import sys

load_dotenv()


# Connect to Gmail using IMAP over SSL (secure sockets layer - encrypted, secure 
# connection between my code and Gmail)
def connect_to_gmail():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(os.getenv("GMAIL_ADDRESS"), os.getenv("GMAIL_APP_PASSWORD"))  # uses app password, not normal password
    return mail


# Extracts readable plain-text body from email
# Falls back safely if decoding fails or content is not clean
def get_email_body(msg):
    if msg.is_multipart():
        # Walk through all parts (emails can be nested)
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode()
                except:
                    return ""  # fail silently if decoding breaks
    else:
        try:
            return msg.get_payload(decode=True).decode()
        except:
            return ""
    return ""


# Generator function: streams emails one by one instead of loading all into memory
def fetch_emails(mail, start_date, end_date):
    mail.select("inbox")  # select inbox folder

    # Convert Python datetime → IMAP-compatible format (e.g., 28-Apr-2026)
    start = start_date.strftime("%d-%b-%Y")
    end = end_date.strftime("%d-%b-%Y")

    # IMAP query: SINCE is inclusive, BEFORE is exclusive → [start, end)
    query = f'SINCE "{start}" BEFORE "{end}"'

    _, messages = mail.uid("search", None, query)

    # Defensive check in case mailbox is empty or query fails
    email_ids = messages[0].split() if messages and messages[0] else []

    # Reverse to process newest emails first
    email_ids = email_ids[::-1]
    
    for email_id in email_ids:
        _, msg_data = mail.uid("fetch", email_id, "(RFC822)")
        
        # Skip malformed responses 
        if not msg_data or not msg_data[0]:
            continue
        
        msg = email.message_from_bytes(msg_data[0][1])
        
        # Decode subject safely (handles encoded headers like =?UTF-8?...)
        subject_parts = decode_header(msg["Subject"])
        subject = ""
        for part, encoding in subject_parts:
            if isinstance(part, bytes):
                subject += part.decode(encoding or "utf-8", errors="ignore")
            else:
                subject += part

        sender = msg["From"]
        body = get_email_body(msg)

        # Yield instead of returning list → memory efficient "streaming"
        yield {
            "id": email_id,
            "subject": subject,
            "sender": sender,
            "body": body[:300],  # truncate to reduce token usage
        }


# Builds a single prompt containing multiple emails → 1 API call per batch
def classify_emails_batch(client, emails):
    email_list = ""
    for i, e in enumerate(emails):
        # Flatten emails into a structured text block for the model
        email_list += f"""
Email {i}:
Sender: {e['sender']}
Subject: {e['subject']}
Body: {e['body'][:300]}
---"""

    prompt = f"""Classify each email as DELETE or KEEP.

DELETE if it is any of these:
- Promotional or sale emails
- Newsletters
- Social media notifications (Facebook, Instagram, Twitter, LinkedIn)
- Google security alerts (sign-in notifications, new device alerts)
- Payment processing notifications ("your payment is being processed")
- Shipping updates ("your order is on its way", "out for delivery")
- App notifications (Just Eat, Uber, etc.)
- "Welcome to..." onboarding emails
- Survey or feedback requests
- Subscription renewal reminders
- Travel updates (status changes, reminders, check-in prompts)

KEEP if it is any of these:
- Order confirmations with order numbers
- Payment receipts or proof of payment
- Invoices
- Bank statements
- Important personal emails
- Work related emails
- Legal or official documents
- Account creation confirmations
- Travel bookings (tickets, reservations)

{email_list}

For each email provide:
- "index": the email number from above
- "decision": DELETE or KEEP
- "confidence": 0-100, how certain you are about the decision (90+ means very 
obvious, 70-89 means likely but some ambiguity, below 70 means genuinely uncertain)
- "reason": one short phrase explaining why

- You MUST return exactly one result per email
- The number of results MUST match the number of emails
- Each index must appear exactly once
- Indices must be in ascending order starting from 0

Respond ONLY with a JSON array, no other text. Example:
[
  {{"index": 0, "decision": "DELETE", "confidence": 95, "reason": "promotional email"}},
  {{"index": 1, "decision": "KEEP", "confidence": 88, "reason": "order confirmation"}}
]
"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    # Extract raw text response from model
    text = response.content[0].text.strip()

    # Attempt to isolate JSON array (guards against extra text / hallucinations)
    start = text.find("[")
    end = text.rfind("]") + 1

    if start == -1 or end == 0:
        print("⚠️ Failed to parse model response, skipping this batch")
        return []

    # Convert JSON string → Python list of dicts
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        print("⚠️ JSON parsing failed, skipping this batch")
        return []


def process_results(batch, results, mail, counters):
    print("-" * 60)

    if not results:
        print("⚠️ Empty or invalid classification result for this batch")
        return
    
    # Local counters (per batch)
    batch_counters = {
        "deleted": 0,
        "auto_deleted": 0
    }
    
    for result in results:
        # Protect against bad indices returned by model
        idx = result.get("index")
        if idx is None or idx >= len(batch):
            continue

        e = batch[idx]
        decision = result.get("decision", "KEEP")  # default to safe option
        confidence = result.get("confidence", 0)
        reason = result.get("reason", "")
        
        if decision == "DELETE":
            print(f"🗑️  DELETE | From: {e['sender'][:40]}")
            print(f"          | Subject: {e['subject'][:50]}")
            print(f"          | Reason: {reason} (confidence of decision: {confidence}%)")
            
            # Auto-delete only when model is highly confident
            # Set HERE the confidence threshold to 85 or 80 for more aggressive 
            # auto-deletion
            if confidence >= 90:
                # Gmail "delete" = remove Inbox label + add Trash label
                status1, _ = mail.uid("store", e["id"], "-X-GM-LABELS", "\\Inbox")
                status2, _ = mail.uid("store", e["id"], "+X-GM-LABELS", "\\Trash")

                if status1 == "OK" and status2 == "OK":
                    batch_counters["deleted"] += 1
                    batch_counters["auto_deleted"] += 1
                    print(f"          🤖 Auto-deleted! (confidence of decision ≥ 90%)\n")
            else:
                # Human-in-the-loop safeguard for lower confidence
                print(f"          Delete this? (y/n): ", end="")
                answer = input()

                if answer.lower() in ["yes", "y"]:
                    status1, _ = mail.uid("store", e["id"], "-X-GM-LABELS", "\\Inbox")
                    status2, _ = mail.uid("store", e["id"], "+X-GM-LABELS", "\\Trash")

                    if status1 == "OK" and status2 == "OK":
                        batch_counters["deleted"] += 1
                        print(f"          ✅ Deleted!\n")
                else:
                    print(f"          ✅ Kept!\n")
        else:
            # KEEP path (no action taken)
            print(f"✅ KEEP   | From: {e['sender'][:40]}")
            print(f"          | Subject: {e['subject'][:50]} (confidence of decision: {confidence}%)\n")
    
    # Aggregate batch results into global counters
    counters["deleted"] += batch_counters["deleted"]
    counters["auto_deleted"] += batch_counters["auto_deleted"]

    print("-" * 60)
    print(f"\n📊 Progress: {batch_counters['deleted']} deleted (of which {batch_counters['auto_deleted']} auto-deleted)...")


def run_emails_batch_agent():
    print("🔌 Connecting to Gmail...")
    mail = connect_to_gmail()
    print("✅ Connected!\n")
    print(f"📬 Fetching emails...\n")

    client = anthropic.Anthropic()

    # Define 👇HERE the cleanup window (inclusive start, exclusive end)
    start_date = datetime(2026, 4, 28)
    end_date = datetime(2026, 4, 30)

    batch = []

    # Global counters across all batches
    counters = {
        "deleted": 0,
        "auto_deleted": 0
    }

    for e in fetch_emails(mail, start_date, end_date):
        batch.append(e)

        # Process batch once it reaches target size
        # Set HERE the target size (currently set to 20)
        if len(batch) == 20:
            results = classify_emails_batch(client, batch)
            process_results(batch, results, mail, counters)
            batch = []  # clear batch
    
    # Process remaining emails (if total not divisible by batch size)
    if batch:
        results = classify_emails_batch(client, batch)
        process_results(batch, results, mail, counters)
    
    print(f"\n📊 Done! {counters['deleted']} emails deleted (of which {counters['auto_deleted']} auto-deleted).")
    
    mail.logout()  # always close connection cleanly


if __name__ == "__main__":
    run_emails_batch_agent()