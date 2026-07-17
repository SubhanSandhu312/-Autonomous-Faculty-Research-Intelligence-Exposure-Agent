"""
n8n_alerts.py

Call trigger_n8n_alerts(all_notifications) from code.py's main(), right
after all_notifications has been fully assembled (after the scholarly
fallback block, before or after saving to professor.json).

all_notifications is the same list code.py already builds — a list of
dicts shaped like:
  {"type": "new_paper", "title": "...", ...}
  {"type": "citation_alert", "title": "...", ...}

Sends one webhook POST per subscriber, matching this JSON shape:

{
  "recipients": "someone@example.com",
  "new_paper_count": 2,
  "citation_alert_count": 1,
  "new_papers": ["Title A", "Title B"],
  "citation_alerts": ["Title C"]
}
"""

import requests
from auth import get_all_subscriber_emails

N8N_WEBHOOK_URL = "https://subhanazhar312.app.n8n.cloud/webhook-test/citation-alerts"


def trigger_n8n_alerts(all_notifications):
    new_papers = [n["title"] for n in all_notifications if n["type"] == "new_paper"]
    citation_alerts = [n["title"] for n in all_notifications if n["type"] == "citation_alert"]

    if not new_papers and not citation_alerts:
        print("No new papers or citation alerts — skipping n8n trigger.")
        return

    recipients = get_all_subscriber_emails()
    if not recipients:
        print("No subscribers registered — skipping n8n trigger.")
        return

    for email in recipients:
        payload = {
            "recipients": email,
            "new_paper_count": len(new_papers),
            "citation_alert_count": len(citation_alerts),
            "new_papers": new_papers,
            "citation_alerts": citation_alerts,
        }
        try:
            response = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=15)
            response.raise_for_status()
            print(f"n8n webhook triggered for {email}")
        except requests.RequestException as e:
            print(f"Failed to trigger n8n webhook for {email}: {e}")