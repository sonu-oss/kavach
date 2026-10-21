#!/bin/bash
# Starts Kavach for a demo: the app, a public https link for phones, and keeps the Mac awake.
# Run it from Terminal (./start-demo.sh) or double-click "Start Kavach.command" in Finder.
# Press Ctrl+C (or close the window) to stop everything.

cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
PORT=8000
LOG_DIR="$ROOT/backend/data/logs"
mkdir -p "$LOG_DIR"

say() { printf "\n\033[1m%s\033[0m\n" "$1"; }
fail() { printf "\n\033[31m✗ %s\033[0m\n\n" "$1"; read -r -p "Press Enter to close." _; exit 1; }

# ---------- checks ----------
[ -x "$ROOT/backend/.venv/bin/uvicorn" ] || fail "The app isn't installed yet. In Terminal run:
  cd \"$ROOT/backend\" && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
[ -f "$ROOT/backend/.env" ] || fail "backend/.env is missing (it holds the Groq, Telegram and Gmail keys)."
command -v cloudflared >/dev/null || fail "cloudflared isn't installed. In Terminal run:  brew install cloudflared"
curl -s -m 5 -o /dev/null https://www.cloudflare.com || fail "No internet connection. Connect to Wi-Fi or a phone hotspot and try again."

PIDS=()
cleanup() {
  say "Stopping Kavach…"
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
  exit 0
}
trap cleanup INT TERM HUP

# ---------- 1. public link (Cloudflare tunnel) ----------
say "1/3  Getting a public https link…"
TUNNEL_LOG="$LOG_DIR/tunnel.log"
: > "$TUNNEL_LOG"
cloudflared tunnel --no-autoupdate --url "http://localhost:$PORT" > "$TUNNEL_LOG" 2>&1 &
PIDS+=($!)
URL=""
for _ in $(seq 1 45); do
  URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" | head -1)
  [ -n "$URL" ] && grep -q "Registered tunnel connection" "$TUNNEL_LOG" && break
  sleep 1
done
[ -n "$URL" ] || fail "Couldn't get a link from Cloudflare. Check the internet and run this again. (Details: $TUNNEL_LOG)"

# ---------- 2. the app ----------
say "2/3  Starting the app…"
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo "   The app is already running on port $PORT. Using it."
else
  # PUBLIC_URL makes Telegram and email links point at the public link, not localhost.
  PUBLIC_URL="$URL" "$ROOT/backend/.venv/bin/uvicorn" app.main:app --app-dir "$ROOT/backend" --port $PORT \
    > "$LOG_DIR/server.log" 2>&1 &
  PIDS+=($!)
fi
for _ in $(seq 1 60); do
  curl -s -m 2 -o /dev/null "http://localhost:$PORT/health" && break
  sleep 1
done
curl -s -m 2 -o /dev/null "http://localhost:$PORT/health" || fail "The app didn't start. Details: $LOG_DIR/server.log"

# The public link can take a few seconds to start working worldwide.
for _ in $(seq 1 30); do
  [ "$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$URL/health")" = "200" ] && break
  sleep 2
done

# ---------- 3. keep the Mac awake while this window is open ----------
caffeinate -dimsu -w $$ &
PIDS+=($!)

say "3/3  Kavach is running ✓"
cat <<EOF

   Phone link:   $URL
   Responders:   $URL/responder.html

   On your iPhone: scan the QR code on the page that just opened (or type the link)
   in Safari → Share → Add to Home Screen. Delete any older Kavach icon first.

   Keep this window open and the Mac plugged in. Press Ctrl+C to stop.
EOF
echo "$URL" > "$LOG_DIR/current-link.txt"
[ -z "$KAVACH_NO_OPEN" ] && open "http://localhost:$PORT/connect.html?u=$URL"

# Stay here until Ctrl+C; say so if the tunnel or app dies.
while sleep 15; do
  for p in "${PIDS[@]}"; do
    kill -0 "$p" 2>/dev/null || { say "⚠ Something stopped. Close this window and run Start Kavach again (the link will change)."; break 2; }
  done
done
cleanup
