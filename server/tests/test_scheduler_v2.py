"""v2.0 scheduler tests: custom daily start time, window cutover, next-run
computation with jitter and missed-window catch-up, and PATCH /me/settings."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.database import utcnow
from app.models.models import Batch, Customer, DownloadJob
from app.services.scheduler import (
    compute_next_run,
    run_scheduler_all,
    run_scheduler_for_customer,
    validate_daily_start_time,
    window_date_for,
)
from tests.conftest import (
    make_customer,
    make_job,
    make_media,
    make_source,
    make_stack,
    make_subscription,
)

NOW = datetime(2026, 10, 1, 12, 0, 0)  # naive UTC


def _stack_with_start(db, code, start_time, tz="UTC"):
    customer = make_customer(db, code=code, timezone=tz)
    customer.daily_start_time = start_time
    db.commit()
    make_subscription(db, customer, expires_at=NOW + timedelta(days=30))
    return customer


# ---------------------------------------------------------------------------
# daily_start_time validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("00:00", (0, 0)),
        ("23:59", (23, 59)),
        ("06:00", (6, 0)),
        (" 18:30 ", (18, 30)),  # surrounding whitespace tolerated
    ],
)
def test_validate_daily_start_time_valid(value, expected):
    assert validate_daily_start_time(value) == expected


@pytest.mark.parametrize(
    "value",
    ["24:00", "12:60", "6:00", "abc", "", "00:000", "--:--", "12-30"],
)
def test_validate_daily_start_time_invalid(value):
    with pytest.raises(ValueError, match="invalid daily_start_time"):
        validate_daily_start_time(value)


# ---------------------------------------------------------------------------
# window_date_for: cutover at the customer's start time
# ---------------------------------------------------------------------------


def test_window_date_default_midnight_matches_local_date():
    # start 00:00 == v1.1 local-date semantics.
    assert (
        window_date_for("Asia/Karachi", "00:00", datetime(2026, 10, 1, 20, 0, 0))
        == date(2026, 10, 2)
    )


def test_window_date_custom_start_before_opening():
    # 20:00 UTC Oct 1 == 01:00 Oct 2 in Karachi. With a 06:00 start the
    # window that opened Oct 1 06:00 is still current -> Oct 1.
    assert (
        window_date_for("Asia/Karachi", "06:00", datetime(2026, 10, 1, 20, 0, 0))
        == date(2026, 10, 1)
    )


def test_window_date_custom_start_after_opening():
    # 01:30 UTC == 06:30 local Oct 1; 06:00 opening already passed -> Oct 1.
    assert (
        window_date_for("Asia/Karachi", "06:00", datetime(2026, 10, 1, 1, 30, 0))
        == date(2026, 10, 1)
    )


def test_window_date_exactly_at_opening():
    # 01:00 UTC == 06:00 local Oct 1: the new window just opened -> Oct 1.
    assert (
        window_date_for("Asia/Karachi", "06:00", datetime(2026, 10, 1, 1, 0, 0))
        == date(2026, 10, 1)
    )


def test_window_date_invalid_inputs_fall_back():
    assert (
        window_date_for("Mars/Olympus_Mons", "99:99", datetime(2026, 10, 1, 12, 0, 0))
        == date(2026, 10, 1)  # UTC fallback + 00:00 fallback
    )


# ---------------------------------------------------------------------------
# compute_next_run
# ---------------------------------------------------------------------------


def test_compute_next_run_catchup_when_never_run():
    nxt = compute_next_run("UTC", "06:00", NOW, None, jitter_seconds=0)
    assert nxt == NOW.replace(tzinfo=timezone.utc)


def test_compute_next_run_catchup_when_stale():
    last = NOW - timedelta(hours=26)
    nxt = compute_next_run("UTC", "06:00", NOW, last, jitter_seconds=0)
    assert nxt == NOW.replace(tzinfo=timezone.utc)


def test_compute_next_run_due_when_window_unprocessed():
    # Ran 23h ago (yesterday 13:00 UTC), but today's 06:00 window opened
    # after that run and was never processed -> due now (< 25h irrelevant).
    last = NOW - timedelta(hours=23)
    nxt = compute_next_run("UTC", "06:00", NOW, last, jitter_seconds=0)
    assert nxt == NOW.replace(tzinfo=timezone.utc)


def test_compute_next_run_next_opening_tomorrow():
    # 12:00 UTC, start 06:00, ran 1h ago -> next opening is tomorrow 06:00.
    nxt = compute_next_run(
        "UTC", "06:00", NOW, NOW - timedelta(hours=1), jitter_seconds=0
    )
    assert nxt == datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)


def test_compute_next_run_opening_later_today():
    # 12:00 UTC, start 18:00, ran 1h ago -> today 18:00.
    nxt = compute_next_run(
        "UTC", "18:00", NOW, NOW - timedelta(hours=1), jitter_seconds=0
    )
    assert nxt == datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)


def test_compute_next_run_timezone_midnight_crossover():
    # 19:00 UTC Oct 1 == 00:00 Oct 2 in Karachi; start 00:30 local, ran 1h
    # ago -> next opening is today 00:30 local == Oct 1 19:30 UTC.
    nxt = compute_next_run(
        "Asia/Karachi",
        "00:30",
        datetime(2026, 10, 1, 19, 0, 0),
        datetime(2026, 10, 1, 18, 0, 0),
        jitter_seconds=0,
    )
    assert nxt == datetime(2026, 10, 1, 19, 30, tzinfo=timezone.utc)


def test_compute_next_run_jitter_bounds():
    # Default jitter stays within +/- 5 minutes of the deterministic opening.
    opening = datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)
    for _ in range(20):
        nxt = compute_next_run("UTC", "06:00", NOW, NOW - timedelta(hours=1))
        assert opening - timedelta(seconds=300) <= nxt <= opening + timedelta(
            seconds=300
        )


# ---------------------------------------------------------------------------
# Scheduler passes with custom start time
# ---------------------------------------------------------------------------


def test_scheduler_batch_uses_window_date(db):
    customer = _stack_with_start(db, "SCHV1", "06:00", tz="Asia/Karachi")
    source = make_source(db, customer, input_value="@handle")
    media = make_media(db, customer, source)
    make_job(db, customer, media, status="queued")

    batch = run_scheduler_for_customer(
        db, customer, batch_size=20, now=datetime(2026, 10, 1, 20, 0, 0)
    )

    assert batch is not None
    # 01:00 local Oct 2 with a 06:00 start -> still Oct 1's window.
    assert batch.batch_date == date(2026, 10, 1)
    assert batch.timezone == "Asia/Karachi"


def test_scheduler_records_last_run(db):
    customer = _stack_with_start(db, "SCHV2", "00:00")
    run_scheduler_for_customer(db, customer, batch_size=20, now=NOW)
    db.expire_all()
    assert db.get(Customer, customer.id).last_scheduler_run_at == NOW


def test_run_scheduler_all_skips_not_due_customer(db):
    customer = _stack_with_start(db, "SCHV3", "00:00")
    first = run_scheduler_all(db, batch_size=20, now=NOW)
    assert len(first) == 1

    # Five minutes later the next window (tomorrow 00:00 +/- jitter) is not
    # due -> skipped.
    second = run_scheduler_all(db, batch_size=20, now=NOW + timedelta(minutes=5))
    assert second == []
    assert db.query(Batch).filter(Batch.customer_id == customer.id).count() == 1


def test_run_scheduler_all_catchup_after_missed_window(db):
    customer = _stack_with_start(db, "SCHV4", "00:00")
    customer.last_scheduler_run_at = NOW - timedelta(hours=26)
    db.commit()

    batches = run_scheduler_all(db, batch_size=20, now=NOW)

    assert len(batches) == 1  # ran immediately despite the window logic


def test_run_scheduler_all_runs_at_window_opening(db):
    customer = _stack_with_start(db, "SCHV5", "06:00")
    # Pretend yesterday's window ran at its opening.
    customer.last_scheduler_run_at = datetime(2026, 9, 30, 6, 5, 0)
    db.commit()

    # 06:10 UTC Oct 1: today's 06:00 opening (+/- jitter) is due.
    batches = run_scheduler_all(
        db, batch_size=20, now=datetime(2026, 10, 1, 6, 10, 0)
    )
    assert len(batches) == 1
    assert batches[0].batch_date == date(2026, 10, 1)


# ---------------------------------------------------------------------------
# PATCH /me/settings
# ---------------------------------------------------------------------------


def test_patch_settings_updates_start_time(db, client):
    stack = make_stack(db, client, code="SCHV10")
    resp = client.patch(
        "/api/v1/me/settings",
        json={"daily_start_time": "06:00"},
        headers=stack.headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["daily_start_time"] == "06:00"
    db.expire_all()
    assert db.get(Customer, stack.customer.id).daily_start_time == "06:00"


@pytest.mark.parametrize("bad", ["25:00", "6:00", "nope", "12:60"])
def test_patch_settings_rejects_invalid(db, client, bad):
    stack = make_stack(db, client, code="SCHV11")
    resp = client.patch(
        "/api/v1/me/settings",
        json={"daily_start_time": bad},
        headers=stack.headers,
    )
    assert resp.status_code == 400


def test_patch_settings_requires_auth(client):
    resp = client.patch("/api/v1/me/settings", json={"daily_start_time": "06:00"})
    assert resp.status_code == 401


def test_me_reports_daily_start_time(db, client):
    stack = make_stack(db, client, code="SCHV12")
    resp = client.get("/api/v1/", headers=stack.headers)
    assert resp.status_code == 200
    assert resp.json()["customer"]["daily_start_time"] == "00:00"
