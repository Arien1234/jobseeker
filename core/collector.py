"""Collect visible Zhaopin jobs. It reads pages the user can already open."""

from __future__ import annotations

import json
import random
import re
import threading
import time
from datetime import datetime

from core.browser import ManualActionRequired, classify_page_state
from core.database import Database, now_text
from core.job_parser import (
    card_signature,
    clean_inline,
    combo_key_for,
    company_size_from_html,
    company_size_text,
    compose_description,
    dedup_key_for,
    load_selectors,
    parse_card_html,
    parse_detail_html,
    parse_panel_html,
)
from core.logging_config import get_logger
from core.models import SOURCE_NAME
from core.paths import CHECKPOINT_PATH

logger = get_logger("collector")


class CollectionStopped(Exception):
    """The user asked the current run to stop."""


def _pause_point(stop_event: threading.Event, pause_event: threading.Event) -> None:
    while pause_event.is_set() and not stop_event.is_set():
        time.sleep(0.2)
    if stop_event.is_set():
        raise CollectionStopped()


def _reading_gap(stop_event: threading.Event, pause_event: threading.Event, delay: float = 1.2) -> None:
    """Short pause so the visible browser can settle between jobs."""
    deadline = time.time() + delay
    while time.time() < deadline:
        _pause_point(stop_event, pause_event)
        time.sleep(0.05)


def list_mark(page) -> str:
    """Identity of the current list. A new page produces a different mark."""
    classes = load_selectors()["classes"]
    try:
        cards = _card_locator(page, classes)
        count = min(cards.count(), 5)
    except Exception:
        return ""
    parts: list[str] = []
    for index in range(count):
        try:
            html = cards.nth(index).evaluate("el => el.outerHTML")
            summary = parse_card_html(html, classes)
        except Exception:
            continue
        if summary.get("title") and summary.get("company"):
            parts.append(f"{summary['title']}|{summary['company']}")
    return "\n".join(parts)


def focus_job(page, title: str, company: str) -> bool:
    """Open the matching card so the side panel is the job about to be applied."""
    classes = load_selectors()["classes"]
    expected_title = clean_inline(title)
    expected_company = clean_inline(company)
    panel = page.locator(f".{classes['panel_title']}")
    if panel.count() and clean_inline(panel.first.inner_text(timeout=2000)) == expected_title:
        return True
    cards = _card_locator(page, classes)
    for index in range(cards.count()):
        card = cards.nth(index)
        try:
            card_title = clean_inline(card.locator(f".{classes['card_title']}").first.inner_text(timeout=2000))
            card_company = clean_inline(card.locator(f".{classes['card_company']}").first.inner_text(timeout=2000))
        except Exception:
            continue
        if card_title != expected_title:
            continue
        if expected_company and card_company != expected_company:
            continue
        card.locator(f".{classes['card_title']}").first.scroll_into_view_if_needed(timeout=5000)
        card.locator(f".{classes['card_title']}").first.click(timeout=5000)
        page.wait_for_function(
            """(expected) => {
                const el = document.querySelector('.job-detail-summary__title-text');
                if (!el) return false;
                return el.textContent.replace(/\\s+/g, ' ').trim() === expected;
            }""",
            arg=expected_title,
            timeout=8000,
        )
        return True
    return False


def _card_locator(page, classes: dict):
    scoped = page.locator(f"div.{classes['list']} > div.{classes['card']}")
    if scoped.count() > 0:
        return scoped
    fallback = page.locator(f"div.{classes['list']} div.{classes['card']}")
    if fallback.count() > 0:
        return fallback
    return page.locator(f"div.{classes['card']}")


def list_is_finished(text: str) -> bool:
    """Zhaopin shows this under the list after the current page has no further jobs."""
    sample = text or ""
    return "没有更多" in sample


def page_says_no_more(page) -> bool:
    try:
        text = page.locator("body").inner_text(timeout=2000)
    except Exception:
        return False
    return list_is_finished(text)


def _scroll_for_more(page, classes: dict, previous: int) -> int:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page.evaluate(
        """(listClass) => {
            const el = document.scrollingElement || document.documentElement;
            el.scrollTop = el.scrollHeight;
            const panel = document.querySelector('.' + listClass);
            if (panel) panel.scrollTop = panel.scrollHeight;
            const cards = document.querySelectorAll('.' + listClass + ' .job-card, div.job-card');
            const last = cards[cards.length - 1];
            if (last) last.scrollIntoView({block: 'end'});
        }""",
        classes["list"],
    )
    selector = f"div.{classes['list']} div.{classes['card']}"
    try:
        page.wait_for_function(
            "(payload) => document.querySelectorAll(payload.selector).length > payload.previous",
            arg={"selector": selector, "previous": previous},
            timeout=8000,
        )
    except PlaywrightTimeout:
        pass
    return page.locator(selector).count()


