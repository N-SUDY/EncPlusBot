import shutil
import requests

from bot.config import _bot, conf
from bot.fun.emojis import enmoji, enmoji2
from bot.fun.quips import enquip, enquip2
from bot.utils.bot_utils import encode_job as ejob
from bot.utils.log_utils import logger
from bot.utils.rss_utils import scheduler
from bot.workers.auto.status import autostat
from bot.workers.auto.transcode import something

from .before import *


def _ensure_aria2c() -> str:
    """Return path to aria2c, downloading a static binary if needed."""
    # Already on PATH?
    path = shutil.which("aria2c")
    if path:
        return path

    # Put it in a persistent spot inside the app's working directory
    local_bin = os.path.join(os.getcwd(), "bin")
    local_aria2c = os.path.join(local_bin, "aria2c")
    if os.path.isfile(local_aria2c):
        return local_aria2c

    LOGS.info("aria2c not found — downloading static binary…")
    import platform, zipfile, io
    arch = platform.machine()
    # Use continuous build tag (updated weekly, always aria2 1.37.0+)
    # Asset filenames confirmed from abcfy2/aria2-static-build releases
    if arch == "aarch64":
        filename = "aria2-aarch64-unknown-linux-musl_static.zip"
    else:
        filename = "aria2-x86_64-unknown-linux-musl_static.zip"
    # Try versioned release first, fall back to continuous build
    urls = [
        f"https://github.com/abcfy2/aria2-static-build/releases/download/1.37.0/{filename}",
        f"https://github.com/abcfy2/aria2-static-build/releases/download/continuous/{filename}",
    ]
    for url in urls:
        try:
            LOGS.info(f"Trying: {url}")
            r = requests.get(url, timeout=60, allow_redirects=True)
            if r.status_code != 200:
                LOGS.warning(f"Got {r.status_code} from {url}")
                continue
            os.makedirs(local_bin, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                names = z.namelist()
                LOGS.info(f"Zip contents: {names}")
                for member in names:
                    if member.endswith("aria2c"):
                        with z.open(member) as src, open(local_aria2c, "wb") as dst:
                            dst.write(src.read())
                        break
            os.chmod(local_aria2c, 0o755)
            LOGS.info(f"aria2c downloaded to {local_aria2c}")
            return local_aria2c
        except Exception as e:
            LOGS.warning(f"Failed from {url}: {e}")
    return "aria2c"  # fall back to hoping it's on PATH

async def start_aria2p():
    try:
        aria2 = aria2p.API(
            aria2p.Client(
                host="http://localhost",
                port=conf.ARIA2_PORT,
                secret="",
                timeout=10,  # was defaulting to aria2p's 60s; a wedged daemon
                             # could silently stall every RPC call for a
                             # minute, which is what caused /lb to appear
                             # stuck at "Waiting for download handler…"
            )
        )
        # Lightweight RPC ping — no external URL needed
        aria2.client.get_version()
        _bot.aria2 = aria2
        _bot.sas = True
        LOGS.info("✓ aria2 RPC started successfully")

    except (ConnectionRefusedError, requests.exceptions.ConnectionError, Exception) as e:
        LOGS.warning(f"aria2 RPC not available: {type(e).__name__}. Bot can still run but torrent/aria2 downloads may fail.")
        _bot.sas = False


async def start_rpc():
    if not conf.ENABLE_ARIA2:
        LOGS.info("aria2 disabled (ENABLE_ARIA2=False) — skipping.")
        _bot.sas = False
        return
    try:
        aria2c_bin = _ensure_aria2c()
        ret = os.system(
            f"{aria2c_bin} --enable-rpc=true --rpc-max-request-size=1024M --rpc-listen-port={conf.ARIA2_PORT} "
            "--seed-time=0 --follow-torrent=mem --summary-interval=0 --daemon=true --allow-overwrite=true "
            "--user-agent=Wget/1.12 "
            "--max-connection-per-server=10 --http-accept-gzip=true --split=10 --disk-cache=10M "
            "--min-split-size=10M --optimize-concurrent-downloads=true "
            "--enable-dht=true --enable-dht6=false --bt-enable-lpd=true "
            "--bt-tracker-connect-timeout=10 --bt-tracker-timeout=10 "
            "--dht-listen-port=6881 --listen-port=6881-6999 "
            "--bt-save-metadata=true --bt-load-saved-metadata=true "
            "--bt-request-peer-speed-limit=10M --bt-max-peers=50 "
            "--rpc-listen-all=true --rpc-allow-origin-all=true "
            "--bt-tracker=udp://tracker.opentrackr.org:1337/announce,"
            "udp://open.tracker.cl:1337/announce,"
            "udp://tracker.openbittorrent.com:6969/announce,"
            "udp://opentracker.io:6969/announce,"
            "udp://tracker.torrent.eu.org:451/announce,"
            "udp://exodus.desync.com:6969/announce,"
            "udp://tracker.moeking.me:6969/announce,"
            "udp://tracker.bitsearch.to:1337/announce,"
            "http://tracker.bt4g.com:2095/announce"
        )
        if ret != 0:
            LOGS.warning(f"aria2c daemon failed to start (exit code: {ret})")
    except Exception as e:
        LOGS.warning(f"aria2c startup error: {e}")

    # Give the daemon a moment to bind its RPC port before we connect
    await asyncio.sleep(2)
    await start_aria2p()

async def onrestart():
    try:
        if sys.argv[1] == "restart":
            msg = "**Restarted!** "
        elif sys.argv[1].startswith("update"):
            s = sys.argv[1].split()[1]
            if s == "True":
                with open(version_file, "r") as file:
                    v = file.read()
                msg = f"**Updated to >>>** `{v}`"
            else:
                msg = "**No major update found!**\n" f"`Bot restarted! {enmoji()}`"
        else:
            return
        chat_id, msg_id = map(int, sys.argv[2].split(":"))
        await pyro.edit_message_text(chat_id, msg_id, msg)
    except Exception:
        await logger(Exception)


async def onstart():
    try:
        for i in conf.OWNER.split():
            try:
                await tele.send_message(int(i), f"**I'm {enquip()} {enmoji()}**")
            except Exception:
                pass
        if conf.LOG_CHANNEL:
            me = await pyro.get_users("me")
            await tele.send_message(
                conf.LOG_CHANNEL, f"**{me.first_name} is {enquip()} {enmoji()}**"
            )
        dev = conf.DEV or conf.LOG_CHANNEL or int(conf.OWNER.split()[0])
        try:
            await tele.send_message(
                dev,
                f"**Aria2:** `{'Online' if _bot.sas else 'Offline/Not_ready'}`",
            )
        except Exception:
            await logger(Exception)
    except BaseException:
        pass


async def on_termination():
    try:
        dead_msg = f"**I'm {enquip2()} {enmoji2()}**"
        if conf.LOG_CHANNEL:
            await tele.send_message((conf.LOG_CHANNEL), dead_msg)
        else:
            for i in conf.OWNER.split():
                try:
                    await tele.send_message(int(i), dead_msg)
                except Exception:
                    pass
    except Exception:
        pass
    # More cleanup code?
    exit(0)


async def on_startup():
    try:
        scheduler.start()
        asyncio.create_task(autostat())
        await start_rpc()  # await so aria2 is ready before onstart reports its status
        # ── health-check server ──────────────────────────────────────────
        if conf.HEALTH_PORT:
            asyncio.create_task(_start_health_server(conf.HEALTH_PORT))
        # ── auto-clean task ──────────────────────────────────────────────
        if conf.AUTO_CLEAN_INTERVAL:
            asyncio.create_task(_auto_clean_task(conf.AUTO_CLEAN_INTERVAL))
        loop = asyncio.get_running_loop()
        for signame in {"SIGINT", "SIGTERM", "SIGABRT"}:
            loop.add_signal_handler(
                getattr(signal, signame),
                lambda: asyncio.create_task(on_termination()),
            )
        if len(sys.argv) == 3:
            await onrestart()
        else:
            await asyncio.sleep(1)
            await onstart()
        await entime.start()
        ejob.reset(force=True)
        # Drop the item that was mid-encoding when bot died.
        # queue[0] is always the "currently processing" slot; it can't be resumed
        # after a restart, so we pop it to prevent an infinite re-encode loop.
        from bot.utils.bot_utils import get_queue, get_bqueue
        from bot.utils.db_utils import save2db
        _q = get_queue()
        if _q:
            _first_key = list(_q.keys())[0]
            get_bqueue().pop(_first_key, None)
            _q.pop(_first_key)
            await save2db()
            await save2db("batches")
            await logger(e="Startup: dropped mid-encoding queue[0] item to prevent loop.")
        await asyncio.sleep(30)
        asyncio.create_task(something())
    except Exception:
        logger(Exception)
    _bot.started = True


async def _start_health_server(port: int):
    """Minimal HTTP health-check server for Heroku/Railway uptime pings."""
    from aiohttp import web

    async def _handle(request):
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", _handle)
    app.router.add_get("/health", _handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    try:
        await site.start()
        await logger(e=f"Health-check server started on port {port}")
    except Exception:
        await logger(Exception)


async def _auto_clean_task(interval: int):
    """Periodically clean temp/download dirs."""
    import shutil

    CLEAN_DIRS = ("temp", "downloads", "downloads2", "mux", "minfo")
    while True:
        await asyncio.sleep(interval)
        try:
            for d in CLEAN_DIRS:
                if os.path.isdir(d) and not os.listdir(d):
                    continue
                if not os.path.isdir(d):
                    continue
                for entry in os.scandir(d):
                    try:
                        if entry.is_dir():
                            shutil.rmtree(entry.path, ignore_errors=True)
                        else:
                            os.remove(entry.path)
                    except Exception:
                        pass
            await logger(e="Auto-clean: temp dirs cleaned.")
        except Exception:
            await logger(Exception)
