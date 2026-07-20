"""
YouTube video/playlist leech support via yt-dlp.

Kept as its own module (separate from aria2's dl_helpers.py) since yt-dlp
has a completely different download/progress model — it manages its own
HTTP(S)/HLS/DASH downloads internally rather than going through aria2.

Design mirrors the existing aria2 batch-torrent flow so the rest of the
bot (queue.py, transcode.py) doesn't need special-casing:
  - get_yt_info(url)      ~= get_leech_name() / get_torrent_files()
  - start_yt_download(...) ~= download.start() for a single file
"""

import asyncio
import os
import re
import time

from bot.config import conf
from bot.utils.bot_utils import Qbit_c, sync_to_async
from bot.utils.log_utils import logger

YOUTUBE_URL_RE = re.compile(
    r"(https?://)?(www\.)?(youtube\.com|youtu\.be|m\.youtube\.com)/\S+",
    re.IGNORECASE,
)


def is_youtube_url(url: str) -> bool:
    return bool(url and YOUTUBE_URL_RE.match(url.strip()))


def _yt_dlp_available():
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


async def get_yt_info(url: str):
    """
    Resolve a YouTube URL (single video or playlist) into a Qbit_c-like
    info object, mirroring get_torrent_files()'s return shape so the
    /ytleech command can reuse the same batch-queue plumbing as /lb.

    Returns a Qbit_c with:
      - .name   -> video title (single) or playlist title (playlist)
      - .file_list -> list of "virtual" entries, one per video, each a
                      dict with 'url', 'title', 'id' (NOT real file paths
                      yet — those only exist after actual download)
      - .error  -> error string if resolution failed
    """
    info = Qbit_c()
    if not _yt_dlp_available():
        info.error = "E404: yt-dlp is not installed. Run: pip install yt-dlp"
        return info

    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",  # don't resolve every video's formats up front
        "skip_download": True,
        "socket_timeout": 30,
    }

    def _extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        data = await sync_to_async(_extract)
    except Exception as e:
        info.error = f"E: Could not resolve YouTube URL — {e}"
        return info

    if not data:
        info.error = "E: yt-dlp returned no data for this URL."
        return info

    entries = data.get("entries")
    if entries:
        # Playlist: entries may be flat (extract_flat) — each has 'url'/'id'/'title'
        video_list = []
        for e in entries:
            if not e:
                continue
            vid = e.get("id")
            title = e.get("title") or vid
            video_list.append(
                {
                    "id": vid,
                    "title": title,
                    "url": e.get("url") or f"https://www.youtube.com/watch?v={vid}",
                }
            )
        if not video_list:
            info.error = "E: Playlist is empty or all entries are unavailable."
            return info
        info.name = data.get("title") or "YouTube Playlist"
        info.file_list = video_list
    else:
        # Single video
        vid = data.get("id")
        title = data.get("title") or vid
        info.name = title
        info.file_list = [
            {"id": vid, "title": title, "url": data.get("webpage_url") or url}
        ]

    return info


def _sanitize_filename(name: str) -> str:
    # Strip characters that break filesystems / ffmpeg shell quoting
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name.strip()[:200] or "video"


async def start_yt_download(
    url: str,
    title: str,
    dl_folder: str = "downloads/",
    progress_cb=None,
    quality: str = None,
):
    """
    Downloads a single YouTube video with yt-dlp. Returns the absolute
    path of the downloaded file, or raises Exception on failure.

    quality: yt-dlp format selector override, e.g. "best[height<=1080]".
             Defaults to conf.YT_DLP_FORMAT if not given.
    """
    if not _yt_dlp_available():
        raise Exception("yt-dlp is not installed. Run: pip install yt-dlp")

    import yt_dlp

    os.makedirs(dl_folder, exist_ok=True)
    safe_title = _sanitize_filename(title)
    out_template = os.path.join(os.getcwd(), dl_folder, f"{safe_title}.%(ext)s")

    fmt = quality or conf.YT_DLP_FORMAT

    result_path = {}

    def _hook(d):
        if d.get("status") == "downloading" and progress_cb:
            try:
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                speed = d.get("speed") or 0
                eta = d.get("eta") or 0
                progress_cb(downloaded, total, speed, eta)
            except Exception:
                pass
        elif d.get("status") == "finished":
            result_path["path"] = d.get("filename")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": fmt,
        "outtmpl": out_template,
        "merge_output_format": "mkv",
        "progress_hooks": [_hook],
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": conf.YT_DLP_CONCURRENT_FRAGMENTS,
        "noplaylist": True,  # safety: only download the single video, playlist handled by caller
    }

    def _download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

    await sync_to_async(_download)

    path = result_path.get("path")
    if not path or not os.path.isfile(path):
        # yt-dlp sometimes reports pre-merge filename; try common extensions
        for ext in ("mkv", "mp4", "webm"):
            candidate = os.path.join(os.getcwd(), dl_folder, f"{safe_title}.{ext}")
            if os.path.isfile(candidate):
                path = candidate
                break

    if not path or not os.path.isfile(path):
        raise Exception("yt-dlp finished but output file could not be located.")

    return os.path.abspath(path)
