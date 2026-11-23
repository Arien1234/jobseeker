"""Logging redaction and the idle controls."""

from pathlib import Path

from core.browser import BrowserSession, browser_was_closed, classify_page_state
from core.database import Database
from core.logging_config import get_logger, redact, setup_logging
from core.paths import LOG_PATH
from core.task_manager import CollectController


def test_secrets_are_redacted(tmp_path: Path):
    assert "sk-secretvalue" not in redact("Authorization: Bearer sk-secretvalue")
    assert "13800138000" not in redact("手机 13800138000")
    setup_logging(tmp_path / "app.log", force=True)
    try:
        get_logger("test").info("token=sk-secretvalue")
    finally:
        setup_logging(LOG_PATH, force=True)
    text = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "sk-secretvalue" not in text
    assert "[REDACTED]" in text


def test_page_state_detection():
    assert classify_page_state("https://www.zhaopin.com/jobs/", "职位", "", True, False) == "已登录"
    assert classify_page_state("https://passport.zhaopin.com/login", "登录", "扫码登录", False, False) == "未登录"
    assert classify_page_state("https://www.zhaopin.com/jobs/?pageMode=recommend", "热门职位", "登录/注册 登录后可查看专属职位推荐", False, False) == "未登录"
    assert classify_page_state("https://www.zhaopin.com/jobs/?pageMode=recommend", "热门职位", "", False, False, True) == "未登录"
    assert classify_page_state("https://www.zhaopin.com/jobs/", "验证", "请完成安全验证", False, False) == "需要人工处理验证"


def test_closed_browser_can_be_reset(tmp_path: Path):
    session = BrowserSession(user_data_dir=tmp_path / "browser_data")

    class ClosedPage:
        def is_closed(self) -> bool:
            return True

    class DeadContext:
        def close(self) -> None:
            raise RuntimeError("Target page, context or browser has been closed")

    session.page = ClosedPage()
    session.context = DeadContext()
    session.playwright = object()
    assert session.is_alive() is False
    session.close()
    assert session.context is None
    assert session.page is None
    assert session.playwright is None
    assert browser_was_closed(RuntimeError("BrowserContext.close: Target page, context or browser has been closed"))


def test_controls_do_not_start_a_second_browser(tmp_path: Path):
    controller = CollectController(Database(tmp_path / "jobs.db"))
    assert "启动浏览器" in controller.start_collect(10)
    assert "没有进行中的任务" in controller.pause_or_resume()
    assert "没有需要停止" in controller.stop()
    assert controller.close_browser() == "浏览器未启动"
    assert controller.enqueue().startswith("待确认 0")


def test_crawl_button_toggles_label(tmp_path: Path):
    controller = CollectController(Database(tmp_path / "jobs.db"))
    assert controller.crawl_button_label() == "仅采集"
    assert controller.apply_button_label() == "筛选并投递"
    controller.snapshot["task"] = "采集中"
    controller._active_task = "采集中"
    assert controller.crawl_button_label() == "停止采集"
    assert "正在停止仅采集" in controller.toggle_crawl()
    assert controller.crawl_button_label() == "停止采集"


def test_apply_total_survives_a_new_round(tmp_path: Path):
    controller = CollectController(Database(tmp_path / "jobs.db"))
    controller._session_applied = 4
    controller._session_collected = 9
    controller.snapshot["applied"] = 4
    controller.snapshot["collected"] = 9
    controller.snapshot["browser"] = "已启动"
    controller._ensure_thread = lambda: None
    controller._queue.put = lambda _item: None
    message = controller.start_apply(12)
    assert "正在筛选并投递" in message
    assert controller.snapshot["applied"] == 4
    assert controller.snapshot["collected"] == 9
    assert controller.apply_button_label() == "停止投递"
    stopping = controller.stop()
    assert "等当前岗位处理完" in stopping
    assert controller.apply_button_label() == "停止投递"
    controller._set(task="已停止", progress="2/12")
    assert controller.apply_button_label() == "筛选并投递"
    assert "筛选并投递已停止" in controller.status_text()
    assert controller.snapshot["applied"] == 4
    state = controller.view()
    assert state["applied"] == 4
    assert state["collected"] == 9
