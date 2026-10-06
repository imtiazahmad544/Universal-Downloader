"""Provider plugin contracts: discovery and download.

Concrete providers (tiktok/instagram/youtube implementations) are built by a
later workstream against these ABCs. Results use ProviderResultType string
constants so schedulers can branch without importing provider internals.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.models import Source


class ProviderResultType:
    SUCCESS = "success"
    RETRYABLE_ERROR = "retryable_error"
    UNSUPPORTED = "unsupported"
    RATE_LIMITED = "rate_limited"
    NON_RETRYABLE_ERROR = "non_retryable_error"
    CAPTCHA = "captcha"  # v2.0: captcha/bot-check wall; upload cookies and retry


@dataclass
class DiscoveredMedia:
    url: str
    external_id: str | None = None
    title: str | None = None
    media_type: str | None = None


@dataclass
class DiscoveryResult:
    result_type: str
    items: list[DiscoveredMedia] = field(default_factory=list)
    error: str | None = None
    retry_after: float | None = None  # seconds; set on RATE_LIMITED


@dataclass
class MediaInfo:
    url: str
    title: str | None = None
    ext: str | None = None
    filesize: int | None = None


@dataclass
class DownloadResult:
    result_type: str
    file_path: str | None = None
    size: int | None = None
    checksum: str | None = None
    error: str | None = None
    retry_after: float | None = None  # seconds; set on RATE_LIMITED
    captcha: bool = False  # v2.0: True when the failure was a captcha/bot-check wall


# progress_callback(downloaded_bytes, total_bytes_or_None)
ProgressCallback = Callable[[int, "int | None"], None]


class DiscoveryProvider(ABC):
    name: str
    supported_platforms: set[str]

    @abstractmethod
    async def discover(
        self, source: "Source", cookies: str | None = None
    ) -> DiscoveryResult:
        """Discover media items for a source. Must not raise for expected
        failures — encode them in DiscoveryResult.result_type instead.

        `cookies` is optional raw Netscape cookies text supplied by the
        customer (v2.0); None means unauthenticated extraction."""


class DownloadProvider(ABC):
    name: str

    @abstractmethod
    async def inspect(self, media_url: str, cookies: str | None = None) -> MediaInfo:
        """Probe a media URL for metadata without downloading."""

    @abstractmethod
    async def download(
        self,
        media: DiscoveredMedia,
        destination: str,
        progress_callback: ProgressCallback | None,
        cookies: str | None = None,
    ) -> DownloadResult:
        """Download media to `destination`, reporting progress via the callback."""


def compute_backoff(attempt: int, base: float = 2.0, cap: float = 300.0) -> float:
    """Exponential backoff: min(cap, base * 2**attempt). Pure function."""
    return min(cap, base * (2**attempt))
