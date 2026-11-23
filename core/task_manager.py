"""Single worker for the visible browser, collection, and AI queue."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from pathlib import Path

from core.application import cancel_application, confirm_application, enqueue_recommended
from core.browser import BrowserSession, ManualActionRequired, browser_was_closed
from core.collector import CollectionStopped, collect_jobs
from core.database import Database, application_can_retry
from core.importer import import_csv
from core.logging_config import get_logger
from core.paths import RESUME_SOURCE_PATH
from core.profile_store import load_profile, save_profile
from core.resume_parser import extract_resume_text, redact_personal_info

logger = get_logger("app")


def _split_terms(text: str) -> list[str]:
    found: list[str] = []
    normalized = str(text or "").replace("，", ",").replace("、", ",").replace("\n", ",")
    for part in normalized.split(","):
        item = part.strip()
        if item and item not in found:
            found.append(item)
    return found


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


class CollectController:
    def __init__(self, database: Database | None = None):
        self.db = database or Database()
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.pause_event = threading.Event()
        self.stop_event = threading.Event()
        self._watch = False
        self._collect_interrupted = False
        self._page_mark = ""
        self._list_head = ""
        self._card_cursor = 0
        self._sent_urls: set[str] = set()
        self._model_give_up: set[int] = set()
        self._busy = False
        self._active_task = "采集中"
        self._stopping = False
        self._seen_jobs = 0
        self._seen_applied = 0
        self._session_collected = 0
        self._session_applied = 0
        self._notice = "空闲。可以点「筛选并投递」或「仅采集」。"
        self.logs: deque[str] = deque(maxlen=200)
        self.snapshot = {
            "browser": "未启动",
            "login": "未检测",
            "task": "空闲",
            "progress": "0/0",
            "collected": 0,
            "duplicated": 0,
            "failed": 0,
            "screened": 0,
            "applied": 0,
        }

    def _set(self, **values) -> None:
        with self._lock:
            values.pop("collected", None)
            values.pop("applied", None)
            previous = self.snapshot.get("task")
            self.snapshot.update(values)
            self.snapshot["collected"] = self._session_collected
            self.snapshot["applied"] = self._session_applied
            task = self.snapshot.get("task")
            if task in {"空闲", "已停止", "失败", "等待用户"}:
                was_stopping = self._stopping
                self._stopping = False
                if was_stopping or previous in {"筛选投递中", "采集中", "已暂停", "分析中", "启动浏览器", "重新加载"} or task in {"已停止", "失败", "等待用户"}:
                    if task != previous or was_stopping or task in {"已停止", "失败", "等待用户"}:
                        self._notice = self._result_text(previous, task, was_stopping)
            elif task in {"筛选投递中", "采集中", "已暂停", "分析中", "启动浏览器", "重新加载"}:
                self._notice = self._busy_text()

    def _note_list_cleared(self) -> None:
        """List rows were deleted. Keep the session totals and count later inserts as new."""
        with self._lock:
            self._seen_jobs = 0
            self._seen_applied = 0

    def _submitted_count(self) -> int:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'submitted'").fetchone()
        return int(row[0] or 0)

    def _busy_text(self) -> str:
        task = self.snapshot.get("task") or ""
        progress = self.snapshot.get("progress") or ""
        active = self._active_task
        if self._stopping:
            if task == "筛选投递中" or (task == "已暂停" and active == "筛选投递中"):
                return f"正在停止筛选并投递，等当前岗位处理完。进度 {progress}。"
            if task == "采集中" or (task == "已暂停" and active == "采集中"):
                return f"正在停止仅采集，等当前岗位处理完。进度 {progress}。"
            return f"正在停止当前任务，等它处理完。进度 {progress}。"
        if task == "筛选投递中":
            return f"正在筛选并投递，本轮进度 {progress}。"
        if task == "采集中":
            return f"正在仅采集，进度 {progress}。"
        if task == "已暂停":
            name = "筛选并投递" if active == "筛选投递中" else "仅采集"
            return f"已暂停{name}，进度 {progress}。"
        if task == "启动浏览器":
            return "正在启动浏览器。"
        if task == "重新加载":
            return "正在重新加载浏览器页面。"
        if task == "分析中":
            return "正在分析已采集的岗位。"
        return f"当前任务：{task}。"

    def _result_text(self, previous: str, task: str, was_stopping: bool) -> str:
        progress = self.snapshot.get("progress") or ""
        applied = self._session_applied
        collected = self._session_collected
        active = self._active_task
        if task == "等待用户":
            return "等待你在浏览器里处理登录或验证。处理完再点「筛选并投递」或「仅采集」。"
        if task == "失败":
            return "任务失败了。看下面的实时日志，处理完可以再试。"
        if previous == "启动浏览器":
            if self.snapshot.get("browser") == "已启动":
                return f"浏览器已启动，登录状态：{self.snapshot.get('login')}。可以点「筛选并投递」或「仅采集」。"
            return "浏览器没有启动成功。看实时日志里的原因。"
        if previous == "重新加载":
            if self.snapshot.get("browser") == "已启动":
                return f"浏览器页面已重新加载，登录状态：{self.snapshot.get('login')}。"
            return "重新加载没有完成。浏览器可能已关闭。"
        if was_stopping or task == "已停止":
            if active == "筛选投递中" or previous == "筛选投递中":
                return f"筛选并投递已停止，当前岗位已处理完。进度 {progress}。这次启动后累计已投递 {applied} 个。"
            if active == "采集中" or previous == "采集中":
                return f"仅采集已停止，当前岗位已处理完。进度 {progress}。这次启动后累计已采集 {collected} 个。"
            return f"当前任务已处理完并停止。进度 {progress}。"
        if previous == "筛选投递中" or active == "筛选投递中":
            return f"筛选并投递这一轮结束了。进度 {progress}。这次启动后累计已投递 {applied} 个，已采集 {collected} 个。"
        if previous == "采集中" or active == "采集中":
            return f"仅采集结束了。进度 {progress}。这次启动后累计已采集 {collected} 个。"
        if previous == "分析中":
            return "分析结束了。看实时日志里的结果。"
        return self._notice or "空闲。可以点「筛选并投递」或「仅采集」。"

    def status_text(self) -> str:
        with self._lock:
            task = self.snapshot.get("task")
            if self._stopping or task in {"筛选投递中", "采集中", "已暂停", "分析中", "启动浏览器", "重新加载"}:
                return self._busy_text()
            return self._notice or "空闲。可以点「筛选并投递」或「仅采集」。"

    def _keep_totals(self) -> None:
        self.snapshot["collected"] = self._session_collected
        self.snapshot["applied"] = self._session_applied

    def _set_busy(self, busy: bool) -> None:
        with self._lock:
            self._busy = busy

    def _log(self, message: str) -> None:
        text = str(message)
        with self._lock:
            self.logs.append(text)
        logger.info(text)

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="jobhunter-worker", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        browser = BrowserSession(headless=False)
        while True:
            try:
                command = self._queue.get(timeout=2)
            except queue.Empty:
                self._watch_page(browser)
                continue
            name = command[0]
            try:
                if name == "shutdown":
                    self.clear_resume_data()
                    browser.close()
                    break
                if name == "start_browser":
                    self._start_browser(browser)
                elif name == "refresh_login":
                    self._refresh_login(browser)
                elif name == "collect":
                    self._collect(browser, int(command[1]), batch=False)
                elif name == "batch":
                    self._collect(browser, int(command[1]), batch=True)
                elif name == "refresh_batch":
                    self._collect(browser, int(command[1]), batch=True, refresh=True)
                elif name == "apply":
                    self._screen_and_apply(browser, int(command[1]))
                elif name == "clear_jobs":
                    self._clear_jobs_only()
                elif name == "crawl":
                    self._crawl_current_page(browser)
                elif name == "analyze":
                    self._analyze()
                elif name == "close_browser":
                    self._watch = False
                    browser.close()
                    self.clear_resume_data()
                    self._set(browser="未启动", login="未检测", task="空闲")
                    self._forget_jobs("浏览器已关闭，职位列表已清空")
                    with self._lock:
                        self._notice = "浏览器已关闭。已采集和已投递仍是这次启动后的累计。"
                elif name == "reload_page":
                    self._reload_page(browser)
            except Exception as exc:
                if browser_was_closed(exc) or not browser.is_alive():
                    browser.close()
                    self._watch = False
                    self._set(browser="未启动", login="未检测", task="空闲")
                    self._forget_jobs("浏览器窗口已关闭，职位列表已清空")
                else:
                    self._log(f"任务失败: {exc}")
                    self._set(task="失败")
            finally:
                if name != "shutdown":
                    self._set_busy(False)

    def _forget_jobs(self, reason: str) -> None:
        """Drop the on-screen list. Jobs only exist while the browser session is open."""
        had_jobs = self.db.count_jobs() > 0
        self._sent_urls.clear()
        self._page_mark = ""
        self._list_head = ""
        self._card_cursor = 0
        self._collect_interrupted = False
        self.db.clear_job_list()
        self._note_list_cleared()
        self._set(duplicated=0, failed=0, screened=0, progress="0/0")
        if had_jobs:
            self._log(reason)

    def _on_browser_disconnected(self) -> None:
        with self._lock:
            task = self.snapshot["task"]
            self.snapshot["browser"] = "未启动"
            self.snapshot["login"] = "未检测"
            idle = task not in {"采集中", "已暂停", "筛选投递中", "分析中", "启动浏览器"}
            if idle:
                self.snapshot["task"] = "空闲"
        self._forget_jobs("浏览器已关闭，职位列表已清空")

    def _start_browser(self, browser: BrowserSession) -> None:
        self._set(task="启动浏览器")
        try:
            if browser.is_alive():
                status = browser.classify()
                self._set(browser="已启动", login=status, task="空闲")
                self._log("浏览器已在运行")
                self._watch = True
                self._remember_page(browser)
                if status != "已登录":
                    self._log("请在打开的浏览器里手动登录，然后点「仅采集」。")
                else:
                    self._log("已登录。点「仅采集」后再抓当前页职位。")
                return
            browser.start(on_closed=self._on_browser_disconnected)
            status = browser.open_entry(self._entry_url())
        except Exception as exc:
            logger.error("浏览器启动失败")
            browser.close()
            if browser_was_closed(exc):
                self._set(browser="未启动", login="未检测", task="空闲")
                self._log("浏览器窗口已关闭，可以重新启动")
                return
            self._set(browser="启动失败", login="未检测", task="空闲")
            self._log(f"浏览器启动失败: {exc}")
            return
        self._set(browser="已启动", login=status, task="空闲")
        self._log(f"浏览器已启动，登录状态：{status}")
        self._watch = True
        self._remember_page(browser)
        if status == "已登录":
            self._log("已登录。只有点「仅采集」或「筛选并投递」才会采集。")
            return
        self._log("请在打开的浏览器里手动登录。登录后点「仅采集」。")

    def _refresh_login(self, browser: BrowserSession) -> None:
        if not browser.is_alive():
            browser.close()
            self._set(browser="未启动", login="未检测", task="空闲")
            self._log("浏览器窗口已关闭，可以重新启动")
            return
        try:
            status = browser.classify()
            if status != "已登录":
                status = browser.open_entry(self._entry_url())
        except Exception as exc:
            if browser_was_closed(exc) or not browser.is_alive():
                browser.close()
                self._set(browser="未启动", login="未检测", task="空闲")
                self._log("浏览器窗口已关闭，可以重新启动")
                return
            logger.warning("登录状态异常: %s", type(exc).__name__)
            self._log(f"登录状态异常: {exc}")
            self._set(task="空闲")
            return
        self._set(browser="已启动", login=status, task="空闲")
        self._log(f"登录状态：{status}")

    def _entry_url(self) -> str:
        profile = load_profile()
        return (profile.get("target_url") or "").strip() or "https://www.zhaopin.com/jobs/?pageMode=recommend"

    def _bump(self, key: str) -> None:
        with self._lock:
            self.snapshot[key] = int(self.snapshot.get(key) or 0) + 1

    def _on_job(self, profile: dict, force_rescreen: bool = False):
        from core.ai_filter import AIError, analyze_job, local_rejection
        from core.application import should_auto_apply
        from core.auto_apply import apply_current_job
        from core.rules import evaluate_rules

        resume_text = self.db.resume_text()

        def handle(page, job: dict) -> None:
            job_id = self.db.find_job_id(job)
            if job_id is None:
                logger.error("数据库保存失败")
                self._log(f"数据库保存失败: {job.get('title')}")
                return
            stored = self.db.get_job(job_id) or job
            decision = evaluate_rules(stored, profile)
            needs_model = force_rescreen or stored.get("ai_score") is None or (
                bool(stored.get("ai_hard_reject")) and not decision.hard_reject
            )
            if decision.hard_reject:
                result = local_rejection(stored, profile)
                self.db.save_analysis(job_id, result)
            elif needs_model:
                try:
                    result = analyze_job(stored, profile, resume_text)
                    self.db.save_analysis(job_id, result)
                except AIError as exc:
                    self._model_give_up.add(job_id)
                    self.db.record_application(job_id, "failed", "模型暂时失败，下次筛选会重试")
                    self._log(f"筛选暂时失败 {job.get('title')}：{exc}。本轮先跳过，下次再试")
                    return
            else:
                result = {
                    "score": int(stored["ai_score"]),
                    "recommendation": stored.get("ai_recommendation") or "",
                    "hard_reject": bool(stored.get("ai_hard_reject")),
                    "unknown_fields": [],
                }
            self._bump("screened")
            prior = job.get("prior_apply") or "未识别"
            self._log(f"筛选 {job.get('title')}：筛选前{prior}，{result['score']} {result['recommendation']}")
            ok, reason = should_auto_apply(result, profile)
            if not ok:
                self.db.record_application(job_id, "skipped", reason)
                self._log(f"不投递 {job.get('title')}：{reason}")
                return
            outcome, detail = apply_current_job(page)
            if outcome in {"submitted", "already"}:
                self.db.record_application(job_id, "submitted", detail)
                self._log(f"{'已投递' if outcome == 'submitted' else '已经投过'} {job.get('title')}：{detail}")
                return
            logger.error("投递任务失败")
            self.db.record_application(job_id, "failed", detail)
            self._log(f"投递失败 {job.get('title')}：{detail}")

        return handle

    def _capture_sent(self) -> None:
        try:
            self._sent_urls.update(self.db.submitted_urls())
        except Exception:
            logger.error("读取数据库失败")

    def _mark_already_sent(self) -> None:
        if not self._sent_urls:
            return
        for job in self.db.jobs_in_collect_order():
            url = job.get("url") or ""
            if url in self._sent_urls and job.get("application_status") != "submitted":
                self.db.record_application(int(job["id"]), "submitted", "本轮已经投递过")

    def _remember_page(self, browser: BrowserSession) -> None:
        if not browser.is_alive():
            return
        try:
            from core.collector import list_mark

            self._page_mark = list_mark(browser.page) or self._page_mark
        except Exception:
            return

    def _auto_collect(self, browser: BrowserSession, replace: bool) -> None:
        from core.collector import collect_jobs

        self._set_busy(True)
        self._set(task="采集中", progress="采集中")
        try:
            if replace:
                self._capture_sent()
                self.db.clear_job_list()
                self._note_list_cleared()
                self._collect_interrupted = False
                self._card_cursor = 0
                self._list_head = ""
                self._log("已清空旧职位列表，开始采这一批")
            status = browser.ensure_collection_allowed(self._entry_url())
            if status != "已登录":
                raise ManualActionRequired(status)
            self._set(login=status)

            def on_stats(stats: dict) -> None:
                self._set(
                    duplicated=stats["duplicated"],
                    failed=stats["failed"],
                    progress=f"本轮新增 {stats['collected']}",
                )

            resume = not replace and (self._collect_interrupted or self.db.count_jobs() > 0)
            stats = collect_jobs(
                browser.page,
                browser.context,
                self.db,
                500,
                self.stop_event,
                self.pause_event,
                on_log=self._log,
                on_stats=on_stats,
                fetch_detail=False,
                fresh=not resume,
            )
            self._mark_already_sent()
            if stats.get("stopped") or self.stop_event.is_set():
                self._collect_interrupted = True
                self._set(task="已停止", progress=f"本轮新增 {stats.get('collected', 0)}")
                self._log(
                    f"采集已暂停：当前列表 {self.db.count_jobs()} 个，"
                    f"其中页面显示继续沟通 {stats.get('already_applied', 0)} 个。再点仅采集会接着往下采。"
                )
                return
            self._collect_interrupted = False
            self._set(task="空闲", progress=f"本轮新增 {stats.get('collected', 0)}")
            self._log(
                f"采集完成：新增 {stats['collected']} 个，继续沟通/已投过 {stats.get('already_applied', 0)} 个。"
                "可以开始筛选投递。"
            )
        except ManualActionRequired as exc:
            logger.warning("登录状态异常: %s", exc)
            self._set(login=str(exc), task="等待用户")
            self._log(f"登录状态异常: {exc}。自动化已暂停，请在浏览器中手动处理。")
        except CollectionStopped:
            self._collect_interrupted = True
            self._set(task="已停止", progress="已停止")
            self._log("仅采集已停止，再点仅采集会从当前位置继续")
        finally:
            self._remember_page(browser)
            self._set_busy(False)

    def _watch_page(self, browser: BrowserSession) -> None:
        """Only refresh login/browser status. Never auto-collect on page changes."""
        if not self._watch:
            return
        with self._lock:
            if self._busy:
                return
        if not browser.is_alive():
            self._watch = False
            self._set(browser="未启动", login="未检测", task="空闲")
            return
        try:
            from core.collector import list_mark

            status = browser.classify()
            self._set(browser="已启动", login=status)
            if status != "已登录":
                return
            mark = list_mark(browser.page)
        except Exception as exc:
            if browser_was_closed(exc) or not browser.is_alive():
                browser.close()
                self._watch = False
                self._set(browser="未启动", login="未检测", task="空闲")
                self._log("浏览器窗口已关闭，可以重新启动")
            return
        if mark:
            self._page_mark = mark

    def _wait_if_paused(self) -> None:
        while self.pause_event.is_set() and not self.stop_event.is_set():
            time.sleep(0.2)
        if self.stop_event.is_set():
            raise CollectionStopped()

    def _reading_pause(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            self._wait_if_paused()
            time.sleep(0.2)

    def _apply_jobs_once(self, browser: BrowserSession, profile: dict, resume_text: str, target: int, applied: int) -> int:
        from core.ai_filter import AIError, analyze_job, local_rejection
        from core.application import should_auto_apply
        from core.auto_apply import apply_current_job
        from core.collector import focus_job
        from core.rules import evaluate_rules

        for job in self.db.jobs_pending_apply():
            self._wait_if_paused()
            if applied >= target:
                break
            status = job.get("application_status") or ""
            job_id = int(job["id"])
            if status in {"submitted", "skipped"} or job_id in self._model_give_up:
                continue
            if status == "failed" and not application_can_retry(status, job.get("application_reason") or ""):
                continue
            url = job.get("url") or ""
            if url and url in self._sent_urls:
                self.db.record_application(int(job["id"]), "submitted", "本轮已经投递过")
                continue
            decision = evaluate_rules(job, profile)
            if decision.hard_reject:
                result = local_rejection(job, profile)
                self.db.save_analysis(job_id, result)
            elif job.get("ai_score") is None:
                try:
                    result = analyze_job(job, profile, resume_text)
                    self.db.save_analysis(job_id, result)
                except AIError as exc:
                    self._model_give_up.add(job_id)
                    self.db.record_application(job_id, "failed", "模型暂时失败，下次筛选会重试")
                    self._log(f"筛选暂时失败 {job.get('title')}：{exc}。本轮先跳过，下次再试")
                    continue
            else:
                result = {
                    "score": int(job["ai_score"]),
                    "recommendation": job.get("ai_recommendation") or "",
                    "hard_reject": bool(job.get("ai_hard_reject")),
                    "unknown_fields": [],
                }
            self._bump("screened")
            prior = job.get("prior_apply") or "未识别"
            self._log(f"筛选 {job.get('title')}：筛选前{prior}，{result['score']} {result['recommendation']}")
            ok, reason = should_auto_apply(result, profile)
            if not ok:
                self.db.record_application(job_id, "skipped", reason)
                self._log(f"不投递 {job.get('title')}：{reason}")
                continue
            try:
                opened = focus_job(browser.page, job.get("title") or "", job.get("company") or "")
            except Exception as exc:
                if browser_was_closed(exc) or not browser.is_alive():
                    raise
                opened = False
            if not opened:
                self.db.record_application(job_id, "failed", "当前页面没有找到这个职位")
                self._log(f"投递失败 {job.get('title')}：当前页面没有找到这个职位")
                continue
            outcome, detail = apply_current_job(browser.page)
            if outcome in {"submitted", "already"}:
                self.db.record_application(job_id, "submitted", detail)
                if url:
                    self._sent_urls.add(url)
                if outcome == "submitted":
                    applied += 1
                self._set(progress=f"{applied}/{target}")
                self._log(f"{'已投递' if outcome == 'submitted' else '已经投过'} {job.get('title')}：{detail}")
                continue
            logger.error("投递任务失败")
            self.db.record_application(job_id, "failed", detail)
            self._log(f"投递失败 {job.get('title')}：{detail}")
        return applied

    def _collect_next_job(self, browser: BrowserSession) -> int:
        """Collect exactly one new job. Does not clear the existing list."""
        from core.collector import collect_jobs

        status = browser.ensure_collection_allowed(self._entry_url())
        if status != "已登录":
            raise ManualActionRequired(status)
        self._set(login=status, task="筛选投递中")
        from core.collector import list_mark

        head = list_mark(browser.page)
        if head != self._list_head:
            self._card_cursor = 0
            self._list_head = head

        stats = collect_jobs(
            browser.page,
            browser.context,
            self.db,
            1,
            self.stop_event,
            self.pause_event,
            on_log=self._log,
            on_stats=None,
            fetch_detail=False,
            fresh=False,
            start_index=self._card_cursor,
        )
        self._mark_already_sent()
        self._remember_page(browser)
        if stats.get("stopped") or self.stop_event.is_set():
            raise CollectionStopped()
        if stats.get("exhausted"):
            self._card_cursor = 0
        else:
            self._card_cursor = int(stats.get("next_index") or 0)
        return int(stats.get("collected") or 0), bool(stats.get("exhausted"))

    def _screen_and_apply(self, browser: BrowserSession, target: int) -> None:
        if not browser.is_alive():
            browser.close()
            self._set(browser="未启动", login="未检测", task="空闲")
            self._log("浏览器窗口已关闭，请重新启动")
            return
        target = max(1, min(int(target), 200))
        self._model_give_up.clear()
        self._set(task="筛选投递中", progress=f"0/{target}", screened=0)
        self._capture_sent()
        profile = load_profile()
        resume_text = self.db.resume_text()
        applied = 0
        try:
            existing = self.db.count_jobs()
            if existing > 0:
                self._log(f"先筛选并投递已采集的 {existing} 个岗位")
                applied = self._apply_jobs_once(browser, profile, resume_text, target, applied)
                self._set(progress=f"{applied}/{target}")

            while applied < target and not self.stop_event.is_set():
                self._wait_if_paused()
                self._log(f"已投 {applied}/{target}，采集下一个 → AI 筛选 → 通过再投")
                got, exhausted = self._collect_next_job(browser)
                self._set_busy(True)
                self._set(task="筛选投递中", progress=f"{applied}/{target}")
                if got > 0:
                    applied = self._apply_jobs_once(browser, profile, resume_text, target, applied)
                    self._set(progress=f"{applied}/{target}")
                if applied >= target or self.stop_event.is_set():
                    break
                if got > 0 and not exhausted:
                    continue
                self._set(task="空闲", progress=f"{applied}/{target}")
                self._log(
                    f"这一页已经没有新岗位，停止投递，本轮投出 {applied}/{target}。"
                    "浏览器保持打开，请自己修改条件并刷出新岗位，再点「筛选并投递」。"
                )
                return
        except ManualActionRequired as exc:
            self._set(login=str(exc), task="等待用户")
            self._log(f"登录状态异常: {exc}。自动化已暂停，请在浏览器中手动处理。")
            return
        except CollectionStopped:
            self._set(task="已停止", progress=f"{applied}/{target}")
            self._log(f"已停止，本轮投出 {applied}/{target}")
            return
        self._set(task="空闲", progress=f"{applied}/{target}")
        if applied >= target:
            self._log(f"本轮已投出 {applied} 个，达到设定数量")
        else:
            self._log(f"续投结束，实际投出 {applied}/{target}")

    def _clear_jobs_only(self) -> None:
        self._sent_urls.clear()
        self._page_mark = ""
        self._list_head = ""
        self._card_cursor = 0
        self._collect_interrupted = False
        self.db.clear_job_list()
        self._note_list_cleared()
        self._set(duplicated=0, failed=0, screened=0, progress="0/0", task="空闲")
        self._log("已清空当前职位，不会自动采集")

    def _crawl_current_page(self, browser: BrowserSession) -> None:
        if not browser.is_alive():
            self._set(browser="未启动", login="未检测", task="空闲")
            self._log("浏览器未打开，请先启动浏览器")
            return
        try:
            status = browser.classify()
        except Exception as exc:
            if browser_was_closed(exc) or not browser.is_alive():
                browser.close()
                self._watch = False
                self._set(browser="未启动", login="未检测", task="空闲")
                self._log("浏览器窗口已关闭，可以重新启动")
                return
            self._set(task="空闲")
            self._log(f"登录状态异常: {exc}")
            return
        self._set(login=status)
        if status != "已登录":
            self._set(task="等待用户")
            self._log("请先登录，登录后再点「仅采集」。")
            return
        resume = self._collect_interrupted and self.db.count_jobs() > 0
        if resume:
            self._log("从上次停下的位置继续采集，已入库岗位会跳过")
            self._auto_collect(browser, replace=False)
        else:
            self._log("正在仅采集当前页面职位")
            self._auto_collect(browser, replace=True)

    def _collect(self, browser: BrowserSession, max_count: int, batch: bool = False, refresh: bool = False) -> None:
        if not browser.is_alive():
            browser.close()
            self._set(browser="未启动", login="未检测", task="空闲")
            self._log("浏览器窗口已关闭，请重新启动")
            return
        try:
            if refresh:
                status = browser.refresh_job_list(self._entry_url())
                self._log("推荐页已刷新，正在采集这一批新职位")
                if status != "已登录":
                    raise ManualActionRequired(status)
                self.db.clear_job_list()
                self._note_list_cleared()
                self._log("已清空旧职位列表，只保留这次刷新出来的职位")
            else:
                status = browser.ensure_collection_allowed(self._entry_url())
        except ManualActionRequired as exc:
            logger.warning("登录状态异常: %s", exc)
            self._set(login=str(exc), task="等待用户")
            self._log(f"登录状态异常: {exc}。自动化已暂停，请在浏览器中手动处理。")
            return
        task_name = "筛选投递中" if batch else "采集中"
        self._set(login=status, task=task_name, progress=f"0/{max_count}")
        profile = load_profile()

        def on_stats(stats: dict) -> None:
            done = stats["handled"] if batch else stats["collected"]
            self._set(
                duplicated=stats["duplicated"],
                failed=stats["failed"],
                progress=f"{done}/{max_count}",
            )

        try:
            stats = collect_jobs(
                browser.page,
                browser.context,
                self.db,
                max_count,
                self.stop_event,
                self.pause_event,
                on_log=self._log,
                on_stats=on_stats,
                on_job=self._on_job(profile, force_rescreen=refresh) if batch else None,
                count_handled=batch,
            )
        except ManualActionRequired as exc:
            logger.warning("登录状态异常: %s", exc)
            self._set(login=str(exc), task="等待用户")
            self._log(f"登录状态异常: {exc}。自动化已暂停，请在浏览器中手动处理。")
            return
        except CollectionStopped:
            self._set(task="已停止")
            self._log("采集已停止")
            return
        if stats.get("stopped") or self.stop_event.is_set():
            self._set(task="已停止")
            self._log("任务已停止")
            return
        if batch:
            self._set(task="空闲")
            self._log(
                f"批量结束：处理 {stats['handled']}，新增 {stats['collected']}，"
                f"重复 {stats['duplicated']}，失败 {stats['failed']}"
            )
            return
        self._log(
            f"采集完成：新增 {stats['collected']}，重复 {stats['duplicated']}，失败 {stats['failed']}。"
            "进入 AI 分析队列"
        )
        self._analyze()

    def _analyze(self) -> None:
        from core.ai_filter import analyze_pending

        self._set(task="分析中")
        profile = load_profile()
        result = analyze_pending(self.db, profile, stop_event=self.stop_event, on_log=self._log)
        if self.stop_event.is_set():
            self._set(task="已停止")
            self._log("分析已停止")
            return
        self._set(task="空闲")
        self._log(f"分析完成：成功 {result['analyzed']}，失败 {result['failed']}")

    def start_browser(self) -> str:
        with self._lock:
            if self._busy:
                self._notice = "已有任务在执行，不能同时启动浏览器"
                return self._notice
            self._busy = True
            self.stop_event.clear()
            self._stopping = False
            self.snapshot["task"] = "启动浏览器"
            self._notice = self._busy_text()
        self._ensure_thread()
        self._queue.put(("start_browser",))
        return self.status_text()

    def reload_browser(self) -> str:
        with self._lock:
            if self.snapshot["browser"] != "已启动":
                self._notice = "请先启动浏览器"
                return self._notice
            if self._busy:
                self._notice = "任务进行中，请先停止再重新加载"
                return self._notice
            self._busy = True
            self.stop_event.clear()
            self._stopping = False
            self.snapshot["task"] = "重新加载"
            self._notice = self._busy_text()
        self._ensure_thread()
        self._queue.put(("reload_page",))
        return self.status_text()

    def _reload_page(self, browser: BrowserSession) -> None:
        if not browser.is_alive():
            browser.close()
            self._set(browser="未启动", login="未检测", task="空闲")
            self._log("浏览器未打开，请先启动浏览器")
            return
        self._log("点击浏览器重新加载")
        try:
            status = browser.refresh_job_list(self._entry_url())
        except Exception as exc:
            if browser_was_closed(exc) or not browser.is_alive():
                browser.close()
                self._watch = False
                self._set(browser="未启动", login="未检测", task="空闲")
                self._forget_jobs("浏览器窗口已关闭，职位列表已清空")
                return
            self._set(task="空闲")
            self._log(f"重新加载失败: {exc}")
            return
        self._set(browser="已启动", login=status, task="空闲")
        self._remember_page(browser)
        self._list_head = ""
        self._card_cursor = 0
        self._log(f"页面已重新加载，登录状态：{status}")

    def refresh_login(self) -> str:
        with self._lock:
            if self.snapshot["browser"] != "已启动":
                return "请先启动浏览器"
            if self._busy:
                return "任务进行中"
            self._busy = True
        self._ensure_thread()
        self._queue.put(("refresh_login",))
        return "正在检查登录状态"

    def start_collect(self, max_count=None) -> str:
        """Compatibility entry: without a browser this still asks the user to start one."""
        if max_count is not None:
            try:
                count = int(max_count)
            except (TypeError, ValueError):
                return "采集数量无效"
            count = max(1, min(300, count))
            with self._lock:
                if self.snapshot["browser"] != "已启动":
                    self._notice = "请先启动浏览器，并在窗口中完成登录"
                    return self._notice
                if self._busy:
                    self._notice = "已有采集或分析任务在运行"
                    return self._notice
                self._busy = True
                self.stop_event.clear()
                self.pause_event.clear()
                self._stopping = False
                self._active_task = "采集中"
                self.snapshot.update(
                    {"task": "采集中", "progress": f"0/{count}", "duplicated": 0, "failed": 0}
                )
                self._keep_totals()
                self._notice = self._busy_text()
            self._ensure_thread()
            self._queue.put(("collect", count))
            return self.status_text()
        return self.start_crawl()

    def crawl_button_label(self) -> str:
        with self._lock:
            task = self.snapshot["task"]
            active = self._active_task
        if task == "采集中" or (task == "已暂停" and active == "采集中"):
            return "停止采集"
        return "仅采集"

    def apply_button_label(self) -> str:
        with self._lock:
            task = self.snapshot["task"]
            active = self._active_task
        if task == "筛选投递中" or (task == "已暂停" and active == "筛选投递中"):
            return "停止投递"
        return "筛选并投递"

    def toggle_crawl(self) -> str:
        with self._lock:
            task = self.snapshot["task"]
            active = self._active_task
        if task == "采集中" or (task == "已暂停" and active == "采集中"):
            return self.stop()
        return self.start_crawl()

    def start_crawl(self) -> str:
        with self._lock:
            if self.snapshot["browser"] != "已启动":
                self._notice = "请先启动浏览器，并在窗口中完成登录"
                return self._notice
            if self._busy:
                self._notice = "已有采集或分析任务在运行"
                return self._notice
            resume = self._collect_interrupted and self.db.count_jobs() > 0
            self._busy = True
            self.stop_event.clear()
            self.pause_event.clear()
            self._stopping = False
            self._active_task = "采集中"
            self.snapshot.update(
                {
                    "task": "采集中",
                    "progress": "继续采集" if resume else "仅采集中",
                    "duplicated": 0,
                    "failed": 0,
                    "screened": 0,
                }
            )
            self._keep_totals()
            if resume:
                self._notice = "正在从上次停下的位置继续仅采集。"
            else:
                self._notice = "正在仅采集当前页。点「停止采集」会等当前这条处理完再停。"
        self._ensure_thread()
        self._queue.put(("crawl",))
        return self.status_text()

    def start_batch(self, count: int) -> str:
        count = max(1, min(int(count), 300))
        with self._lock:
            if self.snapshot["browser"] != "已启动":
                self._notice = "请先启动浏览器，并在窗口中完成登录"
                return self._notice
            if self._busy:
                self._notice = "已有采集或分析任务在运行"
                return self._notice
            self._busy = True
            self.stop_event.clear()
            self.pause_event.clear()
            self._stopping = False
            self._active_task = "筛选投递中"
            self.snapshot.update(
                {
                    "task": "筛选投递中",
                    "progress": f"0/{count}",
                    "duplicated": 0,
                    "failed": 0,
                    "screened": 0,
                }
            )
            self._keep_totals()
            self._notice = self._busy_text()
        self._ensure_thread()
        self._queue.put(("batch", count))
        return self.status_text()

    def start_apply(self, count) -> str:
        try:
            target = int(count)
        except (TypeError, ValueError):
            self._notice = "投递数量无效"
            return self._notice
        target = max(1, min(target, 200))
        with self._lock:
            if self.snapshot["browser"] != "已启动":
                self._notice = "请先启动浏览器，并在窗口中完成登录"
                return self._notice
            if self._busy:
                self._notice = "采集还在进行，请等这一页采完"
                return self._notice
            self._busy = True
            self.stop_event.clear()
            self.pause_event.clear()
            self._stopping = False
            self._active_task = "筛选投递中"
            self.snapshot.update(
                {
                    "task": "筛选投递中",
                    "progress": f"0/{target}",
                    "screened": 0,
                }
            )
            self._keep_totals()
            self._notice = f"正在筛选并投递，本轮进度 0/{target}。已投递按这次启动后的累计计算，不会清零。"
        self._ensure_thread()
        self._queue.put(("apply", target))
        return self.status_text()

    def clear_jobs(self) -> str:
        with self._lock:
            if self._busy:
                self._notice = "请先停止当前任务"
                return self._notice
            thread_alive = self._thread is not None and self._thread.is_alive()
            if thread_alive:
                self._busy = True
        if not thread_alive:
            self._clear_jobs_only()
            with self._lock:
                self._notice = "已清空当前职位。已采集和已投递仍是这次启动后的累计。需要时再点「仅采集」或「筛选并投递」。"
            return self._notice
        self._ensure_thread()
        self._queue.put(("clear_jobs",))
        with self._lock:
            self._notice = "正在清空列表，不会自动采集。已采集和已投递数字保持不变。"
        return self._notice

    def start_refresh_batch(self, count: int) -> str:
        count = max(1, min(int(count), 300))
        with self._lock:
            if self.snapshot["browser"] != "已启动":
                return "请先启动浏览器，并在窗口中完成登录"
            if self._busy:
                return "已有采集或分析任务在运行"
            self._busy = True
            self.stop_event.clear()
            self.pause_event.clear()
            self._active_task = "筛选投递中"
            self.snapshot.update(
                {
                    "task": "筛选投递中",
                    "progress": f"0/{count}",
                    "duplicated": 0,
                    "failed": 0,
                    "screened": 0,
                }
            )
            self._keep_totals()
            self._notice = self._busy_text()
        self._ensure_thread()
        self._queue.put(("refresh_batch", count))
        return "正在刷新推荐页。新出现的职位会重新采集，技能、职责和要求会重新交给 AI。已经投递过的不会再投。"

    def pause_or_resume(self) -> str:
        with self._lock:
            task = self.snapshot["task"]
            if task not in {"采集中", "已暂停", "筛选投递中"}:
                self._notice = "当前没有进行中的任务"
                return self._notice
            if self.pause_event.is_set():
                self.pause_event.clear()
                self.snapshot["task"] = self._active_task
                self._notice = self._busy_text()
                return self._notice
            self.pause_event.set()
            self.snapshot["task"] = "已暂停"
            self._notice = self._busy_text()
            return self._notice

    def stop(self) -> str:
        with self._lock:
            task = self.snapshot["task"]
            active = self._active_task
            if task not in {"采集中", "已暂停", "分析中", "启动浏览器", "筛选投递中"}:
                self._notice = "当前没有需要停止的任务"
                return self._notice
            self._stopping = True
            if task == "筛选投递中" or active == "筛选投递中":
                text = "正在停止筛选并投递，等当前岗位处理完再停。"
            elif task == "采集中" or active == "采集中":
                text = "正在停止仅采集，等当前岗位处理完再停。"
            else:
                text = "正在停止当前任务，等它处理完。"
            self._notice = text
        self.stop_event.set()
        self.pause_event.clear()
        self._log(text)
        return text

    def close_browser(self) -> str:
        with self._lock:
            if self.snapshot["browser"] != "已启动" and not self._busy:
                self._notice = "浏览器未启动"
                return self._notice
            if self._busy and self.snapshot["task"] in {"采集中", "已暂停", "分析中", "筛选投递中"}:
                self._notice = "请先停止当前任务"
                return self._notice
            self._busy = True
            self._notice = "正在关闭浏览器。"
        self.stop_event.set()
        self._ensure_thread()
        self._queue.put(("close_browser",))
        return "正在关闭浏览器。"

    def start_analysis(self) -> str:
        with self._lock:
            if self._busy:
                return "已有任务在运行"
            self._busy = True
            self.stop_event.clear()
            self.snapshot["task"] = "分析中"
        self._ensure_thread()
        self._queue.put(("analyze",))
        return "已进入 AI 分析队列"

    def enqueue(self) -> str:
        try:
            result = enqueue_recommended(self.db, load_profile())
        except Exception as exc:
            logger.error("投递任务失败")
            return f"投递任务失败: {exc}"
        return (
            f"待确认 {result['queued']}，重复 {result['duplicated']}，未通过检查 {result['blocked']}。"
            "程序不会自动点击立即投递。"
        )

    def confirm(self, application_id: int) -> str:
        return confirm_application(self.db, int(application_id))

    def cancel(self, application_id: int) -> str:
        return cancel_application(self.db, int(application_id))

    def import_jobs(self, path: str) -> str:
        stats = import_csv(self.db, Path(path))
        return f"导入完成：新增 {stats['inserted']}，重复 {stats['duplicated']}，失败 {stats['failed']}"

    def clear_resume_data(self) -> str:
        self.db.clear_resume()
        try:
            if RESUME_SOURCE_PATH.exists():
                RESUME_SOURCE_PATH.unlink()
        except Exception:
            logger.error("简历文件删除失败")
        self._log("已清空本地简历")
        return "已清空本地简历"

    def save_resume(self, path: str) -> str:
        file_path = Path(path)
        text = redact_personal_info(extract_resume_text(file_path))
        if not text:
            return "PDF 中没有解析到文本"
        RESUME_SOURCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            if file_path.resolve() != RESUME_SOURCE_PATH.resolve():
                RESUME_SOURCE_PATH.write_bytes(file_path.read_bytes())
        except Exception:
            logger.error("简历副本保存失败")
        self.db.save_resume("resume.pdf", text)
        return f"已保存简历文本 {len(text)} 字。联系方式已去掉，项目经历只使用这份原文。关闭程序时会自动清空。"

    def save_profile_fields(
        self,
        roles: str,
        cities: str,
        salary_min,
        salary_max,
        school: str,
        major: str,
        degree: str,
        graduation_year,
        skills: str,
    ) -> str:
        profile = load_profile()
        profile["target_roles"] = [line.strip() for line in str(roles).splitlines() if line.strip()]
        profile["cities"] = [part.strip() for part in str(cities).replace("，", ",").split(",") if part.strip()]
        profile["salary_min_k"] = int(salary_min)
        profile["salary_max_k"] = int(salary_max) or None
        education = dict(profile.get("education") or {})
        education.update(
            {
                "school": school.strip(),
                "major": major.strip(),
                "degree": degree.strip(),
                "graduation_year": int(graduation_year),
            }
        )
        profile["education"] = education
        profile["skills"] = [part.strip() for part in str(skills).replace("，", ",").split(",") if part.strip()]
        save_profile(profile)
        return "求职画像已保存。项目经历只读取已上传简历。"

    def save_filters(
        self,
        target_url: str,
        cities: str,
        salary_floor,
        salary_min,
        roles: str,
        min_apply_score,
        experience_years,
        auto_apply: bool,
        blocked_titles: str,
        blocked_keywords: str,
        preferred_keywords: str,
        min_company_size,
        prefer_active_hr: bool = True,
        hr_activity_max: str = "本月活跃",
        job_types=None,
        prefer_salary_uncapped: bool = True,
    ) -> str:
        from core.defaults import HR_ACTIVITY_CHOICES, JOB_TYPE_CHOICES

        profile = load_profile()
        url = (target_url or "").strip()
        if not url.startswith("https://www.zhaopin.com/"):
            return "筛选网站只接受智联招聘 https 地址"
        profile["target_url"] = url
        profile["cities"] = _split_terms(cities)
        profile["salary_floor_k"] = int(salary_floor)
        profile["salary_min_k"] = int(salary_min) if not prefer_salary_uncapped else int(salary_min or 0)
        profile["salary_max_k"] = None
        profile["prefer_salary_uncapped"] = bool(prefer_salary_uncapped)
        profile["min_company_size"] = max(0, int(min_company_size))
        profile["prefer_active_hr"] = bool(prefer_active_hr)
        activity = str(hr_activity_max or "本月活跃").strip()
        profile["hr_activity_max"] = activity if activity in HR_ACTIVITY_CHOICES else "本月活跃"
        if isinstance(job_types, str):
            selected = _split_terms(job_types)
        else:
            selected = [str(item) for item in (job_types or []) if str(item).strip()]
        profile["job_types"] = [item for item in selected if item in JOB_TYPE_CHOICES] or list(JOB_TYPE_CHOICES)
        profile["target_roles"] = _split_lines(roles)
        profile["blocked_titles"] = _split_terms(blocked_titles)
        profile["blocked_keywords"] = _split_terms(blocked_keywords)
        profile["preferred_keywords"] = _split_terms(preferred_keywords)
        profile["min_apply_score"] = max(0, min(int(min_apply_score), 100))
        profile["auto_apply"] = bool(auto_apply)
        rules = dict(profile.get("hard_rules") or {})
        rules["reject_experience_at_least_years"] = max(0, int(experience_years))
        profile["hard_rules"] = rules
        save_profile(profile)
        return "筛选指标已保存"

    def restore_default_filters(self) -> dict:
        from copy import deepcopy

        from core.defaults import DEFAULT_PROFILE

        current = load_profile()
        profile = deepcopy(DEFAULT_PROFILE)
        for key in ("education", "skills", "projects", "project_policy"):
            if key in current:
                profile[key] = current[key]
        save_profile(profile)
        self._log("已恢复默认筛选条件")
        return profile

    def view(self) -> dict:
        with self._lock:
            logs = "\n".join(self.logs)
        try:
            jobs = self.db.job_rows()
            applications = self.db.application_rows()
            filename = self.db.resume_filename()
            resume_length = len(self.db.resume_text())
            job_count = len(jobs)
            submitted = self._submitted_count()
        except Exception:
            logger.error("读取数据库失败")
            jobs, applications, filename, resume_length = [], [], "", 0
            job_count = 0
            submitted = 0
        with self._lock:
            if job_count > self._seen_jobs:
                self._session_collected += job_count - self._seen_jobs
            self._seen_jobs = job_count
            if submitted > self._seen_applied:
                self._session_applied += submitted - self._seen_applied
            self._seen_applied = submitted
            self.snapshot["collected"] = self._session_collected
            self.snapshot["applied"] = self._session_applied
            data = dict(self.snapshot)
        resume = f"已上传 {filename}，正文 {resume_length} 字" if filename else "尚未上传简历"
        data.update({"logs": logs, "jobs": jobs, "applications": applications, "resume": resume})
        return data
