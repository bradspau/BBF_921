"""
Unit tests for src/services/state_machine.py.

Verifies every valid transition, every invalid transition,
no-op self-transitions, and the terminal-state guard.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.services.state_machine import validate_transition, ALL_STATES, TERMINAL_STATES


# ── Valid transitions ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("from_s, to_s", [
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
])
def test_valid_transitions(from_s: str, to_s: str) -> None:
    validate_transition(from_s, to_s)  # must not raise


# ── No-op self-transitions ────────────────────────────────────────────────────

@pytest.mark.parametrize("state", sorted(ALL_STATES))
def test_noop_transitions(state: str) -> None:
    validate_transition(state, state)  # same-state is always allowed


# ── Invalid transitions (not in FSM graph) ────────────────────────────────────

@pytest.mark.parametrize("from_s, to_s", [
    ("ACKNOWLEDGED", "FULFILLED"),
    ("ACKNOWLEDGED", "DEGRADED"),
    ("ACKNOWLEDGED", "SUSPENDED"),
    ("FULFILLED",    "DEGRADED"),
    ("FULFILLED",    "SUSPENDED"),
    ("DEGRADED",     "FULFILLED"),
    ("SUSPENDED",    "FULFILLED"),
    ("SUSPENDED",    "DEGRADED"),
])
def test_invalid_transitions_raise_400(from_s: str, to_s: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_transition(from_s, to_s)
    assert exc_info.value.status_code == 400
    assert from_s in exc_info.value.detail
    assert to_s in exc_info.value.detail


# ── Terminal-state guard ──────────────────────────────────────────────────────

@pytest.mark.parametrize("to_s", sorted(ALL_STATES - TERMINAL_STATES))
def test_terminal_state_blocks_all_exits(to_s: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_transition("TERMINATED", to_s)
    assert exc_info.value.status_code == 400
    assert "terminal" in exc_info.value.detail.lower()


# ── Unknown target state ──────────────────────────────────────────────────────

def test_unknown_to_state_raises_422() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_transition("ACKNOWLEDGED", "BANANA")
    assert exc_info.value.status_code == 422
    assert "BANANA" in exc_info.value.detail


# ── Module-level constant sanity ──────────────────────────────────────────────

def test_all_states_count() -> None:
    assert len(ALL_STATES) == 6


def test_terminal_states_is_subset() -> None:
    assert TERMINAL_STATES.issubset(ALL_STATES)
