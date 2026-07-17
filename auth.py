import json
import os
import hashlib
import hmac
import secrets

USERS_PATH = "data/users.json"


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


def register_user(email, password):
    email = email.strip().lower()
    if not email or "@" not in email:
        return False, "Enter a valid email address."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."

    users = _load_users()
    if any(u["email"] == email for u in users):
        return False, "An account with that email already exists."

    pw_hash, salt = _hash_password(password)
    users.append({"email": email, "password_hash": pw_hash, "salt": salt})
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
    """Returns every registered user's email — used by scheduler.py to build
    the recipient list sent to the n8n webhook for citation alert emails."""
    return [u["email"] for u in _load_users()]