def _page_head_state(page) -> str:
    url = page.url or ""
    try:
        title = page.title() or ""
    except Exception:
        title = ""
    try:
        head = page.locator("body").inner_text(timeout=3000)[:400]
    except Exception:
        head = ""
    has_detail = page.locator("h1.summary-planes__title").count() > 0
    return classify_page_state(url, title, head, False, has_detail)


def _read_detail(context, url: str, classes: dict) -> dict:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    detail = context.new_page()
    try:
        try:
            detail.goto(url, wait_until="domcontentloaded", timeout=30000)
            detail.wait_for_selector(f".{classes['detail_title']}", timeout=15000)
        except PlaywrightTimeout as exc:
            state = _page_head_state(detail)
            if state in {"未登录", "需要人工处理验证"}:
                raise ManualActionRequired(state) from exc
            raise TimeoutError("页面加载超时") from exc
        state = _page_head_state(detail)
        if state in {"未登录", "需要人工处理验证"}:
            raise ManualActionRequired(state)
        html = detail.content()
        parsed = parse_detail_html(html, classes)
        parsed["company_size"] = company_size_from_html(html, classes)
        return parsed
    finally:
        detail.close()


def _read_company_size(context, url: str) -> str:
    """Open the job detail only long enough to read the company size block."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    classes = load_selectors()["classes"]
    detail = context.new_page()
    try:
        try:
            detail.goto(url, wait_until="domcontentloaded", timeout=20000)
            detail.wait_for_selector(
                f".{classes['company_summary_scale']}, .{classes['join_company_value']}",
                timeout=8000,
            )
        except PlaywrightTimeout as exc:
            state = _page_head_state(detail)
            if state in {"未登录", "需要人工处理验证"}:
                raise ManualActionRequired(state) from exc
            return ""
        state = _page_head_state(detail)
        if state in {"未登录", "需要人工处理验证"}:
            raise ManualActionRequired(state)
        return company_size_from_html(detail.content(), classes)
    finally:
        detail.close()


def _write_checkpoint(stats: dict, last_url: str, last_signature: str = "", card_index: int = 0) -> None:
    payload = {
        "collected": stats["collected"],
        "duplicated": stats["duplicated"],
        "failed": stats["failed"],
        "already_applied": stats.get("already_applied", 0),
        "last_url": last_url,
        "last_signature": last_signature,
        "card_index": card_index,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _note_apply_button(job: dict, page, log) -> str:
    """Read the side-panel button before screening. 继续沟通 means already applied."""
    from core.auto_apply import read_apply_action

    action = read_apply_action(page)
    if action == "chat":
        label = "已投递"
    elif action == "apply":
        label = "未投递"
    else:
        label = "未识别"
    job["prior_apply"] = label
    log(f"筛选前{label}: {job.get('title')} / {job.get('company')}")
    return label


def _mark_already_applied(database: Database, job: dict, log) -> bool:
    if job.get("prior_apply") != "已投递":
        return False
    job_id = database.find_job_id(job)
    if job_id is None:
        return False
    if database.existing_application_status(job) == "submitted":
        return True
    database.record_application(job_id, "submitted", "侧栏按钮为继续沟通，筛选前已投递")
    log(f"已投过（继续沟通）: {job.get('title')} / {job.get('company')}")
    return True


def collect_jobs(
    page,
    context,
    database: Database,
    max_count: int,
    stop_event: threading.Event,
    pause_event: threading.Event,
    on_log=None,
    on_stats=None,
    on_job=None,
    count_handled: bool = False,
    fetch_detail: bool = True,
    fresh: bool = False,
    start_index: int = 0,
) -> dict:
    classes = load_selectors()["classes"]
    stats = {
        "collected": 0,
        "duplicated": 0,
        "failed": 0,
        "handled": 0,
        "jd_fallback": 0,
        "already_applied": 0,
        "exhausted": False,
        "stopped": False,
    }
    processed: set[str] = set()
    known = database.identity_keys()
    last_url = ""
    last_signature = ""
    last_card_index = 0
    cursor = max(0, int(start_index))
    stats["next_index"] = cursor

    def log(message: str) -> None:
        if on_stats:
            on_stats(dict(stats))
        if on_log:
            on_log(message)

    def remember_failure(**kwargs) -> None:
        try:
            database.record_failure(**kwargs)
        except Exception:
            logger.error("数据库保存失败")

    if CHECKPOINT_PATH.exists() and not fresh:
        log("从上次采集位置继续：已入库职位会跳过，未采完的会接着往下刷")

    scrolls = 0
    while stats["handled" if count_handled else "collected"] < max_count and scrolls < 80:
        _pause_point(stop_event, pause_event)
        cards = _card_locator(page, classes)
        count = cards.count()
        if cursor > count:
            cursor = 0
        if count == 0:
            state = page.locator("body").inner_text(timeout=3000)[:800] if page.locator("body").count() else ""
            logged_out = page.locator(".home-header__c-no-login").count() > 0
            status = classify_page_state(page.url or "", page.title() or "", state, False, False, logged_out)
            if status in {"未登录", "需要人工处理验证"}:
                raise ManualActionRequired(status)
            log("职位解析失败: 当前页面没有职位卡片")
            logger.error("职位解析失败: 当前页面没有职位卡片")
            break

        for index in range(cursor, count):
            if stats["handled" if count_handled else "collected"] >= max_count:
                break
            _pause_point(stop_event, pause_event)
            card = cards.nth(index)
            try:
                html = card.evaluate("el => el.outerHTML")
                summary = parse_card_html(html, classes)
            except Exception as exc:
                stats["failed"] += 1
                if count_handled:
                    stats["handled"] += 1
                logger.error("职位解析失败")
                remember_failure(url="", title="", company="", stage="解析", reason=str(exc)[:300])
                log(f"职位解析失败: {exc}")
                continue
            signature = card_signature(summary)
            if signature in processed:
                continue
            processed.add(signature)
            if not summary["title"] or not summary["company"]:
                stats["failed"] += 1
                if count_handled:
                    stats["handled"] += 1
                logger.error("职位解析失败: 缺少名称或公司")
                remember_failure(
                    url="",
                    title=summary["title"],
                    company=summary["company"],
                    stage="解析",
                    reason="缺少职位名称或公司",
                )
                log(f"职位解析失败: {summary['title'] or '未命名职位'}")
                continue
            if count_handled and database.existing_application_status(summary) == "submitted":
                stats["duplicated"] += 1
                stats["handled"] += 1
                logger.info("职位重复: %s / %s", summary["title"], summary["company"])
                log(f"已经投过: {summary['title']} / {summary['company']}")
                continue
            if not count_handled and (
                dedup_key_for(summary) in known or combo_key_for(summary) in known
            ):
                stats["duplicated"] += 1
                stats["next_index"] = index + 1
                cursor = index + 1
                continue
            try:
                card.locator(f".{classes['card_title']}").scroll_into_view_if_needed(timeout=5000)
                card.locator(f".{classes['card_title']}").click(timeout=5000)
                expected = clean_inline(summary["title"])
                page.wait_for_function(
                    """(expected) => {
                        const el = document.querySelector('.job-detail-summary__title-text');
                        if (!el) return false;
                        return el.textContent.replace(/\\s+/g, ' ').trim() === expected;
                    }""",
                    arg=expected,
                    timeout=8000,
                )
                panel_html = page.locator(f".{classes['panel_root']}").evaluate("el => el.outerHTML")
                panel = parse_panel_html(panel_html, classes)
            except Exception as exc:
                stats["failed"] += 1
                if count_handled:
                    stats["handled"] += 1
                message = str(exc)
                if "Timeout" in type(exc).__name__ or "超时" in message:
                    logger.error("页面加载超时")
                    reason = "页面加载超时"
                else:
                    logger.error("职位解析失败")
                    reason = "职位解析失败"
                remember_failure(
                    url="",
                    title=summary["title"],
                    company=summary["company"],
                    stage="侧栏",
                    reason=reason,
                )
                log(f"{reason}: {summary['title']}")
                continue

            job = {
                "title": summary["title"],
                "company": summary["company"],
                "salary": summary["salary"] or panel.get("salary") or "",
                "location": summary["location"] or panel.get("location") or "",
                "education": panel.get("education") or "",
                "experience": panel.get("experience") or "",
                "description": "",
                "company_size": company_size_text(panel_html),
                "hr_activity": panel.get("hr_activity") or "",
                "url": panel.get("url") or "",
                "source": SOURCE_NAME,
                "collected_at": now_text(),
            }
            detail_description = ""
            if fetch_detail and job["url"]:
                try:
                    detail = _read_detail(context, job["url"], classes)
                    detail_description = detail.get("description") or ""
                    job["education"] = detail.get("education") or job["education"]
                    job["experience"] = detail.get("experience") or job["experience"]
                    job["salary"] = detail.get("salary") or job["salary"]
                    job["location"] = job["location"] or detail.get("location") or ""
                    if detail.get("company"):
                        job["company"] = summary["company"] or detail["company"]
                    if detail.get("company_size"):
                        job["company_size"] = detail["company_size"]
                    if detail.get("hr_activity"):
                        job["hr_activity"] = detail["hr_activity"]
                except ManualActionRequired:
                    raise
                except Exception:
                    stats["jd_fallback"] += 1
                    logger.error("JD 获取失败: %s", summary["title"])
                    remember_failure(
                        url=job["url"],
                        title=summary["title"],
                        company=summary["company"],
                        stage="详情",
                        reason="JD 获取失败",
                    )
                    log(f"JD 获取失败: {summary['title']}")
            elif fetch_detail:
                stats["jd_fallback"] += 1
                logger.error("JD 获取失败: 详情链接缺失 %s", summary["title"])
                remember_failure(
                    url="",
                    title=summary["title"],
                    company=summary["company"],
                    stage="详情",
                    reason="JD 获取失败: 详情链接缺失",
                )
                log(f"JD 获取失败: {summary['title']} 没有详情链接")
            elif job["url"] and not job["company_size"]:
                try:
                    job["company_size"] = _read_company_size(context, job["url"])
                except ManualActionRequired:
                    raise
                except Exception:
                    logger.error("公司规模获取失败: %s", summary["title"])
                    log(f"公司规模没读到: {summary['title']}")

            job["description"] = compose_description(detail_description, panel.get("description") or "")
            if not job["description"]:
                stats["failed"] += 1
                if count_handled:
                    stats["handled"] += 1
                logger.error("JD 获取失败: 描述为空 %s", summary["title"])
                log(f"采集失败: {summary['title']} 没有职位描述")
                continue

            _note_apply_button(job, page, log)
            try:
                outcome = database.upsert_job(job)
            except Exception:
                stats["failed"] += 1
                if count_handled:
                    stats["handled"] += 1
                logger.error("数据库保存失败")
                remember_failure(
                    url=job["url"],
                    title=job["title"],
                    company=job["company"],
                    stage="数据库",
                    reason="数据库保存失败",
                )
                log(f"数据库保存失败: {job['title']}")
                continue
            last_url = job["url"]
            last_signature = signature
            last_card_index = index
            cursor = index + 1
            stats["next_index"] = cursor
            known.add(dedup_key_for(job))
            known.add(combo_key_for(job))
            if outcome == "inserted":
                stats["collected"] += 1
                log(f"已采集 {stats['collected']} {job['title']} / {job['company']}")
            else:
                stats["duplicated"] += 1
                logger.info("职位重复: %s / %s", job["title"], job["company"])
                log(f"职位重复: {job['title']} / {job['company']}")
            if _mark_already_applied(database, job, log):
                stats["already_applied"] += 1
            if on_job is not None:
                on_job(page, job)
            if count_handled:
                stats["handled"] += 1
            _write_checkpoint(stats, last_url, last_signature, last_card_index)
            finished = stats["handled" if count_handled else "collected"]
            if finished < max_count:
                _reading_gap(stop_event, pause_event, 0.25 if not fetch_detail else random.uniform(1.0, 2.0))

        if stats["handled" if count_handled else "collected"] >= max_count:
            break
        previous = count
        updated = _scroll_for_more(page, classes, previous)
        scrolls += 1
        if updated <= previous or page_says_no_more(page):
            stats["exhausted"] = True
            log("这一页没有更多岗位了")
            break
        log(f"继续下拉，当前列表 {updated} 个职位")
    stats["stopped"] = stop_event.is_set()
    _write_checkpoint(stats, last_url, last_signature, last_card_index)
    if on_stats:
        on_stats(dict(stats))
    return stats
