import hashlib

from bot import *
from bot.config import _bot, conf
from bot.fun.emojis import enhearts, enmoji, enmoji2
from bot.utils.bot_utils import (
    code,
    decode,
    get_aria2,
    hbs,
    replace_proxy,
    sync_to_async,
    time_formatter,
    value_check,
)
from bot.utils.log_utils import log, logger
from bot.utils.os_utils import parse_dl, s_remove

from .dl_helpers import (
    rm_leech_file,
)

# ponytail: a batch is one torrent shared by many episodes. Previously every
# episode's Downloader called aria2.add() again for the exact same magnet/
# torrent URI, which made aria2 spin up a brand-new duplicate download each
# time — visibly "restarting" the batch from scratch after every episode,
# and flooding the RPC connection with redundant metadata resolution (which
# is also what was tripping the E409 "lost contact" guard). Track the one
# gid per torrent URI here and hand the same in-progress/completed aria2
# download to every episode of the batch instead of re-adding it.
_BATCH_TORRENT_GIDS = {}


def _uri_key(uri):
    uri_s = uri if isinstance(uri, str) else str(uri)
    return hashlib.sha1(uri_s.encode()).hexdigest()


async def release_batch_torrent(uri):
    """Remove a batch's shared aria2 torrent and forget its gid.

    Call this once the whole batch (every episode) has been processed —
    not per-episode — since the same aria2 download is reused across all
    of a batch's episodes.
    """
    if not uri:
        return
    key = _uri_key(uri)
    gid = _BATCH_TORRENT_GIDS.pop(key, None)
    if gid:
        try:
            await sync_to_async(rm_leech_file, gid)
        except Exception:
            log(Exception)


