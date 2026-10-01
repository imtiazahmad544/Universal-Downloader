"""Download job status state machine (SRS section 8, v1.1).

This is the CENTRAL validator for job status transitions: every layer
(API, scheduler, workers) must validate through can_transition() /
validate_transition() before persisting a new job status. The transition
graph below implements the spec exactly — do not add edges without a spec
change.
"""

from collections import deque

# The 14 job states.
DISCOVERED = "discovered"
QUEUED = "queued"
BATCHED = "batched"
READY = "ready"
DOWNLOADING = "downloading"
RETRY_WAIT = "retry_wait"
COMPLETED = "completed"
FAILED = "failed"
PAUSED = "paused"
CANCELLED = "cancelled"
WAITING_FOR_NETWORK = "waiting_for_network"
INVALID = "invalid"
DUPLICATE = "duplicate"
REQUEUED = "requeued"

STATES = frozenset(
    {
        DISCOVERED,
        QUEUED,
        BATCHED,
        READY,
        DOWNLOADING,
        RETRY_WAIT,
        COMPLETED,
        FAILED,
        PAUSED,
        CANCELLED,
        WAITING_FOR_NETWORK,
        INVALID,
        DUPLICATE,
        REQUEUED,
    }
)

TERMINAL_STATES = frozenset({INVALID, DUPLICATE})

# Exact transition graph from SRS section 8 v1.1.
TRANSITIONS: dict[str, frozenset[str]] = {
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


class InvalidTransitionError(ValueError):
    """Raised when a job status transition is not allowed by the state machine."""


def can_transition(frm: str, to: str) -> bool:
    """Return True if the job may move from `frm` to `to`."""
    return to in TRANSITIONS.get(frm, frozenset())


def validate_transition(frm: str, to: str) -> None:
    """Validate a transition, raising InvalidTransitionError with a clear
    message when it is not allowed."""
    if frm not in STATES:
        raise InvalidTransitionError(f"unknown job status: {frm!r}")
    if to not in STATES:
        raise InvalidTransitionError(f"unknown job status: {to!r}")
    if not can_transition(frm, to):
        allowed = sorted(TRANSITIONS[frm])
        raise InvalidTransitionError(
            f"invalid job status transition: {frm!r} -> {to!r}; "
            f"allowed from {frm!r}: {allowed or 'none (terminal state)'}"
        )


def shortest_path(frm: str, to: str) -> list[str] | None:
    """Shortest legal transition path from `frm` to `to` (BFS over TRANSITIONS).

    Used by cascade logic (e.g. BATCHED -> PAUSED -> CANCELLED). Returns None
    when `to` is unreachable from `frm`.
    """
    if frm == to:
        return [frm]
    if frm not in STATES or to not in STATES:
        return None
    visited = {frm}
    queue: deque[list[str]] = deque([[frm]])
    while queue:
        path = queue.popleft()
        for nxt in sorted(TRANSITIONS[path[-1]]):  # sorted for determinism
            if nxt in visited:
                continue
            new_path = path + [nxt]
            if nxt == to:
                return new_path
            visited.add(nxt)
            queue.append(new_path)
    return None
