"""State-machine tests: the 14 job states and their exact SRS section 8 (v1.1)
transition graph, plus can_transition / validate_transition / shortest_path."""

import pytest

from app.services.state_machine import (
    BATCHED,
    CANCELLED,
    COMPLETED,
    DISCOVERED,
    DOWNLOADING,
    DUPLICATE,
    FAILED,
    INVALID,
    PAUSED,
    QUEUED,
    READY,
    REQUEUED,
    RETRY_WAIT,
    STATES,
    TRANSITIONS,
    WAITING_FOR_NETWORK,
    InvalidTransitionError,
    can_transition,
    shortest_path,
    validate_transition,
)

# The exact transition graph from SRS section 8 v1.1. This literal mirrors
# the spec; any drift in the implementation fails here loudly.
EXPECTED_TRANSITIONS = {
    DISCOVERED: frozenset({QUEUED, INVALID, DUPLICATE}),
    QUEUED: frozenset({BATCHED, PAUSED, CANCELLED}),
    BATCHED: frozenset({READY, PAUSED}),
    READY: frozenset({DOWNLOADING, PAUSED, WAITING_FOR_NETWORK}),
    DOWNLOADING: frozenset({COMPLETED, RETRY_WAIT, FAILED, WAITING_FOR_NETWORK}),
    RETRY_WAIT: frozenset({READY, FAILED}),
    COMPLETED: frozenset({REQUEUED}),
    FAILED: frozenset({READY, REQUEUED}),  # v1.1: manual retry goes FAILED -> READY
    PAUSED: frozenset({QUEUED, READY, CANCELLED}),
    CANCELLED: frozenset({REQUEUED}),
    WAITING_FOR_NETWORK: frozenset({READY, PAUSED, CANCELLED}),
    INVALID: frozenset(),  # terminal
    DUPLICATE: frozenset(),  # terminal
    REQUEUED: frozenset({QUEUED}),
}


def test_exactly_fourteen_states():
    assert len(STATES) == 14


def test_transitions_match_srs_v1_1_exactly():
    assert TRANSITIONS == EXPECTED_TRANSITIONS


@pytest.mark.parametrize(
    "state", sorted({INVALID, DUPLICATE})
)
def test_terminal_states_have_no_outgoing(state):
    assert TRANSITIONS[state] == frozenset()
    for other in STATES:
        if other == state:
            continue
        assert not can_transition(state, other)


@pytest.mark.parametrize(
    "frm,to,expected",
    [
        # v1.1: manual retry FAILED -> READY
        (FAILED, READY, True),
        (READY, COMPLETED, False),
        (BATCHED, CANCELLED, False),
        (DOWNLOADING, WAITING_FOR_NETWORK, True),
        (WAITING_FOR_NETWORK, READY, True),
        # a few extra graph spot checks
        (DISCOVERED, QUEUED, True),
        (QUEUED, BATCHED, True),
        (REQUEUED, QUEUED, True),
        (CANCELLED, QUEUED, False),
        (INVALID, QUEUED, False),
        (DUPLICATE, QUEUED, False),
        (PAUSED, READY, True),
        (READY, DOWNLOADING, True),
        (DOWNLOADING, COMPLETED, True),
        (COMPLETED, DOWNLOADING, False),
    ],
)
def test_can_transition_spot_checks(frm, to, expected):
    assert can_transition(frm, to) is expected


def test_validate_transition_accepts_legal():
    validate_transition(FAILED, READY)
    validate_transition(BATCHED, PAUSED)
    validate_transition(WAITING_FOR_NETWORK, CANCELLED)


def test_validate_transition_raises_on_illegal():
    with pytest.raises(InvalidTransitionError):
        validate_transition(READY, COMPLETED)
    with pytest.raises(InvalidTransitionError):
        validate_transition(BATCHED, CANCELLED)


def test_validate_transition_raises_on_unknown_states():
    with pytest.raises(InvalidTransitionError):
        validate_transition("bogus", READY)
    with pytest.raises(InvalidTransitionError):
        validate_transition(READY, "bogus")


def test_invalid_transition_error_is_value_error():
    assert issubclass(InvalidTransitionError, ValueError)


def test_shortest_path_batched_to_cancelled():
    assert shortest_path(BATCHED, CANCELLED) == [BATCHED, PAUSED, CANCELLED]


def test_shortest_path_more_spot_checks():
    assert shortest_path(DISCOVERED, COMPLETED) == [
        DISCOVERED,
        QUEUED,
        BATCHED,
        READY,
        DOWNLOADING,
        COMPLETED,
    ]
    assert shortest_path(FAILED, COMPLETED) == [
        FAILED,
        READY,
        DOWNLOADING,
        COMPLETED,
    ]
    assert shortest_path(READY, READY) == [READY]
    # Terminal states cannot leave.
    assert shortest_path(INVALID, QUEUED) is None
    assert shortest_path(DUPLICATE, CANCELLED) is None
    # Unknown states return None.
    assert shortest_path("bogus", QUEUED) is None
