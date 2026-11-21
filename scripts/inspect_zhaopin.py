"""Open the real recommend page and record structure counts. No cookies are stored."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.browser import BrowserSession
from core.logging_config import setup_logging
from core.paths import ENTRY_URL, LOG_DIR


def main() -> int:
    setup_logging()
    session = BrowserSession(headless=False)
    report = {"entry_url": ENTRY_URL, "card_count": 0, "login_status": "", "url": "", "title": "", "error": ""}
    try:
        session.start()
        report["login_status"] = session.open_entry(ENTRY_URL)
        report["url"] = session.page.url
        report["title"] = session.page.title()
        report["card_count"] = session.page.locator("div.job-list-panel div.job-card").count()
        report["detail_title_count"] = session.page.locator(".job-detail-summary__title-text").count()
    except Exception as exc:
        report["error"] = type(exc).__name__
    finally:
        session.close()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / "dom_diagnostic.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not report["error"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