class Downloader:
    def __init__(
        self,
        sender=123456,
        lc=None,
        _id=None,
        uri=False,
        dl_info=False,
        folder="downloads/",
    ):
        self.sender = int(sender)
        self.callback_data = "cancel_download"
        self.is_cancelled = False
        self.canceller = None
        self.dl_info = dl_info
        self.download_error = None
        self.file_name = None
        self.message = None
        self.dl_folder = folder
        self.id = _id
        self.uri = replace_proxy(uri)
        self.uri_gid = None
        self.lc = lc
        self.lm = None
        self.log_id = None
        self._sender = None
        self.time = None
        self.aria2 = get_aria2()
        self.path = None
        self.unfin_str = conf.UN_FINISHED_PROGRESS_STR
        self.display_dl_info = _bot.display_additional_dl_info
        if conf.PAUSE_ON_DL_INFO:
            self.pause_on_dl_info = True
        else:
            self.pause_on_dl_info = False
        if self.dl_info:
            self.callback_data_i = "dl_info"
            self.callback_data_b = "back"

    def __str__(self):
        return "#wip"

    def gen_buttons(self):
        # Create a "Cancel" button
        cancel_button = InlineKeyboardButton(
            text=f"{enmoji()} Cancel Download", callback_data=self.callback_data
        )
        if self.dl_info:
            # Create an "info" button
            info_button = InlineKeyboardButton(
                text="ℹ️", callback_data=self.callback_data_i
            )
            # Create a "more" button
            more_button = InlineKeyboardButton(
                text="More…",
                callback_data=f"more 0",
            )
            # create "back" button
            back_button = InlineKeyboardButton(
                text="↩️", callback_data=self.callback_data_b
            )
        else:
            info_button, more_button, back_button = None, None, None
        return info_button, more_button, back_button, cancel_button

    async def log_download(self):
        if self.lc:
            try:
                cancel_button = InlineKeyboardButton(
                    text=f"{enmoji()} CANCEL DOWNLOAD", callback_data=self.callback_data
                )
                more_button = InlineKeyboardButton(
                    text="ℹ️",
                    callback_data=f"more 1",
                )
                reply_markup = InlineKeyboardMarkup([[more_button], [cancel_button]])
                dl_info = await parse_dl(self.file_name)
                msg = "Currently downloading a video"
                if self.uri:
                    msg += " from a link"
                message = await pyro.get_messages(self.lc.chat_id, self.lc.id)
                self._sender = self._sender or await pyro.get_users(self.sender)
                log = await message.edit(
                    f"`{msg} sent by` {self._sender.mention(style='md')}\n" + dl_info,
                    reply_markup=reply_markup,
                )
                self.lm = message
            except Exception:
                await logger(Exception)

    async def start(self, dl, file, message="", e="", select=None, target_path=None):
        try:
            self.file_name = dl
            self.register()
            if self.uri:
                return await self.start2(dl, file, message, e, select=select, target_path=target_path)
            await self.log_download()
            if self.dl_folder:
                self.path = dl = self.dl_folder + dl
            if message:
                self.time = ttt = time.time()
                media_type = str(message.media)
                if media_type == "MessageMediaType.DOCUMENT":
                    media_mssg = "`Downloading a file…`"
                else:
                    media_mssg = "`Downloading a video…`"
                download_task = await pyro.download_media(
                    message=message,
                    file_name=dl,
                    progress=self.progress_for_pyrogram,
                    progress_args=(pyro, media_mssg, e, ttt),
                )
            else:
                download_task = await pyro.download_media(
                    message=file,
                    file_name=dl,
                )
            await self.wait()
            if self.is_cancelled:
                await self.clean_download()
            self.un_register()
            return download_task

        except pyro_errors.BadRequest:
            await reply.edit(f"`Failed {enmoji2()}\nRetrying in 10 seconds…`")
            await asyncio.sleep(10)
            dl_task = await self.start(dl, file, message, e)
            return dl_task

        except pyro_errors.FloodWait as e:
            await asyncio.sleep(e.value)
            await reply.edit(
                f"`Failed: FloodWait error {enmoji2()}\nRetrying in 10 seconds…`"
            )
            await asyncio.sleep(10)
            dl_task = await self.start(dl, file, message, e)
            return dl_task

        except Exception:
            self.un_register()
            await logger(Exception)
            return None

    async def _episode_fail(self, msg, target_path=None):
        """Report a failure for *this episode's* download attempt.

        For a normal (non-batch) download this is terminal, so it behaves
        like clean_download() always did: remove the aria2 task/files.

        For a batch episode, the aria2 download is the *whole torrent*,
        shared by every other episode still waiting in the batch. A
        transient hiccup (RPC connection blip, this one episode taking
        longer than its own timeout to settle) used to call clean_download()
        unconditionally, which removed the entire torrent — killing every
        other episode's progress too, and forcing the next attempt (and
        the next episode) to re-add and redownload the whole thing from
        scratch. That's what produced "restarts downloading again after
        each episode / after this error". We now just fail this attempt
        and leave the shared torrent alone; the batch retry logic in
        transcode.py will try this episode again against the same
        in-progress torrent.
        """
        self.download_error = msg
        if not target_path:
            return await self.clean_download()
        return None

    async def start2(self, dl, file, message, e, select=None, target_path=None):
        try:
            await self.log_download()
            self.time = ttt = time.time()
            await asyncio.sleep(3)
            if not self.aria2:
                from bot.startup.after import start_aria2p
                await start_aria2p()
                self.aria2 = get_aria2()
            if not self.aria2:
                self.download_error = "E404: Aria2 is not available. Check ENABLE_ARIA2=True and that aria2c is installed."
                raise Exception(self.download_error)
            options = {"dir": f"{os.getcwd()}/{self.dl_folder}"}
            uri_for_name = self.uri if isinstance(self.uri, str) else str(self.uri)
            uri_key = _uri_key(uri_for_name)

            download = None
            if target_path:
                # Batch mode: try to reuse the torrent already added by an
                # earlier episode in this same batch instead of adding it
                # again.
                existing_gid = _BATCH_TORRENT_GIDS.get(uri_key)
                if existing_gid:
                    try:
                        candidate = await sync_to_async(
                            self.aria2.get_download, existing_gid
                        )
                        if candidate.status not in ("error", "removed"):
                            download = candidate
                            self.uri_gid = existing_gid
                    except Exception:
                        download = None

            if download is None:
                # E36 fix: aria2 names its metadata/control files (.aria2, .torrent,
                # the initial "[METADATA]..." placeholder) directly after the
                # torrent's display name, with no length cap of its own. Long
                # anime-release titles (100+ chars, common with multi-line/aka
                # titles) blow past the filesystem's ~255 byte filename limit and
                # aria2 fails with "File name too long" before it ever resolves
                # real per-file metadata. Individual files inside a multi-file
                # torrent are fine — it's only the umbrella/top-level name that's
                # unbounded — so give aria2 a short synthetic "out" name for that
                # top-level entry. This does not rename the actual leeched files.
                safe_out = "torrent_" + uri_key[:16]
                options["out"] = safe_out
                downloads = await sync_to_async(
                    self.aria2.add, self.uri, options
                )
                self.uri_gid = downloads[0].gid
                download = await sync_to_async(self.aria2.get_download, self.uri_gid)
                if target_path:
                    _BATCH_TORRENT_GIDS[uri_key] = self.uri_gid

            # ponytail: batch mode — target_path is the exact file we need on disk.
            # Poll until that file reaches its expected size. No index guessing,
            # no is_complete on the whole torrent — just watch the one file we care about.
            if target_path:
                # Step 1: wait for metadata so we can get expected_size for target_path.
                # This used to be a plain "for _ in range(120): ... sleep(1)" loop,
                # which assumed each iteration takes ~1s. That assumption breaks if
                # the aria2 RPC call itself stalls (e.g. daemon wedged after a disk
                # error) — a single get_download() call could block for up to the
                # client's RPC timeout, silently blowing the intended ~120s budget
                # many times over and leaving /lb stuck at "Waiting for download
                # handler…" indefinitely. We now track a real wall-clock deadline
                # and treat repeated RPC failures as a hard error instead of
                # looping forever.
                expected_size = 0
                target_basename = os.path.basename(target_path)
                _meta_deadline = time.time() + 120
                _rpc_fail_streak = 0
                while time.time() < _meta_deadline:
                    try:
                        dl_live = download.live
                        real_dl = dl_live
                        if dl_live.followed_by_ids:
                            real_dl = await sync_to_async(
                                self.aria2.get_download, dl_live.followed_by_ids[0]
                            )
                        _rpc_fail_streak = 0
                    except Exception:
                        _rpc_fail_streak += 1
                        if _rpc_fail_streak >= 3:
                            return await self._episode_fail(
                                "E409: Lost contact with aria2 RPC while resolving "
                                "torrent metadata.",
                                target_path,
                            )
                        await asyncio.sleep(2)
                        continue
                    if real_dl.files and not real_dl.name.startswith("[METADATA]"):
                        self.file_name = real_dl.name
                        self.path = self.dl_folder + real_dl.name
                        # match by basename — immune to sort-order differences
                        for f in real_dl.files:
                            if os.path.basename(str(f.path)) == target_basename:
                                expected_size = f.length
                                break
                        break
                    if real_dl.status == "error":
                        self.download_error = f"E{real_dl.error_code}: {real_dl.error_message}"
                        return await self.clean_download()
                    if self.is_cancelled:
                        return await self.clean_download()
                    await asyncio.sleep(1)

                if expected_size == 0:
                    return await self._episode_fail(
                        "E408: Could not resolve file size from torrent metadata "
                        "(timed out after 120s).",
                        target_path,
                    )

                # Step 2: poll aria2's own per-file completedLength for the target
                # file, instead of trusting the file's size on disk.
                #
                # Root cause of both the "first episode is corrupted / scenes skip
                # by 10-30s" and the "progress jumps straight from 0% to 100% on
                # large batch files" bugs: aria2 pre-allocates each file to its
                # *full* length on disk as soon as the download starts (sparse or
                # zero-filled), so `os.path.getsize(target_path) >= expected_size`
                # is true almost immediately — long before the file's actual bytes
                # have been written. And because BitTorrent pieces for a given file
                # can land in any order across the life of the whole torrent, a
                # "peek the first few bytes" check can pass (the start of the file
                # happened to arrive early) while large stretches later in the file
                # are still untouched zero-filled placeholder bytes. Handing that
                # file to the encoder produces exactly the symptom reported: gaps
                # where the zero-filled stretches are, and — since the size check
                # is satisfied almost instantly for a big file — there's nothing
                # gradual to show, so it looks like the download jumped straight
                # from 0% to 100%.
                #
                # The fix is to stop inferring completeness from disk state and
                # ask aria2 directly: aria2.getFiles() (wrapped by aria2p's
                # `File.completed_length`) reports, per file, the length of pieces
                # that have actually been fully downloaded and verified — not the
                # pre-allocated size. Only trust the file once that number reaches
                # the file's real length, then require it to stay put across a
                # short settle window (aria2 can still be flushing/hash-checking
                # trailing pieces) before handing it to the encoder.
                _dl_timeout = conf.ADL_TIMEOUT or 86400
                _settle_checks = 0
                _SETTLE_REQUIRED = 2  # consecutive stable checks, 5s apart == 10s settle
                _SETTLE_INTERVAL = 5
                _rpc_fail_streak = 0
                while True:
                    if self.is_cancelled:
                        return await self.clean_download()
                    if _dl_timeout and time.time() - ttt > _dl_timeout:
                        return await self._episode_fail(
                            "E28: Download timed out.", target_path
                        )

                    try:
                        dl_live = download.live
                        real_dl = dl_live
                        if dl_live.followed_by_ids:
                            real_dl = await sync_to_async(
                                self.aria2.get_download, dl_live.followed_by_ids[0]
                            )
                        _rpc_fail_streak = 0
                    except Exception:
                        _rpc_fail_streak += 1
                        if _rpc_fail_streak >= 3:
                            return await self._episode_fail(
                                "E409: Lost contact with aria2 RPC while polling "
                                "download progress.",
                                target_path,
                            )
                        await asyncio.sleep(2)
                        continue

                    if real_dl.status == "error":
                        self.download_error = f"E{real_dl.error_code}: {real_dl.error_message}"
                        return await self.clean_download()

                    target_file = None
                    for f in real_dl.files:
                        if os.path.basename(str(f.path)) == target_basename:
                            target_file = f
                            break

                    ready = bool(
                        target_file
                        and target_file.completed_length >= expected_size
                        and os.path.isfile(target_path)
                        and os.path.getsize(target_path) >= expected_size
                    )

                    if ready:
                        _settle_checks += 1
                        if _settle_checks >= _SETTLE_REQUIRED:
                            break
                        await asyncio.sleep(_SETTLE_INTERVAL)
                        continue
                    else:
                        _settle_checks = 0
                    if message:
                        await self.progress_for_aria2(download, ttt, e)
                    await asyncio.sleep(10)

                # Final settle delay: give aria2 time to fully close/flush the file
                # handle and finish any trailing disk writes before handing the file
                # off to the encoder. This is the fix for the "first episode of a
                # batch isn't encoded properly" issue.
                await asyncio.sleep(10)

                await self.wait()
                self.un_register()
                return download

            # Non-batch: wait for the whole download to complete
            while True:
                if message:
                    download = await self.progress_for_aria2(download, ttt, e)
                else:
                    download = await self.progress_for_aria2(
                        downloads[0].gid, ttt, e, silent=True
                    )
                if not download:
                    break
                if download.is_complete:
                    break
            await self.wait()
            self.un_register()
            return download

        except Exception:
            self.un_register()
            await logger(Exception)
            return None

    async def progress_for_pyrogram(self, current, total, app, ud_type, message, start):
        fin_str = enhearts()
        now = time.time()
        diff = now - start
        if self.is_cancelled:
            app.stop_transmission()
        if round(diff % 10.00) == 0 or current == total:
            percentage = current * 100 / total
            status = "downloads" + "/status.json"
            if os.path.exists(status):
                with open(status, "r+") as f:
                    statusMsg = json.load(f)
                    if not statusMsg["running"]:
                        app.stop_transmission()
            elapsed_time = time_formatter(diff)
            speed = current / diff
            time_to_completion = time_formatter(int((total - current) / speed))

            progress = "```\n{0}{1}```\n<b>Progress:</b> `{2}%`\n".format(
                "".join([fin_str for i in range(math.floor(percentage / 10))]),
                "".join(
                    [self.unfin_str for i in range(10 - math.floor(percentage / 10))]
                ),
                round(percentage, 2),
            )

            tmp = (
                progress
                + "`{0} of {1}`\n**Speed:** `{2}/s`\n**ETA:** `{3}`\n**Elapsed:** `{4}`\n".format(
                    hbs(current),
                    hbs(total),
                    hbs(speed),
                    time_to_completion if time_to_completion else "0 s",
                    elapsed_time if elapsed_time != "" else "0 s",
                )
            )
            try:
                # Attach the button to the message with an inline keyboard
                reply_markup = []
                # file_name = self.file_name.split("/")[-1]
                dl_info = await parse_dl(self.file_name)
                (
                    info_button,
                    more_button,
                    back_button,
                    cancel_button,
                ) = self.gen_buttons()
                if not self.dl_info:
                    reply_markup.append([cancel_button])
                    dsp = "{}\n{}".format(ud_type, tmp)
                elif not self.display_dl_info:
                    reply_markup.extend(([info_button], [cancel_button]))
                    dsp = "{}\n{}".format(ud_type, tmp)
                else:
                    reply_markup.extend(([more_button], [back_button], [cancel_button]))
                    dsp = dl_info
                reply_markup = InlineKeyboardMarkup(reply_markup)
                if not message.photo:
                    self.message = await message.edit_text(
                        text=dsp,
                        reply_markup=reply_markup,
                    )
                else:
                    self.message = await message.edit_caption(
                        caption=dsp,
                        reply_markup=reply_markup,
                    )
            except pyro_errors.FloodWait as e:
                await asyncio.sleep(e.value)
            except BaseException:
                await logger(Exception)
                # debug

    async def progress_for_aria2(self, download, start, message, silent=False):
        try:
            download = download.live
            if download.followed_by_ids:
                gid = download.followed_by_ids[0]
                try:
                    download = await sync_to_async(self.aria2.get_download, gid)
                except Exception:
                    log(Exception)
            if download.status == "error" or self.is_cancelled:
                if download.status == "error":
                    self.download_error = (
                        "E" + download.error_code + ": " + download.error_message
                    )
                download = None
                return await self.clean_download()

            ud_type = "`Download Pending…`"
            if not download.name.endswith(".torrent"):
                self.file_name = download.name
                # ponytail: for single-file torrents, use the actual file path, not folder
                if download.is_torrent and download.files and len(download.files) == 1:
                    self.path = str(download.files[0].path)
                else:
                    self.path = self.dl_folder + self.file_name
                ud_type = f"**Downloading:**\n`{download.name}`"
                ud_type += "\n**via:** "
                if download.is_torrent:
                    ud_type += "Torrent."
                else:
                    ud_type += "Direct Link."
            remaining_size = download.total_length - download.completed_length
            total = download.total_length
            current = download.completed_length
            speed = download.download_speed
            # time_to_completion = download.eta
            time_to_completion = ""
            now = time.time()
            diff = now - start
            fin_str = enhearts()

            if conf.ADL_TIMEOUT and (diff >= conf.ADL_TIMEOUT):
                download = None
                return await self.download_timeout()

            if download.completed_length and download.download_speed:
                time_to_completion = time_formatter(
                    int(
                        (download.total_length - download.completed_length)
                        / download.download_speed
                    )
                )

            progress = "```\n{0}{1}```\n<b>Progress:</b> `{2}%`\n".format(
                "".join([fin_str for i in range(math.floor(download.progress / 10))]),
                "".join(
                    [
                        self.unfin_str
                        for i in range(10 - math.floor(download.progress / 10))
                    ]
                ),
                round(download.progress, 2),
            )
            tmp = (
                progress
                + "`{0} of {1}`\n**Speed:** `{2}/s`\n**Remains:** `{3}`\n**ETA:** `{4}`\n**Elapsed:** `{5}`\n".format(
                    value_check(hbs(current)),
                    value_check(hbs(total)),
                    value_check(hbs(speed)),
                    value_check(hbs(remaining_size)),
                    # elapsed_time if elapsed_time != '' else "0 s",
                    # download.eta if len(str(download.eta)) < 30 else "0 s",
                    time_to_completion if time_to_completion else "0 s",
                    time_formatter(diff),
                )
            )
            if silent:
                await asyncio.sleep(10)
                return
            try:
                # Attach the button to the message with an inline keyboard
                reply_markup = []
                # file_name = self.file_name.split("/")[-1]
                dl_info = await parse_dl(self.file_name)
                (
                    info_button,
                    more_button,
                    back_button,
                    cancel_button,
                ) = self.gen_buttons()
                if not self.dl_info:
                    reply_markup.append([cancel_button])
                    dsp = "{}\n{}".format(ud_type, tmp)
                elif not self.display_dl_info:
                    reply_markup.extend(([info_button], [cancel_button]))
                    dsp = "{}\n{}".format(ud_type, tmp)
                else:
                    reply_markup.extend(([more_button], [back_button], [cancel_button]))
                    dsp = dl_info
                reply_markup = InlineKeyboardMarkup(reply_markup)
            except BaseException:
                await logger(BaseException)
            if not message.photo:
                self.message = await message.edit_text(
                    text=dsp,
                    reply_markup=reply_markup,
                )
            else:
                self.message = await message.edit_caption(
                    caption=dsp,
                    reply_markup=reply_markup,
                )

            await asyncio.sleep(10)

        except pyro_errors.BadRequest:
            await asyncio.sleep(10)
            download = await self.progress_for_aria2(download, start, message, silent)

        except pyro_errors.FloodWait as e:
            await asyncio.sleep(e.value)
            await asyncio.sleep(2)
            download = await self.progress_for_aria2(download, start, message, silent)

        except Exception as e:
            self.download_error = str(e)
            await logger(Exception)
            download = await self.clean_download()

        finally:
            return download

    def register(self):
        try:
            code(self, index=self.id)
            if self.lc:
                self.log_id = f"{self.lc.chat_id}:{self.lc.id}"
                code(self, index=self.log_id)
        except Exception:
            log(Exception)

    def un_register(self, force=False):
        if (self.dl_info and conf.COMP_MODE) and not force:
            return
        try:
            decode(self.id, pop=True)
            if self.log_id:
                decode(self.log_id, pop=True)
        except Exception:
            log(Exception)

    async def clean_download(self):
        try:
            if self.uri:
                await sync_to_async(rm_leech_file, self.uri_gid)
                _BATCH_TORRENT_GIDS.pop(_uri_key(self.uri), None)
            else:
                await sync_to_async(s_remove, self.path)
        except Exception:
            log(Exception)

    async def download_timeout(self):
        try:
            self.download_error = "E28: Download took longer than the specified time limit and has therefore been cancelled!"
            await self.clean_download()
        except Exception:
            log(Exception)

    async def wait(self):
        if (
            self.message
            and self.display_dl_info
            and self.pause_on_dl_info
            and self.dl_info
        ):
            msg = "been completed." if not self.is_cancelled else "been cancelled!"
            msg = "ran into errors!" if self.download_error else msg
            reply_markup = []
            (
                info_button,
                more_button,
                back_button,
                cancel_button,
            ) = self.gen_buttons()
            reply_markup.extend(([more_button], [back_button]))
            reply_markup = InlineKeyboardMarkup(reply_markup)
            await self.message.edit(
                self.message.text.markdown + f"\n\n`Download has {msg}\n"
                "To continue click back.`",
                reply_markup=reply_markup,
            )
        while self.dl_info and self.display_dl_info and self.pause_on_dl_info:
            await asyncio.sleep(5)
