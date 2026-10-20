#!/usr/bin/env bash
# NovaNode installer: installs everything sift-v4.py needs, asks for the website URL and starts the bot.
set -e
cd "$(dirname "$0")"
echo "=== NovaNode installer ==="

SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

if [ -z "$SIFT_SKIP_INSTALL" ]; then
  if command -v apt-get >/dev/null 2>&1; then
    echo "[1/4] Installing python3, venv, pip, screen, curl..."
    $SUDO apt-get update -y >/dev/null 2>&1 || true
    $SUDO apt-get install -y python3 python3-venv python3-pip screen curl >/dev/null
  fi
  echo "[2/4] Installing discord.py and aiohttp in ./venv ..."
  python3 -m venv venv
  ./venv/bin/pip install -q -U pip "discord.py>=2.3" aiohttp
  read -rp "Install Docker too (needed for VPS creation)? [y/N]: " DOCKER
  if [[ "$DOCKER" =~ ^[Yy]$ ]]; then
    curl -fsSL https://get.docker.com | $SUDO sh
    $SUDO systemctl enable --now docker >/dev/null 2>&1 || true
  fi
fi

echo "[3/4] Settings"
touch sift.env && chmod 600 sift.env
setkv() { { grep -v "^$1=" sift.env || true; } > sift.env.tmp; echo "$1=$2" >> sift.env.tmp; mv sift.env.tmp sift.env; chmod 600 sift.env; }

while true; do
  read -rp "Website URL to give to users (e.g. https://dash.teraxbot.eu.cc): " URL
  URL="${URL%/}"
  if [[ "$URL" =~ ^https?://[^[:space:]]+$ ]]; then break; fi
  echo "  Please enter a full URL starting with http:// or https://"
done
setkv SIFT_WEB_URL "$URL"

read -rsp "Discord bot token (hidden, Enter = keep current): " TOKEN; echo
[ -n "$TOKEN" ] && setkv DISCORD_TOKEN "$TOKEN"

read -rsp "Extra owner Discord ID (hidden, optional, Enter = skip): " OWN2; echo
OWN2="$(echo "$OWN2" | tr -cd '0-9')"
[ -n "$OWN2" ] && setkv SIFT_OWNER2 "$OWN2"

echo "[4/4] Starting the bot..."
if grep -q '^DISCORD_TOKEN=.' sift.env; then
  pkill -f sift-v4.py 2>/dev/null || true
  PY=./venv/bin/python; [ -x "$PY" ] || PY=python3
  nohup "$PY" sift-v4.py > sift.log 2>&1 &
  sleep 3
  echo "Started. Logs: tail -f sift.log   |   Check site: curl -I http://127.0.0.1:8082"
else
  echo "No token saved yet - run this script again and enter the token, then the bot will start."
fi
