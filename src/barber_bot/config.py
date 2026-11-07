from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]
env_path = ROOT / ".env"
if env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(ROOT / "data" / "barbearia.db")))
KNOWLEDGE_DIR = ROOT / "data" / "knowledge"
CHROMA_DIR = ROOT / "data" / "chroma"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MIN_BOOKING_DAYS = int(os.getenv("MIN_BOOKING_DAYS", "1"))
MAX_BOOKING_DAYS = int(os.getenv("MAX_BOOKING_DAYS", "3"))
TIMEZONE = os.getenv("TIMEZONE", "America/Sao_Paulo")
