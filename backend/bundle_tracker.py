"""
bundle_tracker.py
Hour-1 sepsis bundle state machine. Deterministic: which items are
currently required is derived from patient state (MAP, lactate), not
guessed by a model. Timer math is plain arithmetic on timestamps.
"""

from datetime import datetime
from typing import Optional
from models import BundleItemState

BUNDLE_DEFINITIONS = [
    {"key": "cultures", "label": "Blood cultures obtained (before antibiotics)", "always": True},
    {"key": "antibiotics", "label": "Broad-spectrum antibiotics administered", "always": True},
    {"key": "lactate", "label": "Initial lactate measured", "always": True},
    {"key": "lactate_repeat", "label": "Repeat lactate", "always": False,
     "reason": "required only if initial lactate > 2 mmol/L"},
    {"key": "fluids", "label": "30 mL/kg crystalloid", "always": False,
     "reason": "required only if hypotension (MAP < 65) or lactate >= 4 mmol/L"},
    {"key": "pressors", "label": "Vasopressors started", "always": False,
     "reason": "required only if MAP < 65 after fluid resuscitation"},
]

TARGET_MINUTES = 60


def evaluate_relevance(map_mmhg: Optional[float], lactate_initial: Optional[float]) -> dict:
    """Returns {item_key: (is_relevant: bool, reason: str|None)}"""
    hypotensive = map_mmhg is not None and map_mmhg < 65
    high_lactate = lactate_initial is not None and lactate_initial >= 4
    elevated_lactate = lactate_initial is not None and lactate_initial > 2

    relevance = {}
    for item in BUNDLE_DEFINITIONS:
        if item["always"]:
            relevance[item["key"]] = (True, None)
        elif item["key"] == "lactate_repeat":
            relevance[item["key"]] = (elevated_lactate, item["reason"])
        elif item["key"] == "fluids":
            relevance[item["key"]] = (hypotensive or high_lactate, item["reason"])
        elif item["key"] == "pressors":
            relevance[item["key"]] = (hypotensive, item["reason"])
    return relevance


def build_bundle_state(recognition_time: Optional[datetime],
                        map_mmhg: Optional[float],
                        lactate_initial: Optional[float],
                        confirmations: dict) -> list:
    """
    confirmations: {item_key: {"done": bool, "confirmed_at": datetime|None,
                                "acknowledged_by": str|None, "confirmed_by": str|None}}
    Returns a list of BundleItemState.
    """
    relevance = evaluate_relevance(map_mmhg, lactate_initial)
    states = []
    for item in BUNDLE_DEFINITIONS:
        key = item["key"]
        is_relevant, reason = relevance[key]
        conf = confirmations.get(key, {})
        states.append(BundleItemState(
            key=key,
            label=item["label"],
            required=is_relevant,
            reason_if_not_required=None if is_relevant else reason,
            done=conf.get("done", False),
            confirmed_at=conf.get("confirmed_at"),
            acknowledged_by=conf.get("acknowledged_by"),
            confirmed_by=conf.get("confirmed_by"),
        ))
    return states


def elapsed_minutes(recognition_time: Optional[datetime], now: Optional[datetime] = None) -> Optional[int]:
    if recognition_time is None:
        return None
    now = now or datetime.utcnow()
    return int((now - recognition_time).total_seconds() // 60)


def overdue_items(states: list, recognition_time: Optional[datetime],
                   now: Optional[datetime] = None) -> list:
    """Required, not-yet-done items past the 1-hour target."""
    mins = elapsed_minutes(recognition_time, now)
    if mins is None:
        return []
    return [s for s in states if s.required and not s.done and mins > TARGET_MINUTES]


def bundle_summary(states: list) -> dict:
    required = [s for s in states if s.required]
    completed = [s for s in required if s.done]
    return {
        "required_count": len(required),
        "completed_count": len(completed),
        "complete": len(completed) == len(required) and len(required) > 0,
    }
