"""
push_notifications.py
Sends real Web Push notifications (works even when the app is closed,
as long as the browser/OS is running) to subscribed physicians and nurses.

This is deliberately kept as pure notification delivery -- it never
triggers a treatment action, only pages a human. That keeps it outside
the "AI proposes, physician decides" boundary rather than inside it: an
alert is not a clinical action, it's a request for a human to look.
"""

import os
import base64
import json
from typing import Iterable

from pywebpush import webpush, WebPushException
from sqlalchemy.orm import Session

from database import PushSubscription, User

VAPID_PRIVATE_KEY_B64 = os.environ.get("VAPID_PRIVATE_KEY_B64", "")
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:admin@example.com")


def _vapid_private_key_pem() -> str:
    if not VAPID_PRIVATE_KEY_B64:
        raise RuntimeError("VAPID_PRIVATE_KEY_B64 not configured.")
    return base64.b64decode(VAPID_PRIVATE_KEY_B64).decode()


def send_push_to_roles(db: Session, roles: Iterable[str], title: str, body: str, url: str = "/") -> dict:
    """Sends a push notification to every subscribed device belonging to
    users with one of the given roles (e.g. ["physician", "nurse"]).
    Returns a summary of successes/failures rather than raising, so a push
    failure never breaks the caller's main response."""
    if not VAPID_PRIVATE_KEY_B64:
        return {"sent": 0, "failed": 0, "skipped_reason": "VAPID not configured"}

    subs = (
        db.query(PushSubscription)
        .join(User, User.id == PushSubscription.user_id)
        .filter(User.role.in_(list(roles)))
        .all()
    )

    payload = json.dumps({"title": title, "body": body, "url": url})
    sent, failed, stale_ids = 0, 0, []

    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=_vapid_private_key_pem(),
                vapid_claims={"sub": VAPID_CLAIMS_EMAIL},
            )
            sent += 1
        except WebPushException as e:
            failed += 1
            # 404/410 means the browser subscription is dead -- clean it up
            # so we stop trying it every time.
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                stale_ids.append(sub.id)

    if stale_ids:
        db.query(PushSubscription).filter(PushSubscription.id.in_(stale_ids)).delete(synchronize_session=False)
        db.commit()

    return {"sent": sent, "failed": failed, "stale_removed": len(stale_ids)}
