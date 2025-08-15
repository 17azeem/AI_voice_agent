import os
from dotenv import load_dotenv

load_dotenv()
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
STATIC_DIR = os.path.abspath(os.path.join(PROJECT_ROOT, "..", "frontend"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MURF_API_KEY = os.getenv("MURF_API_KEY")
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")



# Validate required keys at import time to fail fast in dev; keep lazy for tests if needed
_missing = [k for k, v in {
    "GEMINI_API_KEY": GEMINI_API_KEY,
    "MURF_API_KEY": MURF_API_KEY,
    "ASSEMBLYAI_API_KEY": ASSEMBLYAI_API_KEY,
}.items() if not v]

if _missing:
    raise RuntimeError(f"Missing required env vars: {', '.join(_missing)}")
