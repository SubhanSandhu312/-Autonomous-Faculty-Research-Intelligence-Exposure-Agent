"""
n8n_alerts.py

Two trigger functions, both posting to the SAME n8n webhook, distinguished
by an "alert_type" field in the payload so one n8n workflow (Webhook -> AI
Agent -> Gmail) can handle both. Both payloads include "recipient_name"
alongside "recipients" so the email can be addressed by name.

1. trigger_n8n_alerts(all_notifications)
   {
     "alert_type": "citation_update",
     "recipients": "someone@example.com",
     "recipient_name": "Someone",
     "new_paper_count": 2,       # true total
     "citation_alert_count": 1,  # true total
     "new_papers": ["Title A", "Title B"],       # capped, see MAX_TITLES_PER_EMAIL
     "citation_alerts": ["Title C"]              # capped
   }

2. trigger_cfp_alerts(matched_cfps)
   {
     "alert_type": "cfp_alert",
     "recipients": "someone@example.com",
     "recipient_name": "Someone",
     "cfp_count": 2,
     "cfps": [
       {"venue": "CVPR", "deadline": "2026-11-15", "matched_topics": ["computer vision"]},
       ...
     ]
   }
"""

import requests
from auth import get_all_subscribers

N8N_WEBHOOK_URL = "https://subhanazhar312.app.n8n.cloud/webhook-test/citation-alerts"

# Caps how many titles get sent per email. Without this, a first run (or
# any run that finds a large batch of papers) sends dozens/hundreds of
# titles to the AI Agent, which either blows past max_tokens mid-response
# (breaking the JSON) or produces an unreadably long email.
MAX_TITLES_PER_EMAIL = 10


def trigger_n8n_alerts(all_notifications):
    new_papers = [n["title"] for n in all_notifications if n["type"] == "new_paper"]
    citation_alerts = [n["title"] for n in all_notifications if n["type"] == "citation_alert"]

    if not new_papers and not citation_alerts:
        print("No new papers or citation alerts — skipping n8n trigger.")
        return

    subscribers = get_all_subscribers()
    if not subscribers:
        print("No subscribers registered — skipping n8n trigger.")
        return

    new_papers_capped = new_papers[:MAX_TITLES_PER_EMAIL]
    citation_alerts_capped = citation_alerts[:MAX_TITLES_PER_EMAIL]

    for subscriber in subscribers:
        payload = {
            "alert_type": "citation_update",
            "recipients": subscriber["email"],
            "recipient_name": subscriber["name"],
            "new_paper_count": len(new_papers),
            "citation_alert_count": len(citation_alerts),
            "new_papers": new_papers_capped,
            "citation_alerts": citation_alerts_capped,
        }
        try:
            response = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=15)
            response.raise_for_status()
            print(f"n8n citation-update webhook triggered for {subscriber['email']}")
        except requests.RequestException as e:
            print(f"Failed to trigger n8n webhook for {subscriber['email']}: {e}")


def trigger_cfp_alerts(matched_cfps):
    if not matched_cfps:
        print("No matching CFPs found — skipping n8n CFP trigger.")
        return

    subscribers = get_all_subscribers()
    if not subscribers:
        print("No subscribers registered — skipping n8n CFP trigger.")
        return

    cfps_payload = [
        {
            "venue": c["venue"],
            "deadline": c["deadline"],
            "matched_topics": c["matched_topics"],
        }
        for c in matched_cfps
    ]

    for subscriber in subscribers:
        payload = {
            "alert_type": "cfp_alert",
            "recipients": subscriber["email"],
            "recipient_name": subscriber["name"],
            "cfp_count": len(cfps_payload),
            "cfps": cfps_payload,
        }
        try:
            response = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=15)
            response.raise_for_status()
            print(f"n8n CFP-alert webhook triggered for {subscriber['email']}")
        except requests.RequestException as e:
            print(f"Failed to trigger n8n CFP webhook for {subscriber['email']}: {e}")