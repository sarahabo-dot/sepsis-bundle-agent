"""
seed_user.py
One-time helper to create a first login for local development/testing.
Run: python seed_user.py

In real deployment, user provisioning should go through the hospital's
identity system (e.g. SSO/LDAP integration), not this script.
"""

from database import init_db, SessionLocal, User
from auth import hash_password

def seed():
    init_db()
    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == "sarah").first():
            print("User 'sarah' already exists.")
            return
        user = User(
            username="sarah",
            hashed_password=hash_password("changeme123"),
            full_name="Sarah",
            role="physician",
        )
        db.add(user)
        db.commit()
        print("Created user 'sarah' with password 'changeme123' -- change this immediately.")
    finally:
        db.close()

if __name__ == "__main__":
    seed()
