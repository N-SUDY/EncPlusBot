import json

from bot import asyncio, math, os, pyro, time
from bot.config import _bot, conf
from bot.utils.bot_utils import (
    get_aria2,
    get_queue,
    hbs,
    replace_proxy,
    sync_to_async,
    time_formatter,
)
from bot.utils.log_utils import log, logger


def clean_aria_dl(download):
    aria2 = get_aria2()
    download.remove(force=True, files=True)
    if download.following_id:
        download = aria2.get_download(download.following_id)
        download.remove(force=True, files=True)


def rm_leech_file(*gids):
    for gid in gids:
        try:
            if not gid:
                break
            aria2 = get_aria2()
            download = aria2.get_download(gid)
            download.remove(force=True, files=True)
            if download.followed_by_ids:
                download = aria2.get_download(download.followed_by_ids[0])
                download.remove(force=True, files=True)
        except Exception:
            log(Exception)


async def get_torrent_files(uri, aria2, dir_path="temp/list_probe", action="remove"):
    """
    Adds a torrent to aria2 without pausing to allow metadata resolution,
    waits for the file list, and then performs the specified action.
    """
    # aria2 requires absolute paths
    import os as _os
    if not _os.path.isabs(dir_path):
        dir_path = _os.path.join(_os.getcwd(), dir_path)
    _os.makedirs(dir_path, exist_ok=True)
    # Add without pausing
    downloads = await sync_to_async(aria2.add, uri, {"dir": dir_path})
    dl = await sync_to_async(aria2.get_download, downloads[0].gid)

    # Follow metadata to actual torrent
    for _ in range(60):
        dl = dl.live
        if dl.followed_by_ids:
            dl = await sync_to_async(aria2.get_download, dl.followed_by_ids[0])
        if dl.files and not dl.name.startswith("[METADATA]"):
            break
        await asyncio.sleep(1)

    flist = [str(f.path) for f in dl.files if f.path and not str(f.path).endswith("[METADATA]")]
    name = dl.name

    # Perform post-fetch action
    if action == "remove":
        await sync_to_async(dl.remove, force=True)
        try:
            # Try to clean up the metadata download too
            meta = await sync_to_async(aria2.get_download, downloads[0].gid)
            await sync_to_async(meta.remove, force=True)
        except Exception:
            pass
    elif action == "pause":
        await sync_to_async(dl.pause, force=True)

    return dl, flist, name


async def get_leech_name(url):
    from bot.utils.bot_utils import Qbit_c
    dinfo = Qbit_c()
    try:
        aria2 = get_aria2()
        if not aria2:
            # aria2 may still be starting up — try once to reconnect
            from bot.startup.after import start_aria2p
            await start_aria2p()
            aria2 = get_aria2()
        if not aria2:
            dinfo.error = "E404: Aria2 is not available. Check that ENABLE_ARIA2=True and aria2c is installed."
            return dinfo
        url = replace_proxy(url)
        downloads = await sync_to_async(aria2.add, url, {"dir": f"{os.getcwd()}/temp"})
        c_time = time.time()
        while True:
            download = await sync_to_async(aria2.get_download, downloads[0].gid)
            download = download.live
            if download.followed_by_ids:
                gid = download.followed_by_ids[0]
                download = await sync_to_async(aria2.get_download, gid)
            if time.time() - c_time > 300:
                dinfo.error = "E408: Getting filename timed out."
                break
            if download.status == "error":
                dinfo.error = "E" + download.error_code + ": " + download.error_message
                break
            if download.name.startswith("[METADATA]") or download.name.endswith(
                ".torrent"
            ):
                await asyncio.sleep(1)
                continue
            if not download.total_length and (
                not (ext := os.path.splitext(download.name)[1]) or "?" in ext
            ):
                await asyncio.sleep(1)
                continue
            if not download.bittorrent:
                file_path = str(download.files[0].path.absolute())
                dir_path = str(download.dir.absolute())
                if not file_path.startswith(dir_path):
                    await asyncio.sleep(1)
                    continue

            dinfo.name = download.name
            break
        await sync_to_async(clean_aria_dl, download)
    except Exception as e:
        dinfo.error = e
        await logger(Exception)
    finally:
        return dinfo


async def cache_dl(check=False, cached=False):
    if check:
        return _bot.cached
    if cached:
        _bot.cached = True
        return
    try:
        queue = get_queue()
        chat_id, msg_id = list(queue.keys())[1]
        filename, u_msg, v = list(queue.values())[1]
        dl = "downloads/" + filename
        user, msg = u_msg
        if not msg:
            msg = await pyro.get_messages(chat_id, msg_id)
        else:
            msg._client = pyro
        if msg.text:
            return
        media_type = str(msg.media)
        if media_type == "MessageMediaType.VIDEO":
            file = msg.video.file_id
        else:
            file = msg.document.file_id
        # download2 is defined in this same module — no import needed
        await download2(dl, file)
        _bot.cached = True
    except Exception:
        await logger(Exception)
        _bot.cached = False


async def download2(dl, file, message=None, e=None):
    try:
        if not message:
            return asyncio.create_task(
                pyro.download_media(
                    message=file,
                    file_name=dl,
                )
            )
        ttt = time.time()
        media_type = str(message.media)
        if media_type == "MessageMediaType.DOCUMENT":
            media_mssg = "`Downloading a file…`\n"
        else:
            media_mssg = "`Downloading a video…`\n"
        download_task = asyncio.create_task(
            pyro.download_media(
                message=message,
                file_name=dl,
                progress=progress_for_pyrogram,
                progress_args=(pyro, media_mssg, e, ttt),
            )
        )
        return download_task
    except Exception:
        await logger(Exception)


async def progress_for_pyrogram(current, total, bot, ud_type, message, start):
    now = time.time()
    diff = now - start
    if round(diff % 10.00) == 0 or current == total:
        percentage = current * 100 / total
        status = "downloads" + "/status.json"
        if os.path.exists(status):
            with open(status, "r+") as f:
                statusMsg = json.load(f)
                if not statusMsg["running"]:
                    bot.stop_transmission()
        speed = current / diff
        time_to_completion = time_formatter(int((total - current) / speed))
        progress = "{0}{1} \n<b>Progress:</b> {2}%\n".format(
            "".join(
                [conf.FINISHED_PROGRESS_STR for i in range(math.floor(percentage / 10))]
            ),
            "".join(
                [
                    conf.UN_FINISHED_PROGRESS_STR
                    for i in range(10 - math.floor(percentage / 10))
                ]
            ),
            round(percentage, 2),
        )

        tmp = progress + "{0} of {1}\nSpeed: {2}/s\nETA: {3}\n".format(
            hbs(current),
            hbs(total),
            hbs(speed),
            time_to_completion if time_to_completion else "0 s",
        )
        try:
            if not message.photo:
                await message.edit_text(text="{}\n {}".format(ud_type, tmp))
            else:
                await message.edit_caption(caption="{}\n {}".format(ud_type, tmp))
        except BaseException:
            pass
