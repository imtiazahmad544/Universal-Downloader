"""URL and source canonicalization tests."""

import pytest

from app.core.canonicalize import canonicalize_source, canonicalize_url


# ---------------------------------------------------------------------------
# canonicalize_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Scheme defaults to https, host lowercased, query+fragment dropped,
        # trailing slash stripped.
        ("HTTP://Example.COM/Path/?a=1#frag", "http://example.com/Path"),
        ("https://EXAMPLE.com/x?utm_source=share&igsh=1", "https://example.com/x"),
        ("https://example.com/a/b/", "https://example.com/a/b"),
        # Missing scheme defaults to https.
        ("example.com", "https://example.com/"),
        ("example.com/some/path", "https://example.com/some/path"),
        # Default ports dropped, non-default kept.
        ("http://example.com:80/", "http://example.com/"),
        ("https://example.com:443/x", "https://example.com/x"),
        ("http://example.com:8080/x", "http://example.com:8080/x"),
        # Bare root normalizes to "/".
        ("https://example.com", "https://example.com/"),
        # Path case is preserved (only the host is lowercased).
        ("https://example.com/MixedCase", "https://example.com/MixedCase"),
    ],
)
def test_canonicalize_url(raw, expected):
    assert canonicalize_url(raw) == expected


def test_canonicalize_url_empty_raises():
    with pytest.raises(ValueError):
        canonicalize_url("")
    with pytest.raises(ValueError):
        canonicalize_url("   ")


def test_canonicalize_url_unparseable_host_raises():
    with pytest.raises(ValueError):
        canonicalize_url("not a url at all")


# ---------------------------------------------------------------------------
# canonicalize_source: tiktok
# ---------------------------------------------------------------------------


def test_tiktok_at_handle():
    assert canonicalize_source("tiktok", "@SomeUser") == "tiktok:someuser"


def test_tiktok_bare_name():
    assert canonicalize_source("tiktok", "someuser") == "tiktok:someuser"


def test_tiktok_url():
    assert (
        canonicalize_source("tiktok", "https://www.tiktok.com/@SomeUser/video/123")
        == "tiktok:someuser"
    )


def test_tiktok_wrong_host_raises():
    with pytest.raises(ValueError):
        canonicalize_source("tiktok", "https://example.com/@user")


# ---------------------------------------------------------------------------
# canonicalize_source: instagram
# ---------------------------------------------------------------------------


def test_instagram_url():
    assert (
        canonicalize_source("instagram", "https://instagram.com/Some.Name/")
        == "instagram:some.name"
    )


def test_instagram_at_handle():
    assert canonicalize_source("instagram", "@name") == "instagram:name"


def test_instagram_wrong_host_raises():
    with pytest.raises(ValueError):
        canonicalize_source("instagram", "https://tiktok.com/@user")


# ---------------------------------------------------------------------------
# canonicalize_source: youtube
# ---------------------------------------------------------------------------


def test_youtube_at_handle_lowercased():
    assert canonicalize_source("youtube", "@MyHandle") == "youtube:@myhandle"


def test_youtube_url_at_handle():
    assert (
        canonicalize_source("youtube", "https://www.youtube.com/@MyHandle")
        == "youtube:@myhandle"
    )


def test_youtube_channel_id_case_preserved():
    # Channel IDs are case-SENSITIVE: never lowercased.
    assert (
        canonicalize_source("youtube", "https://www.youtube.com/channel/UCaBcDeF123")
        == "youtube:channel:UCaBcDeF123"
    )


def test_youtube_c_path():
    assert (
        canonicalize_source("youtube", "https://www.youtube.com/c/SomeChannel")
        == "youtube:c:somechannel"
    )


def test_youtube_user_path():
    assert (
        canonicalize_source("youtube", "https://www.youtube.com/user/OldName")
        == "youtube:c:oldname"
    )


def test_youtube_watch_url_falls_back_to_canonical_url():
    assert (
        canonicalize_source("youtube", "https://www.youtube.com/watch?v=abc123&t=10s")
        == "youtube:url:https://www.youtube.com/watch"
    )


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


def test_garbage_raises_value_error():
    with pytest.raises(ValueError):
        canonicalize_source("youtube", "just some text")


def test_unsupported_platform_raises():
    with pytest.raises(ValueError):
        canonicalize_source("vimeo", "@user")


def test_empty_input_raises():
    with pytest.raises(ValueError):
        canonicalize_source("tiktok", "   ")
