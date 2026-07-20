#!/usr/bin/env bash
set -e
export TERM=${TERM:-xterm}
export PATH="$HOME/bin:$PATH"

# ── aria2c ──────────────────────────────────────────────────────────
if ! command -v aria2c &>/dev/null; then
    echo "Installing aria2c..."
    sudo apt-get install -y aria2 2>/dev/null \
    || {
        # fallback: static binary
        ARCH=$(uname -m)
        [ "$ARCH" = "aarch64" ] \
            && URL="https://github.com/abcfy2/aria2-static-build/releases/download/1.37.0/aria2-aarch64-linux-musl.zip" \
            || URL="https://github.com/abcfy2/aria2-static-build/releases/download/1.37.0/aria2-x86_64-linux-musl.zip"
        mkdir -p "$HOME/bin"
        wget -q "$URL" -O /tmp/aria2.zip
        sudo apt-get install -y unzip 2>/dev/null || true
        unzip -qo /tmp/aria2.zip -d /tmp/aria2_bin
        cp /tmp/aria2_bin/aria2c "$HOME/bin/aria2c"
        chmod +x "$HOME/bin/aria2c"
    }
fi
echo "aria2c: $(aria2c --version | head -1)"

# ── ffmpeg ──────────────────────────────────────────────────────────
if ! command -v ffmpeg &>/dev/null; then
    echo "Installing ffmpeg..."
    sudo apt-get install -y ffmpeg
fi
echo "ffmpeg: $(ffmpeg -version 2>&1 | head -1)"

# ── run ─────────────────────────────────────────────────────────────
python3 update.py && python3 -m bot
