"""
chat_store.py

Persists chat sessions per logged-in user, so each user can have multiple
named conversations they can navigate between via a history list, rather
than a single in-memory conversation that resets on refresh/logout.

Storage shape (data/chats.json):
{
  "user@example.com": [
    {
      "chat_id": "uuid",
      "title": "First 50 chars of the first question...",
      "created_at": "2026-07-19T10:00:00",
      "messages": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."}
      ]
    },
    ...
  ]
}

Newest chat is always inserted at index 0, so callers can just take the
list as-is for a "most recent first" history view.
"""

import json
import os
import uuid
import datetime

CHATS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "chats.json")


def _load_all():
    if not os.path.exists(CHATS_PATH):
        return {}
    try:
        with open(CHATS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_all(data):
    os.makedirs(os.path.dirname(CHATS_PATH), exist_ok=True)
    with open(CHATS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def create_chat(user_email):
    """Creates a new, empty chat for this user and returns its chat_id."""
    data = _load_all()
    user_chats = data.setdefault(user_email, [])

    chat_id = str(uuid.uuid4())
    user_chats.insert(0, {
        "chat_id": chat_id,
        "title": "New Chat",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "messages": []
    })

    _save_all(data)
    return chat_id


def get_user_chats(user_email):
    """Returns [{chat_id, title, created_at}, ...] for the sidebar history
    list, newest first. Doesn't include full message bodies — keeps the
    list cheap to render even with a lot of history."""
    data = _load_all()
    return [
        {"chat_id": c["chat_id"], "title": c["title"], "created_at": c["created_at"]}
        for c in data.get(user_email, [])
    ]


def get_chat_messages(user_email, chat_id):
    data = _load_all()
    for c in data.get(user_email, []):
        if c["chat_id"] == chat_id:
            return c["messages"]
    return []


def append_message(user_email, chat_id, role, content):
    data = _load_all()
    user_chats = data.get(user_email, [])

    for c in user_chats:
        if c["chat_id"] == chat_id:
            c["messages"].append({"role": role, "content": content})
            # Auto-title the chat from the first user question, so the
            # history list shows something meaningful instead of every
            # entry reading "New Chat".
            if c["title"] == "New Chat" and role == "user":
                c["title"] = (content[:50] + "...") if len(content) > 50 else content
            break

    _save_all(data)


def delete_chat(user_email, chat_id):
    data = _load_all()
    data[user_email] = [c for c in data.get(user_email, []) if c["chat_id"] != chat_id]
    _save_all(data)