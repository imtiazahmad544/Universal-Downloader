"""Provider registry: ordered discovery/download provider lists.

Order matters for discovery fallback: the discovery worker tries providers
in registry order for the source's platform and moves to the next one only
on UNSUPPORTED. RATE_LIMITED and error results fail the run immediately —
providers are never rotated to dodge limits.
"""

from app.providers.base import (
    DiscoveredMedia,
    DiscoveryProvider,
    DiscoveryResult,
    DownloadProvider,
    DownloadResult,
    MediaInfo,
    ProgressCallback,
    ProviderResultType,
    compute_backoff,
)
from app.providers.instagram import (
    InstagramDiscoveryProvider,
    InstagramDownloadProvider,
)
from app.providers.tiktok import TikTokDiscoveryProvider, TikTokDownloadProvider
from app.providers.youtube import YouTubeDiscoveryProvider, YouTubeDownloadProvider


def get_discovery_providers() -> list[DiscoveryProvider]:
    """Ordered discovery providers for the discovery worker."""
    return [
        YouTubeDiscoveryProvider(),
        TikTokDiscoveryProvider(),
        InstagramDiscoveryProvider(),
    ]


def get_download_providers() -> list[DownloadProvider]:
    """Ordered download providers."""
    return [
        YouTubeDownloadProvider(),
        TikTokDownloadProvider(),
        InstagramDownloadProvider(),
    ]


__all__ = [
    "DiscoveredMedia",
    "DiscoveryProvider",
    "DiscoveryResult",
    "DownloadProvider",
    "DownloadResult",
    "MediaInfo",
    "ProgressCallback",
    "ProviderResultType",
    "compute_backoff",
    "YouTubeDiscoveryProvider",
    "YouTubeDownloadProvider",
    "TikTokDiscoveryProvider",
    "TikTokDownloadProvider",
    "InstagramDiscoveryProvider",
    "InstagramDownloadProvider",
    "get_discovery_providers",
    "get_download_providers",
]
