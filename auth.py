"""
auth.py

Users AND their tracked professors now live in ONE place: users.json.
Previously, subscriptions were tracked separately in
data/user_professors.json (a dict of email -> [professor_ids]) while
users.json only had login credentials. Keeping the two in sync across two
files was the source of a real bug: users ended up seeing each other's
professors mixed together, because there was no single source of truth
tying a professor list to a specific user record.

Now every user object in users.json carries its OWN "professors" list:

    {"email": "a@b.com", "name": "Alice", "password_hash": "...",
     "salt": "...", "professors": ["profid1", "profid2"]}

Every read of "which professors does this user track" and every write of
"add/remove a professor for this user" goes through the SAME user record
in the SAME file -- there's no second file that can drift out of sync.
"""

import json
import os
import hashlib
import hmac
import secrets

USERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "users.json")


def _load_users():
    if not os.path.exists(USERS_PATH):
        return []
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_users(users):
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)


def _hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000
    )
    return pw_hash.hex(), salt


def _find_user(users, email):
    email = email.strip().lower()
    return next((u for u in users if u["email"] == email), None)


def register_user(email, password, name):
    email = email.strip().lower()
    name = name.strip()
    if not email or "@" not in email:
        return False, "Enter a valid email address."
    if not name:
        return False, "Enter your name."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."

    users = _load_users()
    if any(u["email"] == email for u in users):
        return False, "An account with that email already exists."

    pw_hash, salt = _hash_password(password)
    users.append({
        "email": email,
        "name": name,
        "password_hash": pw_hash,
        "salt": salt,
        "professors": [],  # this user's own tracked professor_ids -- private to them
    })
    _save_users(users)
    return True, "Account created — you can log in now."


def verify_user(email, password):
    email = email.strip().lower()
    users = _load_users()
    user = next((u for u in users if u["email"] == email), None)
    if not user:
        return False
    pw_hash, _ = _hash_password(password, user["salt"])
    return hmac.compare_digest(pw_hash, user["password_hash"])


def get_all_subscriber_emails():
    """Kept for backward compatibility — some callers just want emails."""
    return [u["email"] for u in _load_users()]


def get_all_subscribers():
    """Returns every registered user's email + name, e.g.
    [{"email": "a@b.com", "name": "Alice"}, ...]. Users registered before
    the name field existed will have name == "" — fine, callers should
    handle a blank name gracefully."""
    return [{"email": u["email"], "name": u.get("name", "")} for u in _load_users()]


# --- Per-user professor tracking -------------------------------------------
# These replace professors.py's old subscribe/unsubscribe/get_user_professor_ids,
# which used to read/write a separate data/user_professors.json file.

def get_user_professor_ids(email):
    """This user's own tracked professor_ids -- ONLY from their own user
    record, never anyone else's."""
    users = _load_users()
    user = _find_user(users, email)
    return list(user.get("professors", [])) if user else []


def add_professor_to_user(email, professor_id):
    """Adds professor_id to THIS user's own list only. If the user
    doesn't exist (shouldn't happen post-login, but defensively) this is
    a no-op."""
    users = _load_users()
    user = _find_user(users, email)
    if not user:
        return False
    professors_list = user.setdefault("professors", [])
    if professor_id not in professors_list:
        professors_list.append(professor_id)
        _save_users(users)
    return True


def remove_professor_from_user(email, professor_id):
    """Removes professor_id from THIS user's own list only."""
    users = _load_users()
    user = _find_user(users, email)
    if not user:
        return False
    professors_list = user.setdefault("professors", [])
    if professor_id in professors_list:
        professors_list.remove(professor_id)
        _save_users(users)
    return True


def get_subscribers_for_professor(professor_id):
    """Every user email tracking this professor -- read directly from
    each user's own "professors" list, so a user only ever shows up here
    because THEIR OWN record says so."""
    users = _load_users()
    return [u["email"] for u in users if professor_id in u.get("professors", [])]