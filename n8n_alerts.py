"""
n8n_alerts.py

Both trigger functions now take PER-USER bundles (built by code.py's
main(), which aggregates across every professor a user tracks) rather than
querying subscribers themselves -- a user following 3 professors gets ONE
combined email per alert type, not three.

Both post to the SAME n8n webhook, distinguished by "alert_type", exactly
as before -- just with a "professors" array instead of flat fields, since
one email can now cover multiple professors.

1. trigger_n8n_alerts(user_citation_bundles)
   user_citation_bundles: {user_email: [{"professor_name", "new_papers", "citation_alerts"}, ...]}
   Payload sent per user:
   {
     "alert_type": "citation_update",
     "recipients": "someone@example.com",
     "recipient_name": "Someone",
     "professors": [
       {"professor_name": "Prof A", "new_paper_count": 5, "citation_alert_count": 1,
        "new_papers": [...up to 10...], "citation_alerts": [...up to 10...]},
       {"professor_name": "Prof B", "new_paper_count": 2, "citation_alert_count": 0,
        "new_papers": [...], "citation_alerts": []}
     ]
   }

2. trigger_cfp_alerts(user_cfp_bundles)
   user_cfp_bundles: {user_email: [{"professor_name", "cfps"}, ...]}
   Payload sent per user:
   {
     "alert_type": "cfp_alert",
     "recipients": "someone@example.com",
     "recipient_name": "Someone",
     "professors": [
       {"professor_name": "Prof A", "cfp_count": 2, "cfps": [{"venue":..,"deadline":..,"matched_topics":[..]}, ...]}
     ]
   }
"""

import requests
from auth import get_all_subscribers

N8N_WEBHOOK_URL = "https://subhanazhar312.app.n8n.cloud/webhook-test/citation-alerts"

# Caps how many titles get sent per professor per email -- protects
# against a huge first-run batch (or a prolific professor) blowing past
# the AI Agent's max_tokens budget and producing invalid JSON.
MAX_TITLES_PER_EMAIL = 10


def _name_lookup():
    return {s["email"]: s["name"] for s in get_all_subscribers()}


def trigger_n8n_alerts(user_citation_bundles):
    if not user_citation_bundles:
        print("No citation/new-paper updates for any subscriber this run.")
        return

    names = _name_lookup()

    for email, professor_entries in user_citation_bundles.items():
        professors_payload = []
        for entry in professor_entries:
            new_papers = entry["new_papers"][:MAX_TITLES_PER_EMAIL]
            citation_alerts = entry["citation_alerts"][:MAX_TITLES_PER_EMAIL]
            professors_payload.append({
                "professor_name": entry["professor_name"],
                "new_paper_count": len(entry["new_papers"]),
                "citation_alert_count": len(entry["citation_alerts"]),
                "new_papers": new_papers,
                "citation_alerts": citation_alerts,
            })

        payload = {
            "alert_type": "citation_update",
            "recipients": email,
            "recipient_name": names.get(email, ""),
            "professors": professors_payload,
        }
        try:
            response = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=15)
            response.raise_for_status()
            print(f"n8n citation-update webhook triggered for {email} ({len(professors_payload)} professor(s))")
        except requests.RequestException as e:
            print(f"Failed to trigger n8n webhook for {email}: {e}")


def trigger_cfp_alerts(user_cfp_bundles):
    if not user_cfp_bundles:
        print("No matching CFPs for any subscriber this run.")
        return

    names = _name_lookup()

    for email, professor_entries in user_cfp_bundles.items():
        professors_payload = []
        for entry in professor_entries:
            cfps = [
                {"venue": c["venue"], "deadline": c["deadline"], "matched_topics": c["matched_topics"]}
                for c in entry["cfps"]
            ]
            professors_payload.append({
                "professor_name": entry["professor_name"],
                "cfp_count": len(cfps),
                "cfps": cfps,
            })

        payload = {
            "alert_type": "cfp_alert",
            "recipients": email,
            "recipient_name": names.get(email, ""),
            "professors": professors_payload,
        }
        try:
            response = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=15)
            response.raise_for_status()
            print(f"n8n CFP-alert webhook triggered for {email} ({len(professors_payload)} professor(s))")
        except requests.RequestException as e:
            print(f"Failed to trigger n8n CFP webhook for {email}: {e}")