"""
cfp_alerts.py

Module 4 — CFP (Call for Papers) Alerts.

There's no clean free API for conference/journal CFPs, so this keeps a
small curated venue list (data/cfp_venues.json) with topic keywords and
submission deadlines. Each run:

  1. Loads the venue list.
  2. Matches each venue's keywords against the researcher's
     research_interests (pulled from professor.json's merged profile).
  3. Flags venues whose deadline falls within CFP_ALERT_WINDOW_DAYS.
  4. Skips venues already alerted on (tracked in
     data/cfp_alerts_sent.json) so subscribers don't get the same
     CFP email every run.
  5. Sends one webhook POST per subscriber to the SAME n8n webhook
     used for paper/citation alerts, tagged "alert_type": "cfp_alert"
     so the n8n workflow can branch to a different AI Agent prompt.

Call trigger_cfp_alerts() from code.py's main(), any time after the
merged profile has been saved (it reads research_interests from
professor.json).

Edit data/cfp_venues.json with real venues and deadlines relevant to
the researcher's field — the seeded file has placeholder examples.
"""

import json
import os
import datetime
import requests
from auth import get_all_subscribers

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENUES_PATH = os.path.join(_BASE_DIR, "data", "cfp_venues.json")
SENT_LOG_PATH = os.path.join(_BASE_DIR, "data", "cfp_alerts_sent.json")
DATA_PATH = os.path.join(_BASE_DIR, "data", "professor.json")

# Same webhook as n8n_alerts.py — the n8n workflow branches on alert_type.
N8N_WEBHOOK_URL = "https://subhanazhar312.app.n8n.cloud/webhook/citation-alerts"
CFP_ALERT_WINDOW_DAYS = 60


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _load_research_interests():
    data = _load_json(DATA_PATH, {})
    return data.get("profile", {}).get("research_interests") or []


def find_matching_venues(research_interests, venues):
    interests = [i.lower() for i in research_interests]
    matches = []
    for venue in venues:
        keywords = [k.lower() for k in venue.get("keywords", [])]
        if any(kw in interest or interest in kw for kw in keywords for interest in interests):
            matches.append(venue)
    return matches


def find_upcoming_deadlines(venues, window_days=CFP_ALERT_WINDOW_DAYS):
    today = datetime.date.today()
    upcoming = []
    for venue in venues:
        try:
            deadline = datetime.date.fromisoformat(venue["deadline"])
        except (KeyError, ValueError):
            continue
        days_left = (deadline - today).days
        if 0 <= days_left <= window_days:
            venue = dict(venue)
            venue["days_left"] = days_left
            upcoming.append(venue)
    return upcoming


def trigger_cfp_alerts():
    venues = _load_json(VENUES_PATH, [])
    if not venues:
        print("No CFP venues configured (data/cfp_venues.json) — skipping CFP check.")
        return

    research_interests = _load_research_interests()
    matched = find_matching_venues(research_interests, venues)
    upcoming = find_upcoming_deadlines(matched)

    already_sent = _load_json(SENT_LOG_PATH, [])
    new_alerts = [v for v in upcoming if v["name"] not in already_sent]

    if not new_alerts:
        print("No new CFP alerts to send.")
        return

    subscribers = get_all_subscribers()
    if not subscribers:
        print("No subscribers registered — skipping CFP alert.")
        return

    cfp_matches = [
        {
            "venue": v["name"],
            "deadline": v["deadline"],
            "days_left": v["days_left"],
            "link": v.get("link", ""),
            "matched_topic": (v.get("keywords") or [""])[0],
        }
        for v in new_alerts
    ]

    for subscriber in subscribers:
        payload = {
            "recipients": subscriber["email"],
            "recipient_name": subscriber["name"],
            "alert_type": "cfp_alert",
            "cfp_matches": cfp_matches,
        }
        try:
            response = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=15)
            response.raise_for_status()
            print(f"CFP alert webhook triggered for {subscriber['email']}")
        except requests.RequestException as e:
            print(f"Failed to trigger CFP alert webhook for {subscriber['email']}: {e}")

    _save_json(SENT_LOG_PATH, already_sent + [v["name"] for v in new_alerts])