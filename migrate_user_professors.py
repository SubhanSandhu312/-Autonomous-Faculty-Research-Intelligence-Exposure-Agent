"""
migrate_user_professors.py

One-time migration: pulls existing subscriptions out of the OLD
data/user_professors.json (email -> [professor_ids]) and writes each
one into the corresponding user's own "professors" list inside
users.json, using the new auth.add_professor_to_user().

Run this ONCE after upgrading to the new auth.py/professors.py:

    python migrate_user_professors.py

After it reports success, you can delete data/user_professors.json --
it is no longer read by anything.
"""

import json
import os

import auth

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OLD_PATH = os.path.join(BASE_DIR, "data", "user_professors.json")


def main():
    if not os.path.exists(OLD_PATH):
        print(f"No old subscriptions file found at {OLD_PATH} -- nothing to migrate.")
        return

    with open(OLD_PATH, "r", encoding="utf-8") as f:
        old_subs = json.load(f)

    if not old_subs:
        print("Old subscriptions file is empty -- nothing to migrate.")
        return

    total = 0
    for email, professor_ids in old_subs.items():
        for professor_id in professor_ids:
            ok = auth.add_professor_to_user(email, professor_id)
            if ok:
                total += 1
            else:
                print(f"  WARNING: no user record found for {email} -- "
                      f"skipped professor_id {professor_id}. "
                      f"(They may need to re-add it manually after registering.)")

    print(f"Migrated {total} subscription(s) across {len(old_subs)} user(s) into users.json.")
    print(f"You can now safely delete {OLD_PATH}.")


if __name__ == "__main__":
    main()