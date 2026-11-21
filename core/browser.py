"""Visible Chromium session. Login, captcha, and risk checks stay with the user."""

from __future__ import annotations

from pathlib import Path

from core.logging_config import get_logger
from core.paths import BROWSER_DATA_DIR, ENTRY_URL

logger = get_logger("browser")

LOGIN_PHRASES = ("扫码登录", "密码登录", "验证码登录", "请登录", "登录/注册", "登录后可查看专属职位推荐", "去登录")
BLOCK_PHRASES = ("安全验证", "滑动验证", "访问验证", "操作过于频繁")


def browser_was_closed(exc: BaseException) -> bool:
    text = str(exc)
    return any(phrase in text for phrase in ("has been closed", "Target closed", "Browser closed", "Connection closed"))


class ManualActionRequired(Exception):
    """The page is waiting for the user. Automation must stop."""


def classify_page_state(
    url: str,
    title: str,
    head: str,
    has_cards: bool,
    has_detail: bool,
    logged_out_marker: bool = False,
) -> str:
    if has_cards:
        return "已登录"
    if has_detail and "jobdetail" in (url or ""):
        return "详情页"
    if logged_out_marker:
        return "未登录"
    blob = f"{url or ''}\n{title or ''}\n{head or ''}"
    if any(phrase in blob for phrase in BLOCK_PHRASES):
        return "需要人工处理验证"
    if "passport.zhaopin.com" in (url or "") or any(phrase in blob for phrase in LOGIN_PHRASES):
        return "未登录"
    return "页面状态未知"


class BrowserSession:
    def __init__(self, user_data_dir: Path | None = None, headless: bool = False):
        self.user_data_dir = Path(user_data_dir or BROWSER_DATA_DIR)
        self.headless = headless
        self.playwright = None
        self.context = None
        self.page = None
        self._on_closed = None

    @property
    def started(self) -> bool:
        return self.is_alive()

    def is_alive(self) -> bool:
        page = self.page
        if self.context is None or page is None:
            return False
        try:
            return not page.is_closed()
        except Exception:
            return False

    def start(self, on_closed=None) -> None:
        if self.is_alive():
            return
        self.close()
        self._on_closed = on_closed
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                self._launch()
                return
            except Exception as exc:
                last_error = exc
                self.close()
                self._on_closed = on_closed
                if attempt == 1:
                    self._clear_profile_locks()
                    continue
                logger.error("浏览器启动失败")
                raise
        if last_error is not None:
            raise last_error

    def _launch(self) -> None:
        from playwright.sync_api import sync_playwright

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self.playwright = sync_playwright().start()
        try:
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=self.headless,
                locale="zh-CN",
                viewport={"width": 1400, "height": 900},
                timezone_id="Asia/Shanghai",
            )
        except Exception:
            self.playwright.stop()
            self.playwright = None
            raise
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(15000)
        self.context.on("close", self._handle_disconnected)
        self.page.on("close", self._handle_disconnected)

    def _handle_disconnected(self, _source=None) -> None:
        callback = self._on_closed
        context = self.context
        self._on_closed = None
        self.page = None
        self.context = None
        if context is not None and _source is not context:
            try:
                context.close()
            except Exception:
                logger.info("浏览器已经关闭")
        if callback is not None:
            callback()

    def _clear_profile_locks(self) -> None:
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
            path = self.user_data_dir / name
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("浏览器启动失败: 配置目录仍被占用")

    def close(self) -> None:
        self._on_closed = None
        context = self.context
        playwright = self.playwright
        self.context = None
        self.page = None
        self.playwright = None
        try:
            if context is not None:
                context.close()
        except Exception:
            logger.info("浏览器已经关闭")
        try:
            if playwright is not None:
                playwright.stop()
        except Exception:
            logger.info("浏览器已经关闭")

    def refresh_job_list(self, url: str) -> str:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        if not self.is_alive():
            raise RuntimeError("浏览器尚未启动")
        current = self.page.url or ""
        try:
            if url.split("?")[0] in current:
                self.page.reload(wait_until="domcontentloaded", timeout=45000)
            else:
                self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except PlaywrightTimeout:
            logger.error("页面加载超时")
            raise
        try:
            self.page.wait_for_selector(
                "div.job-card, .home-header__c-no-login, h1.summary-planes__title",
                timeout=12000,
            )
        except PlaywrightTimeout:
            logger.warning("页面加载超时: 刷新后未出现职位列表或登录提示")
        status = self.classify()
        if status in {"未登录", "需要人工处理验证", "页面状态未知"}:
            logger.warning("登录状态异常: %s", status)
        return status

    def classify(self) -> str:
        if not self.started:
            return "未启动"
        page = self.page
        url = page.url or ""
        try:
            title = page.title() or ""
        except Exception:
            title = ""
        has_cards = page.locator("div.job-list-panel div.job-card").count() > 0
        has_detail = page.locator(".summary-planes__title, .job-detail-summary__title-text").count() > 0
        logged_out = page.locator(".home-header__c-no-login").count() > 0
        try:
            head = page.locator("body").inner_text(timeout=3000)[:800]
        except Exception:
            head = ""
        return classify_page_state(url, title, head, has_cards, has_detail, logged_out)

    def open_entry(self, url: str = ENTRY_URL) -> str:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        if not self.started:
            raise RuntimeError("浏览器尚未启动")
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except PlaywrightTimeout:
            logger.error("页面加载超时")
            raise
        try:
            self.page.wait_for_selector(
                "div.job-card, .home-header__c-no-login, h1.summary-planes__title",
                timeout=12000,
            )
        except PlaywrightTimeout:
            logger.warning("页面加载超时: 入口页未出现职位列表或登录提示")
        status = self.classify()
        if status in {"未登录", "需要人工处理验证", "页面状态未知"}:
            logger.warning("登录状态异常: %s", status)
        return status

    def ensure_collection_allowed(self, url: str | None = None) -> str:
        from core.paths import ENTRY_URL

        target = url or ENTRY_URL
        status = self.classify()
        current = self.page.url or ""
        if status == "已登录" and target.split("?")[0] in current:
            return status
        status = self.open_entry(target)
        if status != "已登录":
            raise ManualActionRequired(status)
        return status
