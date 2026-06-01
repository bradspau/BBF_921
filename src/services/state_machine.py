"""
Lifecycle FSM for TMF921 Intent resources.

States: ACKNOWLEDGED, ACTIVE, FULFILLED, DEGRADED, SUSPENDED, TERMINATED
TERMINATED is the only terminal state (no exit transitions).
"""
from __future__ import annotations

from fastapi import HTTPException

# Valid (from_state, to_state) pairs
_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("ACKNOWLEDGED", "ACTIVE"),
    ("ACKNOWLEDGED", "TERMINATED"),
    ("ACTIVE",       "FULFILLED"),
    ("ACTIVE",       "DEGRADED"),
    ("ACTIVE",       "SUSPENDED"),
    ("ACTIVE",       "TERMINATED"),
    ("FULFILLED",    "ACTIVE"),
    ("FULFILLED",    "TERMINATED"),
    ("DEGRADED",     "ACTIVE"),
    ("DEGRADED",     "SUSPENDED"),
    ("DEGRADED",     "TERMINATED"),
    ("SUSPENDED",    "ACTIVE"),
    ("SUSPENDED",    "TERMINATED"),
})

ALL_STATES: frozenset[str] = frozenset({
    "ACKNOWLEDGED", "ACTIVE", "FULFILLED", "DEGRADED", "SUSPENDED", "TERMINATED",
})

TERMINAL_STATES: frozenset[str] = frozenset({"TERMINATED"})


def validate_transition(from_status: str, to_status: str) -> None:
    """
    Raise HTTP 422 if to_status is not a known state, or HTTP 400 if the
    transition from_status → to_status is not permitted by the FSM.
    """
    if to_status not in ALL_STATES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown lifecycleStatus: {to_status!r}",
        )
    if from_status == to_status:
        return  # no-op transitions are always valid
    if from_status in TERMINAL_STATES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Intent is in terminal state {from_status!r}; "
                "no further transitions are allowed."
            ),
        )
    if (from_status, to_status) not in _TRANSITIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid lifecycle transition: {from_status!r} → {to_status!r}"
            ),
        )
