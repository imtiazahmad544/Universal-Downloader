"""v2.0 cookies tests: encryption roundtrip, format validation, resolution
precedence, and the customer-scoped cookies API (tenant isolation, no raw
values ever returned)."""

import pytest
from cryptography.fernet import Fernet

from app.core import config as config_module
from app.models.models import Customer
from app.services import cookies as cookies_mod
from app.services.cookies import (
    decrypt_cookies,
    encrypt_cookies,
    resolve_cookies_for_source,
    validate_cookies_format,
)
from tests.conftest import (
    make_customer,
    make_source,
    make_stack,
)

VALID_HEADER = "# Netscape HTTP Cookie File\n# https://curl.se/docs/http-cookies.html\n"
VALID_LINE = ".example.com\tTRUE\t/\tFALSE\t1893456000\tsessionid\tabc123"
VALID_TEXT = VALID_HEADER + VALID_LINE + "\n"
VALID_NO_HEADER = VALID_LINE + "\n.other.com\tTRUE\t/\tFALSE\t1893456000\tuid\txyz\n"


def _fernet_key() -> str:
    return Fernet.generate_key().decode("utf-8")


# ---------------------------------------------------------------------------
# Format validation
# ---------------------------------------------------------------------------


def test_validate_accepts_netscape_header():
    validate_cookies_format(VALID_TEXT)  # must not raise


def test_validate_accepts_tabbed_lines_without_header():
    validate_cookies_format(VALID_NO_HEADER)  # must not raise


def test_validate_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        validate_cookies_format("")
    with pytest.raises(ValueError, match="empty"):
        validate_cookies_format("   \n  ")


def test_validate_rejects_garbage():
    with pytest.raises(ValueError, match="not a recognized cookies format"):
        validate_cookies_format("hello world, this is not cookies")
    with pytest.raises(ValueError, match="not a recognized cookies format"):
        validate_cookies_format("# just a comment\n# another comment\n")


# ---------------------------------------------------------------------------
# Encrypt / decrypt
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setattr(
        config_module.settings, "COOKIES_ENCRYPTION_KEY", _fernet_key()
    )
    blob = encrypt_cookies(VALID_TEXT)
    assert blob != VALID_TEXT  # not stored in cleartext
    assert decrypt_cookies(blob) == VALID_TEXT


def test_decrypt_wrong_key_fails(monkeypatch):
    monkeypatch.setattr(
        config_module.settings, "COOKIES_ENCRYPTION_KEY", _fernet_key()
    )
    blob = encrypt_cookies(VALID_TEXT)
    monkeypatch.setattr(
        config_module.settings, "COOKIES_ENCRYPTION_KEY", _fernet_key()
    )
    with pytest.raises(ValueError, match="could not decrypt"):
        decrypt_cookies(blob)


def test_encrypt_rejects_bad_format(monkeypatch):
    monkeypatch.setattr(
        config_module.settings, "COOKIES_ENCRYPTION_KEY", _fernet_key()
    )
    with pytest.raises(ValueError, match="not a recognized cookies format"):
        encrypt_cookies("definitely not cookies")


def test_encrypt_rejects_invalid_key(monkeypatch):
    monkeypatch.setattr(
        config_module.settings, "COOKIES_ENCRYPTION_KEY", "not-a-fernet-key"
    )
    with pytest.raises(RuntimeError, match="not a valid Fernet key"):
        encrypt_cookies(VALID_TEXT)


def test_missing_key_in_prod_is_hard_error(monkeypatch):
    monkeypatch.setattr(config_module.settings, "COOKIES_ENCRYPTION_KEY", "")
    monkeypatch.setattr(config_module.settings, "APP_ENV", "prod")
    with pytest.raises(RuntimeError, match="must be set in production"):
        encrypt_cookies(VALID_TEXT)


def test_missing_key_in_dev_warns_and_works(monkeypatch, capsys):
    monkeypatch.setattr(config_module.settings, "COOKIES_ENCRYPTION_KEY", "")
    monkeypatch.setattr(config_module.settings, "APP_ENV", "dev")
    monkeypatch.setattr(cookies_mod, "_dev_ephemeral_key", None)
    blob = encrypt_cookies(VALID_TEXT)
    assert decrypt_cookies(blob) == VALID_TEXT
    assert "EPHEMERAL" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Resolution precedence
# ---------------------------------------------------------------------------


def test_resolve_source_override_wins(db, monkeypatch):
    monkeypatch.setattr(
        config_module.settings, "COOKIES_ENCRYPTION_KEY", _fernet_key()
    )
    customer = make_customer(db, code="CK1")
    customer.cookies_encrypted = encrypt_cookies(VALID_TEXT)
    source = make_source(db, customer)
    source.cookies_encrypted = encrypt_cookies(VALID_NO_HEADER)
    db.commit()
    assert resolve_cookies_for_source(source, db) == VALID_NO_HEADER


def test_resolve_falls_back_to_customer_global(db, monkeypatch):
    monkeypatch.setattr(
        config_module.settings, "COOKIES_ENCRYPTION_KEY", _fernet_key()
    )
    customer = make_customer(db, code="CK2")
    customer.cookies_encrypted = encrypt_cookies(VALID_TEXT)
    source = make_source(db, customer)
    db.commit()
    assert resolve_cookies_for_source(source, db) == VALID_TEXT


