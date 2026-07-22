"""
email_alerts.py

Sends citation/new-paper and CFP alert emails directly via SMTP (Gmail).
Replaces the old n8n webhook + AI Agent + Code node JSON-repair pipeline
entirely -- no external service, no webhook URL, nothing that can go
stale or silently stop firing.

One-time setup:
1. Enable 2-Step Verification on the Gmail account:
   https://myaccount.google.com/security
2. Create an App Password (choose "Mail"):
   https://myaccount.google.com/apppasswords
3. Add to your .env:
     GMAIL_ADDRESS=youraddress@gmail.com
     GMAIL_APP_PASSWORD=the16charcode
   (NOT your normal Gmail password.)

Optional: if OPENROUTER_API_KEY is set (same var app.py already uses),
an LLM writes a slightly warmer subject/body. If the key is missing, the
call fails, or the model's output can't be parsed, this ALWAYS falls
back to a plain templated email instead of losing the alert -- unlike
the old n8n flow, a malformed model response can never block delivery.
"""

import os
import json
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = "openrouter/free"

MAX_TITLES_PER_EMAIL = 10

# When True, a subscriber who tracks at least one professor but got NO
# new papers / citation alerts / CFP matches this run still gets a short
# "nothing new this week" email, so they know the system checked and
# everything's quiet rather than wondering if it ran at all.
# Flip to False any time to go back to only emailing when there's
# something to report -- this is the only line you need to change.
NOTIFY_ON_NO_UPDATES = True


def _send_email(to_address, subject, body):
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("FAILED: set GMAIL_ADDRESS / GMAIL_APP_PASSWORD in .env -- cannot send email.")
        return False

    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_address

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, [to_address], msg.as_string())
        print(f"SUCCESS: email sent to {to_address}")
        return True
    except smtplib.SMTPException as e:
        print(f"FAILED: could not send email to {to_address}: {e}")
        return False


def _template_citation_email(recipient_name, professor_entries):
    lines = [f"Hi {recipient_name or 'there'},", "", "Here's what's new this week:"]
    for entry in professor_entries:
        lines.append(f"\n{entry['professor_name']}:")
        new_papers = entry["new_papers"][:MAX_TITLES_PER_EMAIL]
        citation_alerts = entry["citation_alerts"][:MAX_TITLES_PER_EMAIL]
        if new_papers:
            lines.append("  New publications:")
            lines.extend(f"    - {t}" for t in new_papers)
            extra = len(entry["new_papers"]) - len(new_papers)
            if extra > 0:
                lines.append(f"    ...and {extra} more")
        if citation_alerts:
            lines.append("  Citation increases:")
            lines.extend(f"    - {t}" for t in citation_alerts)
            extra = len(entry["citation_alerts"]) - len(citation_alerts)
            if extra > 0:
                lines.append(f"    ...and {extra} more")
    lines.append("\nHave a good week.")
    return "Research Update: New Papers & Citations", "\n".join(lines)


def _template_cfp_email(recipient_name, professor_entries):
    lines = [f"Hi {recipient_name or 'there'},", "", "Upcoming calls for papers matching your tracked professors:"]
    for entry in professor_entries:
        lines.append(f"\n{entry['professor_name']}:")
        for c in entry["cfps"]:
            topics = ", ".join(c.get("matched_topics") or [])
            line = f"  - {c['venue']} — deadline {c['deadline']}"
            if topics:
                line += f" (matched: {topics})"
            lines.append(line)
    lines.append("\nHave a good week.")
    return "Upcoming CFP Matches", "\n".join(lines)


def _generate_with_llm(prompt_context, fallback_subject, fallback_body):
    """Best-effort LLM flourish. On ANY failure (missing key, network
    error, bad JSON) this returns the plain template instead -- an alert
    email is never dropped because a free model returned garbage."""
    if not OPENROUTER_API_KEY:
        return fallback_subject, fallback_body

    import requests

    system_message = (
        "You write short, warm, professional email alerts for a faculty "
        "research monitoring tool. Output ONLY JSON, nothing else: "
        '{"subject": "string, under 60 chars", "body": "string, use \\n for line breaks"}. '
        "No markdown fences, no commentary before or after. Under 150 words. "
        "Do not invent details that aren't in the data provided."
    )

    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": json.dumps(prompt_context)},
                ],
            },
            timeout=20,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        return parsed.get("subject") or fallback_subject, parsed.get("body") or fallback_body
    except Exception as e:
        print(f"WARNING: LLM email flourish failed, using plain template instead: {e}")
        return fallback_subject, fallback_body


def send_no_update_emails(subscriber_emails, names):
    """subscriber_emails: emails of users who track at least one
    professor but had nothing to report this run (no new papers, no
    citation alerts, no CFP matches). Does nothing if
    NOTIFY_ON_NO_UPDATES is False -- flip that flag off any time to
    disable this without touching code.py."""
    if not NOTIFY_ON_NO_UPDATES:
        return
    if not subscriber_emails:
        return

    subject = "Research Update: Nothing New This Week"
    for email in subscriber_emails:
        recipient_name = names.get(email, "")
        body = (
            f"Hi {recipient_name or 'there'},\n\n"
            "Checked in on the professors you're tracking this week -- "
            "no new papers, citation increases, or matching CFPs to report.\n\n"
            "Have a good week."
        )
        _send_email(email, subject, body)


def send_citation_update_emails(user_citation_bundles, names):
    """user_citation_bundles: {email: [{"professor_name", "new_papers", "citation_alerts"}, ...]}
    names: {email: name} -- e.g. from auth.get_all_subscribers()."""
    if not user_citation_bundles:
        print("No citation/new-paper updates for any subscriber this run.")
        return

    for email, professor_entries in user_citation_bundles.items():
        recipient_name = names.get(email, "")
        fallback_subject, fallback_body = _template_citation_email(recipient_name, professor_entries)
        subject, body = _generate_with_llm(
            {"alert_type": "citation_update", "recipient_name": recipient_name, "professors": professor_entries},
            fallback_subject, fallback_body,
        )
        _send_email(email, subject, body)


def send_cfp_alert_emails(user_cfp_bundles, names):
    """user_cfp_bundles: {email: [{"professor_name", "cfps"}, ...]}"""
    if not user_cfp_bundles:
        print("No matching CFPs for any subscriber this run.")
        return

    for email, professor_entries in user_cfp_bundles.items():
        recipient_name = names.get(email, "")
        fallback_subject, fallback_body = _template_cfp_email(recipient_name, professor_entries)
        subject, body = _generate_with_llm(
            {"alert_type": "cfp_alert", "recipient_name": recipient_name, "professors": professor_entries},
            fallback_subject, fallback_body,
        )
        _send_email(email, subject, body)