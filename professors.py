"""
professors.py

Manages two things:
1. A global professor registry -- every professor ANY user has ever added
   (data/professors.json). Two users adding the same professor (matched by
   Google Scholar URL) share one registry entry, so code.py only fetches
   and processes each unique professor once per run, no matter how many
   users track them.
2. Per-user subscriptions -- which professors each user has chosen to
   track (data/user_professors.json).

Each professor also gets its own data directory
(data/professors/<professor_id>/) holding that professor's own
professor.json system-of-record, completely separate from every other
professor's notification/citation history.
"""

import json
import os
import hashlib
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFESSORS_PATH = os.path.join(BASE_DIR, "data", "professors.json")
USER_PROFESSORS_PATH = os.path.join(BASE_DIR, "data", "user_professors.json")


def _load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def make_professor_id(name, scholar_url=""):
    """Deterministic ID from the Scholar URL (falls back to name if no URL
    given), so re-adding the same professor under a different account
    reuses the same registry entry and fetched data instead of duplicating
    it."""
    basis = (scholar_url or name).strip().lower()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def add_professor(name, scholar_url=""):
    """Adds a professor to the global registry if not already present
    (matched by Scholar URL/name). Returns the professor_id either way."""
    professors = _load_json(PROFESSORS_PATH)
    professor_id = make_professor_id(name, scholar_url)

    if professor_id not in professors:
        professors[professor_id] = {
            "name": name.strip(),
            "scholar_url": (scholar_url or "").strip(),
            "added_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        _save_json(PROFESSORS_PATH, professors)

    return professor_id


def get_all_professors():
    """[{"id": "...", "name": "...", "scholar_url": "...", "added_at": "..."}, ...]"""
    professors = _load_json(PROFESSORS_PATH)
    return [{"id": pid, **info} for pid, info in professors.items()]


def get_professor(professor_id):
    professors = _load_json(PROFESSORS_PATH)
    info = professors.get(professor_id)
    return {"id": professor_id, **info} if info else None


def get_professor_data_dir(professor_id):
    """Each professor's own data/professors/<id>/ folder for its
    professor.json system-of-record."""
    path = os.path.join(BASE_DIR, "data", "professors", professor_id)
    os.makedirs(path, exist_ok=True)
    return path


def subscribe(user_email, professor_id):
    subs = _load_json(USER_PROFESSORS_PATH)
    user_email = user_email.strip().lower()
    ids = subs.setdefault(user_email, [])
    if professor_id not in ids:
        ids.append(professor_id)
        _save_json(USER_PROFESSORS_PATH, subs)


def unsubscribe(user_email, professor_id):
    subs = _load_json(USER_PROFESSORS_PATH)
    user_email = user_email.strip().lower()
    if user_email in subs and professor_id in subs[user_email]:
        subs[user_email].remove(professor_id)
        _save_json(USER_PROFESSORS_PATH, subs)


def get_user_professor_ids(user_email):
    subs = _load_json(USER_PROFESSORS_PATH)
    return subs.get(user_email.strip().lower(), [])


def get_user_professors(user_email):
    """Full professor dicts this user is tracking."""
    ids = set(get_user_professor_ids(user_email))
    return [p for p in get_all_professors() if p["id"] in ids]


def get_subscribers_for_professor(professor_id):
    """Every user email tracking this professor -- used to know who should
    be notified when this professor's data changes."""
    subs = _load_json(USER_PROFESSORS_PATH)
    return [email for email, ids in subs.items() if professor_id in ids]