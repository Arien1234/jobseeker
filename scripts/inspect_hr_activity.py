"""Inspect HR activity text and class names on the live logged-in page."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.browser import BrowserSession
from core.logging_config import setup_logging
from core.paths import ENTRY_URL, LOG_DIR

JS_FIND = """() => {
  const re = /刚刚活跃|今日活跃|本周活跃|本月活跃|3日内活跃|总回复|在线/;
  const out = [];
  for (const el of document.querySelectorAll('[class]')) {
    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    if (!t || t.length > 80 || !re.test(t)) continue;
    out.push({cls: String(el.className).slice(0, 120), text: t.slice(0, 80)});
    if (out.length >= 40) break;
  }
  const html = document.documentElement.innerHTML;
  const state = html.match(/"(?:staffActiveState|staffActive|activeState|onlineStatus|hrActive|activeTimeDesc|activeTime|staffOnline|staffName)"\\s*:\\s*"[^"]{0,40}"/g) || [];
  return {nodes: out, state: state.slice(0, 30), title: document.title};
}"""


def main() -> int:
    setup_logging()
    session = BrowserSession(headless=False)
    report: dict = {}
    try:
        session.start()
        report["status"] = session.open_entry(ENTRY_URL)
        page = session.page
        report["url"] = page.url
        report["title"] = page.title()
        report["cards"] = page.locator("div.job-card").count()
        if report["cards"] == 0:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            (LOG_DIR / "hr_activity_dom.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(json.dumps(report, ensure_ascii=False))
            return 0
        page.locator("div.job-card").first.click(timeout=5000)
        page.wait_for_timeout(2000)
        report["panel"] = page.evaluate(JS_FIND)
        link = page.locator("a.job-detail-view-more")
        if link.count():
            href = link.first.get_attribute("href") or ""
            if href.startswith("/"):
                href = "https://www.zhaopin.com" + href
            report["detail_href"] = href
            detail = session.context.new_page()
            detail.goto(href, wait_until="domcontentloaded", timeout=30000)
            detail.wait_for_timeout(2500)
            report["detail"] = detail.evaluate(JS_FIND)
            detail.close()
        body = page.evaluate("() => document.body.innerText")
        report["text_hits"] = re.findall(
            r".{0,40}(?:刚刚活跃|今日活跃|本周活跃|本月活跃|3日内活跃|在线|总回复).{0,40}",
            body or "",
        )[:20]
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        session.close()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / "hr_activity_dom.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False)[:4000])
    return 0 if "error" not in report else 1


if __name__ == "__main__":
    raise SystemExit(main())
