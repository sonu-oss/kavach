import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"

load_dotenv(BACKEND_DIR / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
# Only sent for reasoning models (gpt-oss, qwen3); blank disables it.
GROQ_REASONING_EFFORT = os.getenv(
    "GROQ_REASONING_EFFORT", "low" if "gpt-oss" in GROQ_MODEL else ""
).strip()
# Used for the retry when the main model hits its rate limit.
GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b").strip()
GROQ_TIMEOUT = float(os.getenv("GROQ_TIMEOUT", "12"))
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Evidence Vault storage (SQLite + screenshot files). Override for tests.
DATA_DIR = Path(os.getenv("KAVACH_DATA_DIR", str(BACKEND_DIR / "data")))
# Enables the "simulate tampering" and "reset vault" endpoints for the live demo.
DEMO_MODE = os.getenv("KAVACH_DEMO", "1").strip() == "1"