def test_resolve_none_when_nothing_stored(db):
    customer = make_customer(db, code="CK3")
    source = make_source(db, customer)
    assert resolve_cookies_for_source(source, db) is None


# ---------------------------------------------------------------------------
# API: source cookies
# ---------------------------------------------------------------------------


def _upload(client, url, text, headers):
    return client.put(
        url,
        files={"file": ("cookies.txt", text.encode("utf-8"), "text/plain")},
        headers=headers,
    )


def test_source_cookies_put_get_delete(db, client):
    stack = make_stack(db, client, code="CK10")
    source = make_source(db, stack.customer)

    # GET before upload: absent, and no raw values anywhere.
    resp = client.get(
        f"/api/v1/sources/{source.id}/cookies", headers=stack.headers
    )
    assert resp.status_code == 200
    assert resp.json() == {"present": False, "updated_at": None}

    # PUT valid cookies.
    resp = _upload(
        client, f"/api/v1/sources/{source.id}/cookies", VALID_TEXT, stack.headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["present"] is True
    assert body["updated_at"] is not None
    # Raw cookie values must never appear in any response.
    assert "abc123" not in resp.text
    assert "sessionid" not in resp.text

    # GET after upload: present, still no raw values.
    resp = client.get(
        f"/api/v1/sources/{source.id}/cookies", headers=stack.headers
    )
    assert resp.json()["present"] is True
    assert "abc123" not in resp.text

    # Stored encrypted at rest.
    db.expire_all()
    assert db.get(Customer, stack.customer.id) is not None
    from app.models.models import Source

    stored = db.get(Source, source.id)
    assert stored.cookies_encrypted not in (None, VALID_TEXT)
    assert decrypt_cookies(stored.cookies_encrypted) == VALID_TEXT

    # DELETE clears.
    resp = client.delete(
        f"/api/v1/sources/{source.id}/cookies", headers=stack.headers
    )
    assert resp.status_code == 200
    assert resp.json() == {"present": False, "updated_at": None}


def test_source_cookies_rejects_bad_format(db, client):
    stack = make_stack(db, client, code="CK11")
    source = make_source(db, stack.customer)
    resp = _upload(
        client,
        f"/api/v1/sources/{source.id}/cookies",
        "not cookies at all",
        stack.headers,
    )
    assert resp.status_code == 400


def test_source_cookies_rejects_oversize(db, client):
    stack = make_stack(db, client, code="CK12")
    source = make_source(db, stack.customer)
    big = ("a\tb\n" * ((cookies_mod.MAX_COOKIES_BYTES // 4) + 10)).encode()
    resp = client.put(
        f"/api/v1/sources/{source.id}/cookies",
        files={"file": ("cookies.txt", big, "text/plain")},
        headers=stack.headers,
    )
    assert resp.status_code == 413


def test_source_cookies_tenant_isolation(db, client):
    stack_a = make_stack(db, client, code="CK13")
    stack_b = make_stack(db, client, code="CK14")
    source_a = make_source(db, stack_a.customer)

    # B cannot read, write, or delete A's source cookies: 404 (not 403 — no
    # cross-tenant existence leak).
    assert (
        client.get(
            f"/api/v1/sources/{source_a.id}/cookies", headers=stack_b.headers
        ).status_code
        == 404
    )
    assert (
        _upload(
            client,
            f"/api/v1/sources/{source_a.id}/cookies",
            VALID_TEXT,
            stack_b.headers,
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/sources/{source_a.id}/cookies", headers=stack_b.headers
        ).status_code
        == 404
    )


def test_source_cookies_requires_auth(client):
    resp = client.get("/api/v1/sources/1/cookies")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# API: customer-global cookies
# ---------------------------------------------------------------------------


def test_customer_cookies_put_get_delete(db, client):
    stack = make_stack(db, client, code="CK20")

    resp = client.get("/api/v1/me/cookies", headers=stack.headers)
    assert resp.status_code == 200
    assert resp.json() == {"present": False, "updated_at": None}

    resp = _upload(client, "/api/v1/me/cookies", VALID_TEXT, stack.headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["present"] is True
    assert "abc123" not in resp.text  # never leak raw values

    resp = client.get("/api/v1/me/cookies", headers=stack.headers)
    assert resp.json()["present"] is True
    assert "abc123" not in resp.text

    resp = client.delete("/api/v1/me/cookies", headers=stack.headers)
    assert resp.status_code == 200
    assert resp.json() == {"present": False, "updated_at": None}


def test_customer_cookies_tenant_isolation(db, client):
    stack_a = make_stack(db, client, code="CK21")
    stack_b = make_stack(db, client, code="CK22")
    _upload(client, "/api/v1/me/cookies", VALID_TEXT, stack_a.headers)

    # B's own global cookies are untouched by A's upload.
    resp = client.get("/api/v1/me/cookies", headers=stack_b.headers)
    assert resp.json() == {"present": False, "updated_at": None}
