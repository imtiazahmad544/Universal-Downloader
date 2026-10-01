"""Shared pytest fixtures for the Universal Downloader backend test suite.

Conventions:
- Fresh SQLite file per test (function-scoped engine on tmp_path).
- TestClient(create_app()) with the get_db dependency overridden to point at
  the test engine. The app lifespan is NOT entered, so init_db() never touches
  the real dev database file.
- Password hashing (argon2id ~0.5-1s) is cached per password string so user
  creation stays cheap even with function-scoped fixtures.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import deps as deps_module
from app.core.canonicalize import canonicalize_source, canonicalize_url
from app.core.database import Base, get_db, utcnow
from app.core.security import hash_password
from app.main import create_app
from app.models.models import (
    Batch,
    Customer,
    DownloadJob,
    MediaItem,
    Source,
    Subscription,
    User,
)
from app.services.subscriptions import add_months

# Default password used by every test user; its argon2id hash is computed once.
PASSWORD = "correct-horse-battery-staple"

_hash_cache: dict[str, str] = {}


def password_hash(password: str) -> str:
    """Cached argon2id hash: hashing is slow, verification is what matters."""
    if password not in _hash_cache:
        _hash_cache[password] = hash_password(password)
    return _hash_cache[password]


@pytest.fixture(autouse=True)
def _reset_login_rate_limit():
    """Login rate limiting is per-IP in-memory state; clear it per test so
    one test's logins can never 429 another test."""
    deps_module._login_attempts.clear()
    yield
    deps_module._login_attempts.clear()


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path}/test.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture
def client(engine):
    app = create_app()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    # NOTE: no `with` block — the lifespan (init_db on the real DATABASE_URL)
    # is deliberately not entered.
    return TestClient(app)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

_code_seq = {"n": 0}


def next_code(prefix: str = "CUST") -> str:
    _code_seq["n"] += 1
    return f"{prefix}{_code_seq['n']:04d}"


def make_customer(db, code=None, name="Test Customer", status="active", timezone="UTC"):
    customer = Customer(
        customer_code=code or next_code(),
        name=name,
        status=status,
        timezone=timezone,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def make_user(db, customer=None, password=PASSWORD, role="customer", username=None,
              status="active"):
    user = User(
        customer_id=customer.id if customer is not None else None,
        role=role,
        username=username or (customer.customer_code if customer is not None else next_code("USER")),
        password_hash=password_hash(password),
        status=status,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_subscription(db, customer, expires_at=None, starts_at=None, status="active"):
    now = utcnow()
    subscription = Subscription(
        customer_id=customer.id,
        starts_at=starts_at or now,
        expires_at=expires_at if expires_at is not None else add_months(now, 1),
        status=status,
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription


def make_source(db, customer, platform="youtube", input_value="@somehandle",
                canonical_id=None, status="active"):
    source = Source(
        customer_id=customer.id,
        platform=platform,
        input_value=input_value,
        canonical_id=canonical_id or canonicalize_source(platform, input_value),
        status=status,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def make_media(db, customer, source, url="https://example.com/v/1", external_id=None,
               status="discovered"):
    media = MediaItem(
        customer_id=customer.id,
        source_id=source.id,
        canonical_url=canonicalize_url(url),
        external_id=external_id,
        status=status,
    )
    db.add(media)
    db.commit()
    db.refresh(media)
    return media


def make_job(db, customer, media, status="queued", batch_id=None, attempts=0):
    job = DownloadJob(
        customer_id=customer.id,
        media_item_id=media.id,
        status=status,
        batch_id=batch_id,
        attempts=attempts,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def make_batch(db, customer, batch_date=None, size=20, status="open", timezone=None):
    batch = Batch(
        customer_id=customer.id,
        batch_date=batch_date or utcnow().date(),
        timezone=timezone or customer.timezone or "UTC",
        size=size,
        status=status,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


def login_tokens(client, identifier, password=PASSWORD):
    resp = client.post(
        "/api/v1/auth/login",
        json={"identifier": identifier, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def auth_headers(client, identifier, password=PASSWORD):
    tokens = login_tokens(client, identifier, password)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def make_stack(db, client, code=None, password=PASSWORD, subscription_days=30,
               customer_status="active", timezone="UTC"):
    """One customer + login user + (optional) subscription + auth headers."""
    customer = make_customer(db, code=code, status=customer_status, timezone=timezone)
    user = make_user(db, customer, password=password)
    if subscription_days is not None:
        make_subscription(
            db, customer, expires_at=utcnow() + timedelta(days=subscription_days)
        )
    headers = auth_headers(client, customer.customer_code, password)
    return SimpleNamespace(customer=customer, user=user, headers=headers)
