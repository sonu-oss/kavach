# Running a live demo on a phone

Kavach runs on a laptop and is exposed to phones over HTTPS through a Cloudflare quick tunnel.
HTTPS is required for GPS, in-browser encryption and installing the app on a home screen.

## Start

Double-click `Start Kavach.command` in Finder, or run:

```bash
./start-demo.sh
```

The script:

1. opens a Cloudflare tunnel and waits for its public `https://…trycloudflare.com` address,
2. starts the backend with `PUBLIC_URL` set to that address, so Telegram and email links point to it,
3. keeps the Mac awake while it runs (`caffeinate`),
4. opens `connect.html`, which shows QR codes for the app and the responder page.

The current address is also written to `backend/data/logs/current-link.txt`. Logs are in `backend/data/logs/`.
Stop with Ctrl+C or by closing the Terminal window; the tunnel, server and `caffeinate` all stop with it.

## Install on the phone

Scan the QR code, open it in Safari, then Share → Add to Home Screen.
Quick-tunnel addresses change on every start, so remove the previous home-screen icon and reinstall from the new code.

## Before presenting

Open the app and use **Reset everything** on the home page. It ends any running SOS and clears the Vault,
chats, drafts and SOS history. Trusted contacts and responders are kept.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The app looks out of date, or SOS fails | The tunnel has dropped and the phone is showing its cached copy | Restart the script and reinstall from the new QR code |
| "No internet connection" | No network | Join Wi-Fi or a phone hotspot and restart |
| "The app isn't installed yet" | No virtual environment | `cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` |
| macOS won't open `Start Kavach.command` | Gatekeeper | Right-click → Open → Open (first time only) |

For a permanent address, use a named Cloudflare tunnel on your own domain instead of a quick tunnel.
