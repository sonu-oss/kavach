#!/usr/bin/env python3
"""Automated Real-Time Telegram Message Monitor for Kavach.

Listens for incoming messages from a Telegram bot webhook or Telegram MTProto client,
automatically screens them using Kavach Smart Shield, and notifies when harassment or threats occur.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx
from app import config
from app.schemas import AnalyzeRequest, Message
from app.shield.classifier import analyze


async def scan_incoming_text(sender: str, text: str, platform: str = "telegram"):
    """Screen an incoming text message using Kavach Shield without manual screenshots."""
    req = AnalyzeRequest(
        platform=platform,
        self_handle="me",
        messages=[
            Message(
                id=f"live-{int(asyncio.get_event_loop().time()*1000)}",
                sender=sender,
                text=text,
            )
        ],
    )
    res = await analyze(req)
    print(f"\n[Kavach Shield] Scanned message from '{sender}':")
    print(f"  Verdict: {res.category.upper()} (Risk Score: {res.risk_score}/100, Severity: {res.severity})")
    print(f"  Reason:  {res.reason}")

    if res.severity in ("medium", "high") and res.category != "safe":
        print(f"  🚨 ALERT: Threat detected! Automated action triggered.")
        # In production: trigger alert or save evidence
    return res


async def run_poll_bot():
    """Poll Telegram Bot API for incoming user messages."""
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        print("[!] TELEGRAM_BOT_TOKEN not configured in backend/.env.")
        print("    Configure it or use the Kavach Browser Extension for WhatsApp & Telegram Web.")
        return

    offset = 0
    print(f"[*] Starting Telegram real-time live monitor with bot token...")
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=20"
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        msg = update.get("message") or update.get("edited_message")
                        if msg and "text" in msg:
                            sender = msg.get("from", {}).get("first_name", "Unknown")
                            text = msg["text"]
                            if not text.startswith("/"):
                                await scan_incoming_text(sender, text, platform="telegram-bot")
            except Exception as e:
                print(f"[!] Polling error: {e}")
                await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(run_poll_bot())
    except KeyboardInterrupt:
        print("\nExiting Kavach Live Monitor.")

