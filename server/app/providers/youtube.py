"""YouTube provider adapters backed by the optional yt-dlp dependency.

Compliance (by design, not by request):
    - Public metadata only. The adapters never authenticate, never inject
      cookies or credentials, and never bypass access controls.
    - No rate-limit circumvention. Rate-limit signals are classified as
      RATE_LIMITED so the discovery worker fails the run (and stops
      provider rotation) instead of trying to dodge the limit.

yt-dlp is OPTIONAL: when it is not installed every entry point reports
UNSUPPORTED with an install hint instead of raising ImportError.
"""

import asyncio
import hashlib
import os

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

_YTDLP_MISSING = (
    "yt-dlp not installed; install the optional yt-dlp dependency "
    "to enable YouTube discovery"
)
_YTDLP_MISSING_DOWNLOAD = (
    "yt-dlp not installed; install the optional yt-dlp dependency "
    "to enable YouTube downloads"
)


def _source_url(input_value: str) -> str:
    value = (input_value or "").strip()
    if value.startswith("http"):
        return value
    # Handles ('@handle') and bare channel/user names land on the canonical
    # youtube.com path; yt-dlp resolves the rest.
    return f"https://www.youtube.com/{value}"


def _classify_ydl_error(exc: Exception) -> DiscoveryResult:
    """Map a yt-dlp exception to a DiscoveryResult without raising."""
    msg = str(exc).lower()
    if "429" in msg or "too many requests" in msg or "rate" in msg:
        return DiscoveryResult(
            ProviderResultType.RATE_LIMITED, error=str(exc), retry_after=None
        )
    type_name = type(exc).__name__.lower()
    if (
        isinstance(exc, TimeoutError)
        or "timeout" in msg
        or "timed out" in msg
        or "urlerror" in type_name
        or "connection" in msg
    ):
        return DiscoveryResult(ProviderResultType.RETRYABLE_ERROR, error=str(exc))
    return DiscoveryResult(ProviderResultType.NON_RETRYABLE_ERROR, error=str(exc))


class YouTubeDiscoveryProvider(DiscoveryProvider):
    """Discover public videos from a YouTube channel/handle/playlist URL via
    yt-dlp flat extraction (metadata only, no downloads)."""

    name = "youtube-ytdlp"
    supported_platforms = {"youtube"}

    async def discover(self, source) -> DiscoveryResult:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            return DiscoveryResult(ProviderResultType.UNSUPPORTED, error=_YTDLP_MISSING)
        # yt-dlp is blocking: keep it off the event loop.
        return await asyncio.to_thread(self._discover_sync, source)

    def _discover_sync(self, source) -> DiscoveryResult:
        import yt_dlp

        ydl_opts = {
            "extract_flat": True,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
        }
        url = _source_url(source.input_value)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # yt-dlp raises many concrete error types
            return _classify_ydl_error(exc)
        entries = info.get("entries") or []
        items: list[DiscoveredMedia] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            video_id = entry.get("id")
            if not video_id:
                continue
            items.append(
                DiscoveredMedia(
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    external_id=str(video_id),
                    title=entry.get("title"),
                    media_type="video",
                )
            )
        return DiscoveryResult(ProviderResultType.SUCCESS, items=items)


class YouTubeDownloadProvider(DownloadProvider):
    """Download public YouTube media via yt-dlp. No auth, no cookie use."""

    name = "youtube-ytdlp"

    async def inspect(self, media_url: str) -> MediaInfo:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            raise RuntimeError(_YTDLP_MISSING_DOWNLOAD)
        return await asyncio.to_thread(self._inspect_sync, media_url)

    def _inspect_sync(self, media_url: str) -> MediaInfo:
        import yt_dlp

        ydl_opts = {"skip_download": True, "quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(media_url, download=False)
        if not info:
            raise RuntimeError(f"yt-dlp returned no metadata for {media_url!r}")
        return MediaInfo(
            url=media_url,
            title=info.get("title"),
            ext=info.get("ext"),
            filesize=info.get("filesize"),
        )

    async def download(
        self,
        media: DiscoveredMedia,
        destination: str,
        progress_callback: ProgressCallback | None,
    ) -> DownloadResult:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            return DownloadResult(
                ProviderResultType.UNSUPPORTED, error=_YTDLP_MISSING_DOWNLOAD
            )
        return await asyncio.to_thread(
            self._download_sync, media, destination, progress_callback
        )

    def _download_sync(
        self,
        media: DiscoveredMedia,
        destination: str,
        progress_callback: ProgressCallback | None,
    ) -> DownloadResult:
        import yt_dlp

        os.makedirs(destination, exist_ok=True)
        hooks = []
        if progress_callback is not None:
            def _hook(status: dict) -> None:
                if status.get("status") == "downloading":
                    downloaded = status.get("downloaded_bytes") or 0
                    total = status.get("total_bytes") or status.get(
                        "total_bytes_estimate"
                    )
                    progress_callback(downloaded, total)

            hooks.append(_hook)
        ydl_opts = {
            "outtmpl": os.path.join(destination, "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": hooks,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(media.url, download=True)
                if not info:
                    return DownloadResult(
                        ProviderResultType.NON_RETRYABLE_ERROR,
                        error=f"yt-dlp downloaded nothing for {media.url!r}",
                    )
                file_path = ydl.prepare_filename(info)
        except Exception as exc:
            return DownloadResult(
                _classify_download_error(exc), error=str(exc)
            )
        if not os.path.isfile(file_path):
            return DownloadResult(
                ProviderResultType.NON_RETRYABLE_ERROR,
                error=f"expected output file missing after download: {file_path}",
            )
        checksum, size = _sha256_file(file_path)
        return DownloadResult(
            ProviderResultType.SUCCESS,
            file_path=file_path,
            size=size,
            checksum=checksum,
        )


def _classify_download_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "429" in msg or "too many requests" in msg or "rate" in msg:
        return ProviderResultType.RATE_LIMITED
    if isinstance(exc, TimeoutError) or "timeout" in msg or "connection" in msg:
        return ProviderResultType.RETRYABLE_ERROR
    return ProviderResultType.NON_RETRYABLE_ERROR


def _sha256_file(path: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size
