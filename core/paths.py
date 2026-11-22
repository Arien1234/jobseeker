"""Project paths shared by the runtime modules."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
BROWSER_DATA_DIR = ROOT / "browser_data"
PROFILE_PATH = CONFIG_DIR / "profile.json"
SELECTORS_PATH = CONFIG_DIR / "selectors.json"
SECRETS_PATH = CONFIG_DIR / "secrets.json"
DATABASE_PATH = DATA_DIR / "jobs.db"
LOG_PATH = LOG_DIR / "app.log"
CHECKPOINT_PATH = DATA_DIR / "collect_checkpoint.json"
RESUME_SOURCE_PATH = DATA_DIR / "resume_source.pdf"
ENTRY_URL = "https://www.zhaopin.com/jobs/?pageMode=recommend"
