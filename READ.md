# Email Cleanup Agent

## The Story
My mother-in-law was facing Yahoo's new 15 GB storage limit (deadline: 5th May) 
with a 32 GB inbox containing 32,000 emails. Manually reviewing each one was 
exhausting and time-consuming — so I built her an AI agent to do it.

Along the way, I discovered that Gmail and Yahoo handle email deletion differently,
which led me to build two separate agents.

---

## How Gmail vs Yahoo handle deletion differently

**Gmail** stores all emails in one place and uses labels to organise them.
"Deleting" an email means removing the Inbox label and adding the Trash label —
essentially a relabelling task.

**Yahoo** keeps emails in separate folders. Deleting means copying the email from 
Inbox to Trash, flagging it as Deleted, then expunging all Deleted-flagged emails.

---

## Project Structure

```
Email Agent/
├── Gmail/
│   ├── Simple Agent/
│   │   └── simple_gmail_agent.py
│   └── Complex Agent/
│       └── complex_gmail_agent.py
├── Yahoo/
│   ├── Simple Agent/
│   │   └── simple_yahoo_agent.py
│   └── Complex Agent/
│       └── complex_yahoo_agent.py
└── .gitignore
```

---

## Agent Versions

### Simple Version
- Retrieves emails within a specified date range, capped at `max_emails = 20` 
  (configurable) to prevent excessive API usage
- Each email is analysed individually by Claude using its sender, subject, and 
  the first 300 characters of the plain-text body
- Makes one API call per email
- For emails marked DELETE, asks for user confirmation before moving to Trash
  (emails in Trash are automatically removed after 30 days)
- KEEP emails are skipped without any action
- At the end, displays the total number of deleted emails
- Best for small inboxes or cautious users

### Complex Version
- Retrieves emails within a specified date range, processing them in a streaming 
  (lazy) fashion — yielding one email at a time instead of loading all into memory
- Groups emails into batches (default: 20, configurable) and makes one single API 
  call per batch — significantly reducing cost and latency
- The model returns a structured JSON response containing for each email:
  - **decision**: DELETE or KEEP
  - **confidence**: 0-100, how certain the model is about its decision
  - **reason**: a short explanation of the decision
- Confidence-driven deletion:
  - **≥ 90%** → auto-deleted (very obvious spam/promotional)
  - **< 90%** → asks for user confirmation (ambiguous emails)
  - Lower the threshold to 85 or 80 in the code for more aggressive auto-deletion
- After each batch, a progress summary is printed. At the end, the total number 
  of deleted and auto-deleted emails is shown
- Best for large inboxes

---

## Setup

### 1. Clone the repo
```bash
git clone https://github.com/andra-e28/mom-s-email-agent.git
cd mom-s-email-agent
```

### 2. Create and activate virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies
```bash
pip install anthropic python-dotenv
```

### 4. Create a `.env` file in the root of the project
```
ANTHROPIC_API_KEY=your_key_here

# Gmail
GMAIL_ADDRESS=your@gmail.com
GMAIL_APP_PASSWORD=your_app_password

# Yahoo
YAHOO_ADDRESS=your@yahoo.com
YAHOO_APP_PASSWORD=your_app_password
YAHOO_TRASH=Trash
```

### 5. Get your App Password

**Important:** First make sure your email account language is set to **English**, 
otherwise folder names (like Trash) may be in a different language and the script 
won't find them.

#### Gmail:
1. Go to [myaccount.google.com](https://myaccount.google.com)
2. Security → **2-Step Verification** → Turn on (required)
3. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
4. App name: `Email Agent` → Click **Create**
5. Copy the 16-character password (remove spaces before pasting into `.env`)

#### Yahoo:
1. Go to [login.yahoo.com](https://login.yahoo.com)
2. Security → **2-Step Verification** → Turn on (required)
3. Click your profile → **Account Security**
4. Scroll to **App passwords** → Generate
5. App name: `Email Agent` → Copy the password

---

## Usage

Open the agent file of your choice and set your date range near the bottom:
```python
start_date = datetime(2026, 4, 1)   # inclusive
end_date = datetime(2026, 4, 30)    # exclusive
```

To adjust the auto-deletion threshold in the complex version (default: 90):
```python
if confidence >= 90:   # lower to 85 or 80 for more aggressive auto-deletion
```

Then run from terminal:
```bash
python3 complex_gmail_agent.py
# or
python3 complex_yahoo_agent.py
```

---

## Important Note
This agent reads the first 300 characters of each email's body. If your inbox 
contains emails with sensitive information (passwords, banking details), adjust 
the date range to exclude those emails before running. Alternatively, manually 
remove those emails from your inbox first.

---

## Built with
- [Anthropic Claude API](https://anthropic.com) — email classification (claude-haiku-4-5)
- [ChatGPT](https://chatgpt.com) (GPT-5.3 Instant, free version) — assisted with development
- [imaplib](https://docs.python.org/3/library/imaplib.html) — IMAP email access over SSL
- Inspired by Andrew Ng's [Agentic AI Workflows](https://learn.deeplearning.ai) course
