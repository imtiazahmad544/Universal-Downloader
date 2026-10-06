"""TikTok provider stubs.

Deliberately UNIMPLEMENTED: there is no scraping, no unofficial-API use, and
no rate-limit circumvention anywhere in this project. TikTok discovery and
downloads will be enabled only through a legitimate official API integration
authenticated with TIKTOK_API_KEY / TIKTOK_API_SECRET (see .env.example).

Until such an integration is configured, every entry point reports
UNSUPPORTED so the discovery worker falls through cleanly.
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
)

_NOT_IMPLEMENTED = (
    "TikTok adapter not implemented: configure TIKTOK_API_KEY/TIKTOK_API_SECRET "
    "for a legitimate official API integration (see .env.example)"
)


class TikTokDiscoveryProvider(DiscoveryProvider):
    name = "tiktok-official"
    supported_platforms = {"tiktok"}

    async def discover(self, source, cookies: str | None = None) -> DiscoveryResult:
        return DiscoveryResult(ProviderResultType.UNSUPPORTED, error=_NOT_IMPLEMENTED)


class TikTokDownloadProvider(DownloadProvider):
    name = "tiktok-official"

    async def inspect(self, media_url: str, cookies: str | None = None) -> MediaInfo:
        raise RuntimeError(_NOT_IMPLEMENTED)

    async def download(
        self,
        media: DiscoveredMedia,
        destination: str,
        progress_callback: ProgressCallback | None,
        cookies: str | None = None,
    ) -> DownloadResult:
        return DownloadResult(ProviderResultType.UNSUPPORTED, error=_NOT_IMPLEMENTED)
