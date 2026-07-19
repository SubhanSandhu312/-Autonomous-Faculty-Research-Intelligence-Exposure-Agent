"""
cfp_alerts.py

Module 4 -- CFP (Call for Papers) matching.

WHY THIS CHANGED:
code.py imports `get_matched_cfps` from this module, but the previous
version of this file only exposed `trigger_cfp_alerts()` (which sent its
own webhook per subscriber, read a single hardcoded data/professor.json,
and duplicated the sending logic that n8n_alerts.py now handles with
proper per-user bundling across multiple professors). That mismatch was
the ImportError.

Rather than inventing a function with made-up behavior, this reconciles
the two files along the lines the real architecture already uses:

  - n8n_alerts.py now owns ALL webhook sending (one combined email per
    user per run, across every professor they track). This file no
    longer sends anything or knows about subscribers/webhooks.
  - This file now owns MATCHING only: given one professor's profile
    (for research_interests) and papers, return the CFPs relevant to
    THAT professor with an upcoming deadline. code.py calls this once
    per professor inside its main() loop and hands the result to
    n8n_alerts.trigger_cfp_alerts() for bundling/sending.

The original matching logic (find_matching_venues / find_upcoming_deadlines)
is unchanged -- only the single-professor DATA_PATH reader and the
webhook-sending code were removed, since both are superseded.

Edit data/cfp_venues.json with real venues and deadlines relevant to your
researchers' fields -- the seeded file has placeholder examples.
"""

import json
import os
import datetime

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENUES_PATH = os.path.join(_BASE_DIR, "data", "cfp_venues.json")
SENT_LOG_PATH = os.path.join(_BASE_DIR, "data", "cfp_alerts_sent.json")

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


def _matched_topics(research_interests, venue):
    """Which of the venue's keywords actually matched this professor's
    interests -- used for the 'matched_topics' list n8n_alerts.py's
    payload expects per CFP entry."""
    interests_lower = [i.lower() for i in research_interests]
    matched = []
    for kw in venue.get("keywords", []):
        kw_lower = kw.lower()
        if any(kw_lower in interest or interest in kw_lower for interest in interests_lower):
            matched.append(kw)
    return matched


def get_upcoming_cfp_matches(profile, window_days=CFP_ALERT_WINDOW_DAYS):
    """Read-only version of the matching, meant for the Streamlit UI.

    Returns EVERY currently-matching, currently-upcoming CFP for this
    professor's research_interests -- regardless of whether an alert
    email has already gone out for it. (That de-dup only applies to
    get_matched_cfps() below, which is for outbound emails; the UI should
    keep showing a venue with a live deadline even after the one-time
    email alert has already fired.)

        [{"venue": ..., "deadline": ..., "days_left": ..., "link": ...,
          "matched_topics": [...]}, ...]
    """
    venues = _load_json(VENUES_PATH, [])
    if not venues:
        return []

    research_interests = (profile or {}).get("research_interests") or []
    if not research_interests:
        return []

    matched = find_matching_venues(research_interests, venues)
    upcoming = find_upcoming_deadlines(matched, window_days)

    return [
        {
            "venue": v["name"],
            "deadline": v["deadline"],
            "days_left": v["days_left"],
            "link": v.get("link", ""),
            "matched_topics": _matched_topics(research_interests, v),
        }
        for v in upcoming
    ]


def get_matched_cfps(profile, papers=None):
    """Called once per professor, per run, from code.py's main() loop.

    profile: that professor's merged profile dict (has research_interests).
    papers: that professor's papers -- accepted for future use (e.g.
        matching venue keywords against paper titles/abstracts too), not
        currently used for matching.

    Returns only the NEWLY-relevant CFPs for THIS professor (i.e. the ones
    that haven't already triggered an email) -- this is the function
    code.py uses to decide what goes in this run's alert email.

    De-duplication (data/cfp_alerts_sent.json) is keyed on
    "<professor_name>::<venue_name>", so the same venue can still surface
    separately for a different professor, but won't repeat for the same
    professor on the next run.
    """
    all_matches = get_upcoming_cfp_matches(profile)
    if not all_matches:
        return []

    professor_name = (profile or {}).get("name") or "unknown"
    already_sent = _load_json(SENT_LOG_PATH, [])
    already_sent_set = set(already_sent)

    new_alerts = []
    newly_sent_keys = []
    for match in all_matches:
        key = f"{professor_name}::{match['venue']}"
        if key in already_sent_set:
            continue
        new_alerts.append(match)
        newly_sent_keys.append(key)

    if newly_sent_keys:
        _save_json(SENT_LOG_PATH, already_sent + newly_sent_keys)

    return new_alerts