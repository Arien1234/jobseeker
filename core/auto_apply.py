"""Click the visible apply button and trust only the page's own success text."""

from __future__ import annotations

SUCCESS_TEXTS = ("已向对方发送简历", "投递成功")
BLOCK_TEXTS = ("安全验证", "滑动验证", "访问验证", "操作过于频繁")
STAY_LABELS = ("留在此页", "关闭")


class ApplyBlocked(Exception):
    """The page is asking for a human check. Do not record a successful application."""


def dismiss_label(labels: list[str]) -> str | None:
    present = {str(label).strip() for label in labels}
    for name in STAY_LABELS:
        if name in present:
            return name
    return None


def classify_apply_text(text: str) -> str:
    sample = text or ""
    if any(phrase in sample for phrase in BLOCK_TEXTS):
        return "blocked"
    if any(phrase in sample for phrase in SUCCESS_TEXTS):
        return "submitted"
    return "failed"


def _click_stay(page) -> str:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    for name in STAY_LABELS:
        locator = page.get_by_role("button", name=name)
        if name == "留在此页" and locator.count() == 0:
            locator = page.get_by_text(name, exact=True)
        if locator.count() == 0:
            continue
        try:
            locator.first.click(timeout=3000)
        except Exception:
            continue
        try:
            page.wait_for_function(
                """(name) => ![...document.querySelectorAll('button')].some(el => (el.innerText || '').replace(/\\s+/g, '').includes(name))""",
                arg=name,
                timeout=4000,
            )
        except PlaywrightTimeout:
            pass
        return name
    return ""


def _page_marker(page) -> str:
    try:
        marker = page.evaluate(
            """() => {
                const text = document.body ? document.body.innerText : '';
                if (['安全验证', '滑动验证', '访问验证', '操作过于频繁'].some(item => text.includes(item))) return 'blocked';
                if (['已向对方发送简历', '投递成功'].some(item => text.includes(item))) return 'submitted';
                if (text.includes('已投递')) return 'already';
                return 'failed';
            }"""
        )
    except Exception:
        return "failed"
    return str(marker or "failed")


def apply_current_job(page) -> tuple[str, str]:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    from core.browser import ManualActionRequired

    try:
        preview = page.locator("body").inner_text(timeout=3000)[:800]
    except Exception:
        preview = ""
    if _page_marker(page) == "blocked" or classify_apply_text(preview) == "blocked":
        raise ManualActionRequired("需要人工处理验证")

    button = page.get_by_role("button", name="立即投递")
    if button.count() == 0:
        if _page_marker(page) == "already" or "已投递" in preview:
            return "already", "页面显示已经投过"
        return "failed", "没有找到立即投递按钮"

    button.first.click(timeout=5000)
    try:
        page.wait_for_function(
            """() => {
                const text = document.body ? document.body.innerText : '';
                return ['已向对方发送简历', '投递成功', '安全验证', '滑动验证', '访问验证'].some(item => text.includes(item));
            }""",
            timeout=8000,
        )
    except PlaywrightTimeout:
        if _page_marker(page) != "submitted":
            return "failed", "点击后没有出现投递成功提示"

    outcome = _page_marker(page)
    if outcome == "blocked":
        raise ManualActionRequired("需要人工处理验证")
    if outcome != "submitted":
        return "failed", "页面没有确认投递成功"
    try:
        page.get_by_text("留在此页", exact=True).first.wait_for(state="visible", timeout=4000)
    except Exception:
        pass
    stayed = _click_stay(page)
    if stayed:
        return "submitted", f"页面出现投递成功提示，已点击{stayed}"
    return "submitted", "页面出现投递成功提示，没有找到留在此页"
