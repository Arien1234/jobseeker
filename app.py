"""JobHunter AI desktop console."""

from __future__ import annotations

import atexit
import html
import json

import gradio as gr

from core.ai_filter import list_model_names, normalize_model_settings, probe_model, resolve_model_settings
from core.defaults import DEFAULT_MODEL, DEFAULT_PROFILE, HR_ACTIVITY_CHOICES, JOB_TYPE_CHOICES
from core.logging_config import setup_logging
from core.profile_store import load_profile
from core.task_manager import CollectController

setup_logging()
controller = CollectController()
controller.db.clear_job_list()
atexit.register(controller.clear_resume_data)

JOB_COLUMNS = [
    "序号",
    "岗位详情",
    "职位名称",
    "公司",
    "薪资",
    "地点",
    "学历",
    "工作经验",
    "公司规模",
    "HR活跃",
    "AI评分",
    "AI推荐结果",
    "筛选前",
    "筛选说明",
    "投递状态",
]

APP_CSS = """
html { scroll-behavior: smooth; }
body, gradio-app, .gradio-container, .gradio-container .main,
.gradio-container .wrap, .gradio-container .contain,
.gradio-container .contain > .column, .gradio-container .tabs, .tabitem {
  height: auto !important;
  max-height: none !important;
  overflow: visible !important;
}
.gradio-container .contain > .column { gap: 0 !important; }
.gradio-container {
  width: 100% !important;
  max-width: 1280px !important;
  margin: 0 auto !important;
  padding: 0 clamp(10px, 2vw, 20px) 48px !important;
  box-sizing: border-box !important;
}
#app-top {
  position: sticky;
  top: 0;
  z-index: 80;
  display: flex;
  flex-direction: column;
  gap: 0;
  background-color: var(--body-background-fill, #0b0f19) !important;
  padding: 12px 4px 0;
  margin: 0 !important;
}
#app-top h2 {
  margin: 0 !important;
  font-size: clamp(18px, 2vw, 22px) !important;
  line-height: 1.25 !important;
}
#app-top .tab-wrapper,
.gradio-container .tabs .tab-wrapper {
  position: static !important;
  top: auto !important;
  z-index: 70;
  margin: 0 !important;
  padding: 2px 0 0;
  background-color: var(--body-background-fill, #0b0f19) !important;
  border-bottom: 1px solid var(--border-color-primary, #243044);
}
#job-toolbar, #filter-panel, #model-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 18px 0 8px;
  position: relative;
  z-index: 1;
}
#job-toolbar label, #filter-panel label, #model-panel label, #job-table label {
  position: static !important;
  display: block !important;
  transform: none !important;
  inset: auto !important;
  margin: 0 0 6px !important;
}
#job-toolbar .row, #filter-panel .row, #model-panel .row, .status-row {
  flex-wrap: wrap !important;
  gap: 10px !important;
}
.browser-actions, .work-actions {
  flex-wrap: nowrap !important;
  gap: 10px !important;
}
.browser-actions > *, .work-actions > *, .status-row > *, #filter-panel .row > *, #model-panel .row > *, #job-toolbar .row > * {
  flex: 1 1 0 !important;
  min-width: 0 !important;
}
.browser-actions button, .work-actions button {
  min-height: 42px;
  width: 100%;
  white-space: nowrap !important;
}
#job-toolbar textarea, #filter-panel textarea, #model-panel textarea { min-height: 42px !important; }
#job-table {
  width: 100% !important;
  margin-top: 8px;
  overflow: visible !important;
  max-height: none !important;
}
.job-sheet-label {
  margin: 0 0 8px;
  font-weight: 650;
}
.job-sheet-scroll {
  width: 100%;
  max-height: min(70vh, 760px);
  overflow: auto;
  border: 1px solid rgba(148, 163, 184, 0.28);
  border-radius: 0;
  background: transparent;
}
.job-sheet {
  width: max-content;
  min-width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 14px;
  line-height: 1.45;
}
.job-sheet th,
.job-sheet td {
  padding: 7px 10px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.16);
  text-align: left;
  vertical-align: top;
  white-space: normal;
  word-break: normal;
}
.job-sheet th {
  position: sticky;
  top: 0;
  z-index: 2;
  white-space: nowrap;
  font-weight: 650;
  background: #141b2b;
}
.job-sheet td {
  min-width: 64px;
  max-width: 132px;
  cursor: default;
  user-select: text;
}
.job-sheet td:nth-child(1),
.job-sheet th:nth-child(1) {
  width: 48px;
  min-width: 48px;
  max-width: 56px;
}
.job-sheet td.detail-cell,
.job-sheet th:nth-child(2) {
  width: 64px;
  min-width: 64px;
  max-width: 72px;
  white-space: nowrap;
}
.job-sheet td:nth-child(3),
.job-sheet td:nth-child(4) {
  min-width: 128px;
  max-width: 156px;
}
.job-sheet td:nth-child(14),
.job-sheet th:nth-child(14) {
  min-width: 340px;
  max-width: 380px;
}
.job-detail-link {
  appearance: none;
  background: transparent;
  border: 0;
  padding: 0;
  margin: 0;
  color: #8eb7ff;
  font: inherit;
  font-weight: 650;
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;
}
.job-detail-link:focus,
.job-detail-link:focus-visible {
  outline: none;
  box-shadow: none;
}
.job-sheet tr.job-hidden { display: none; }
.job-sheet td.job-title-cell {
  min-width: 148px;
  max-width: 240px;
}
mark.job-hit {
  background: #ffd56a;
  color: #1c1404;
  border-radius: 3px;
  padding: 0 2px;
  font-weight: 700;
}
.job-hit-excerpt {
  display: block;
  margin-top: 4px;
  color: #d5deea;
  font-size: 12px;
  line-height: 1.45;
}
#job-detail-pick {
  position: fixed !important;
  left: -10000px !important;
  top: 0 !important;
  width: 240px !important;
  height: 40px !important;
  opacity: 0 !important;
  overflow: hidden !important;
}
#job-modal {
  position: fixed !important;
  inset: 0 !important;
  z-index: 4000 !important;
  width: 100vw !important;
  max-width: none !important;
  height: 100vh !important;
  margin: 0 !important;
  padding: 0 !important;
  background: rgba(6, 10, 18, 0.62) !important;
  border: 0 !important;
  box-shadow: none !important;
  overflow: auto !important;
  animation: job-mask-in 220ms ease;
}
#job-modal.hide,
#job-modal[hidden] {
  display: none !important;
}
#job-modal.is-closing {
  animation: job-mask-out 200ms ease forwards;
}
#job-dialog {
  position: relative !important;
  z-index: 1 !important;
  width: min(760px, calc(100vw - 48px)) !important;
  max-height: min(78vh, 820px) !important;
  overflow: auto !important;
  margin: 8vh auto !important;
  padding: 22px 22px 18px !important;
  border-radius: 18px !important;
  background: var(--body-background-fill, #0b0f19) !important;
  border: 1px solid rgba(148, 163, 184, 0.28) !important;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.5);
  animation: job-pop-in 240ms cubic-bezier(0.16, 1, 0.3, 1);
}
#job-modal.is-closing #job-dialog {
  animation: job-pop-out 200ms ease forwards;
}
@keyframes job-mask-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes job-mask-out {
  from { opacity: 1; }
  to { opacity: 0; }
}
@keyframes job-pop-in {
  from { opacity: 0; transform: translateY(14px) scale(0.98); }
  to { opacity: 1; transform: none; }
}
@keyframes job-pop-out {
  from { opacity: 1; transform: none; }
  to { opacity: 0; transform: translateY(8px) scale(0.985); }
}
#job-dialog .prose { max-width: none !important; }
#filter-panel, #model-panel { padding-bottom: 64px; }
#resume-card {
  margin-top: 12px;
  padding: 18px 18px 16px;
  border: 1px solid var(--border-color-primary, #243044);
  border-radius: 14px;
  background: var(--block-background-fill, rgba(255, 255, 255, 0.03));
  gap: 12px !important;
}
#resume-card .prose p { margin: 0 0 4px !important; line-height: 1.5; }
#resume-card .hint { opacity: 0.75; font-size: 13px; }
#resume-card label,
#resume-card label span {
  background: transparent !important;
  background-color: transparent !important;
  color: #c5d0de !important;
  border: 0 !important;
  box-shadow: none !important;
  border-radius: 0 !important;
  padding: 0 !important;
  width: auto !important;
  height: auto !important;
  font-size: 13px !important;
  font-weight: 600 !important;
}
#resume-card label svg { display: none !important; }
#resume-card textarea {
  min-height: 42px !important;
  max-height: 72px !important;
}
#resume-card .upload-container,
#resume-card .file-preview {
  height: auto !important;
  min-height: 132px !important;
  max-height: none !important;
  overflow: visible !important;
}
#resume-actions {
  gap: 12px !important;
}
#resume-actions > button {
  flex: 1 1 0 !important;
  min-width: 0 !important;
  min-height: 44px !important;
}
#job-finder {
  margin: 12px 0 4px !important;
}
#job-finder .form {
  align-items: stretch !important;
  flex-wrap: nowrap !important;
}
#job-keyword {
  flex: 1 1 auto !important;
  min-width: 0 !important;
}
#job-finder textarea {
  min-height: 42px !important;
}
#applied-only {
  flex: 0 0 auto !important;
  width: auto !important;
  min-width: 148px !important;
  max-width: 168px !important;
  align-self: stretch !important;
  display: flex !important;
  align-items: flex-end !important;
}
#applied-only > .wrap {
  top: 0 !important;
  bottom: 0 !important;
  height: 100% !important;
  min-height: 0 !important;
  width: 100% !important;
}
#applied-only label {
  display: flex !important;
  align-items: center !important;
  height: 42px !important;
  min-height: 42px !important;
  margin: 0 !important;
  white-space: nowrap !important;
  gap: 8px !important;
}
#applied-only input[type="checkbox"] {
  width: 18px !important;
  height: 18px !important;
  min-width: 18px !important;
  min-height: 18px !important;
  max-height: 18px !important;
  flex: 0 0 18px !important;
  margin: 0 !important;
}
#applied-only .label-text {
  white-space: nowrap !important;
  width: auto !important;
  line-height: 1.2 !important;
}
.job-type-note p { margin: 0 0 6px; }
.job-type-note ul { margin: 4px 0 0; padding-left: 1.2em; }
@media (max-width: 720px) {
  .browser-actions, .work-actions, .status-row { flex-wrap: wrap !important; }
  .browser-actions > *, .work-actions > *, .status-row > * { flex-basis: 100% !important; }
  #job-table table { font-size: 12px; }
}
"""


def _jobs_html(rows: list[dict]) -> str:
    head = "".join(f"<th>{html.escape(name)}</th>" for name in JOB_COLUMNS)
    body = []
    for row in rows:
        cells = []
        title_text = str(row.get("职位名称") or "").replace("\n", " ")
        detail_text = " ".join(str(row.get("详情正文") or "").split())
        applied = "1" if "已投递" in str(row.get("投递状态") or "") else "0"
        for name in JOB_COLUMNS:
            if name == "岗位详情":
                title = html.escape(title_text, quote=True)
                company = html.escape(str(row.get("公司") or "").replace("\n", " "), quote=True)
                try:
                    row_index = int(row.get("序号") or 1) - 1
                except (TypeError, ValueError):
                    row_index = 0
                cells.append(
                    '<td class="detail-cell">'
                    f'<button type="button" class="job-detail-link" data-row="{row_index}" '
                    f'data-title="{title}" data-company="{company}">查看</button>'
                    "</td>"
                )
            elif name == "职位名称":
                cells.append(f'<td class="job-title-cell">{html.escape(title_text)}</td>')
            else:
                cells.append(f"<td>{html.escape(str(row.get(name) if row.get(name) is not None else ''))}</td>")
        body.append(
            f'<tr data-applied="{applied}" data-title="{html.escape(title_text, quote=True)}" '
            f'data-detail="{html.escape(detail_text, quote=True)}">' + "".join(cells) + "</tr>"
        )
    return (
        f'<div class="job-sheet-label">当前职位（{len(rows)}）</div>'
        '<div class="job-sheet-scroll"><table class="job-sheet">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def _file_path(uploaded) -> str:
    if uploaded is None:
        return ""
    if isinstance(uploaded, str):
        return uploaded
    return getattr(uploaded, "name", "") or ""


def _join(items) -> str:
    return "，".join(items or [])


def _save_filters(
    url,
    cities,
    salary_floor,
    salary_min,
    roles,
    min_score,
    experience_years,
    blocked_titles,
    blocked_keywords,
    preferred_keywords,
    min_company_size,
    prefer_active_hr,
    hr_activity_max,
    job_types,
    prefer_salary_uncapped,
):
    return controller.save_filters(
        url,
        cities,
        salary_floor,
        salary_min,
        roles,
        min_score,
        experience_years,
        True,
        blocked_titles,
        blocked_keywords,
        preferred_keywords,
        min_company_size,
        prefer_active_hr,
        hr_activity_max,
        job_types,
        prefer_salary_uncapped,
    )


def _restore_defaults():
    profile = controller.restore_default_filters()
    return (
        "已恢复默认筛选条件",
        profile.get("target_url") or DEFAULT_PROFILE["target_url"],
        _join(profile.get("cities")),
        profile.get("salary_floor_k"),
        profile.get("salary_min_k"),
        bool(profile.get("prefer_salary_uncapped", True)),
        profile.get("min_apply_score"),
        (profile.get("hard_rules") or {}).get("reject_experience_at_least_years"),
        profile.get("min_company_size"),
        "\n".join(profile.get("target_roles") or []),
        _join(profile.get("blocked_titles")),
        _join(profile.get("preferred_keywords")),
        _join(profile.get("blocked_keywords")),
        bool(profile.get("prefer_active_hr", True)),
        profile.get("hr_activity_max") or "本月活跃",
        profile.get("job_types") or list(JOB_TYPE_CHOICES),
    )


def _crawl_update():
    label = controller.crawl_button_label()
    return gr.update(value=label, variant="stop" if label == "停止采集" else "secondary")


def _apply_update():
    label = controller.apply_button_label()
    if label == "停止投递":
        return gr.update(value=label, variant="secondary")
    return gr.update(value=label, variant="primary")


_ui_cache = {"table": None, "logs": None, "button": None, "apply": None, "action": None}


def poll(force_table: bool = False):
    state = controller.view()
    jobs = state["jobs"] or []
    stamp = tuple(
        (
            row.get("序号"),
            row.get("筛选前"),
            row.get("AI评分"),
            row.get("投递状态"),
            row.get("筛选说明"),
        )
        for row in jobs
    )
    if force_table or stamp != _ui_cache["table"]:
        frame = gr.update(value=_jobs_html(jobs))
        _ui_cache["table"] = stamp
    else:
        frame = gr.update()
    logs = state["logs"]
    if logs != _ui_cache["logs"]:
        log_out = logs
        _ui_cache["logs"] = logs
    else:
        log_out = gr.update()
    label = controller.crawl_button_label()
    if force_table or label != _ui_cache["button"]:
        button = _crawl_update()
        _ui_cache["button"] = label
    else:
        button = gr.update()
    apply_label = controller.apply_button_label()
    if force_table or apply_label != _ui_cache["apply"]:
        apply_button = _apply_update()
        _ui_cache["apply"] = apply_label
    else:
        apply_button = gr.update()
    action = controller.status_text()
    if force_table or action != _ui_cache["action"]:
        action_out = action
        _ui_cache["action"] = action
    else:
        action_out = gr.update()
    return (
        state["login"],
        state["task"],
        state["progress"],
        str(state["collected"]),
        str(state["applied"]),
        log_out,
        frame,
        state["resume"],
        button,
        action_out,
        apply_button,
    )


def toggle_crawl():
    message = controller.toggle_crawl()
    return message, _crawl_update()


def toggle_apply(count):
    if controller.apply_button_label() == "停止投递":
        message = controller.stop()
    else:
        message = controller.start_apply(count)
    return message, _apply_update()


def open_from_pick(raw: str):
    if not raw or not str(raw).strip():
        return gr.update(), gr.update()
    try:
        data = json.loads(raw)
        row_index = int(data.get("row"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return gr.update(), gr.update()
    detail = controller.db.job_detail_markdown(
        row_index,
        str(data.get("title") or ""),
        str(data.get("company") or ""),
    )
    return detail, gr.update(visible=True)


def _refresh_models(host, current):
    try:
        names = list_model_names(host)
    except Exception as exc:
        return gr.update(), f"读不到本机模型：{type(exc).__name__}"
    if not names:
        return gr.update(), "这个地址上没有已安装的模型。"
    chosen = str(current or "").strip()
    if chosen and chosen not in names:
        names = [chosen, *names]
    value = chosen if chosen in names else names[0]
    return gr.update(choices=names, value=value), f"读到 {len(names)} 个模型。"


def _test_model(host, name, temperature, num_predict, timeout_seconds, keep_alive, disable_think):
    try:
        settings = normalize_model_settings(
            host, name, temperature, num_predict, timeout_seconds, disable_think, keep_alive
        )
    except ValueError as exc:
        return str(exc)
    return probe_model(settings)


def _save_model(host, name, temperature, num_predict, timeout_seconds, keep_alive, disable_think):
    try:
        return controller.save_model_settings(
            host, name, temperature, num_predict, timeout_seconds, disable_think, keep_alive
        )
    except ValueError as exc:
        return str(exc)


def _restore_model():
    profile = controller.restore_default_model()
    settings = resolve_model_settings(profile)
    return (
        f"已恢复默认模型 {DEFAULT_MODEL['name']}。",
        settings["host"],
        gr.update(choices=[settings["name"]], value=settings["name"]),
        settings["temperature"],
        settings["num_predict"],
        settings["timeout_seconds"],
        settings["keep_alive"],
        settings["disable_think"],
    )


def build_app() -> gr.Blocks:
    profile = load_profile()
    initial_jobs = _jobs_html(controller.view().get("jobs") or [])

    with gr.Blocks(title="JobHunter AI") as demo:
        with gr.Column(elem_id="app-top"):
            gr.Markdown("## JobHunter")

        with gr.Tabs():
            with gr.Tab("岗位列表"):
                with gr.Column(elem_id="job-toolbar"):
                    with gr.Row(elem_classes=["browser-actions"]):
                        start_browser = gr.Button("启动浏览器")
                        reload_browser = gr.Button("重新加载")
                        close_browser = gr.Button("关闭浏览器")
                    with gr.Row(elem_classes=["work-actions"]):
                        start_apply = gr.Button("筛选并投递", variant="primary")
                        start_crawl = gr.Button("仅采集")
                        pause_collect = gr.Button("暂停")
                        clear_jobs = gr.Button("清空列表")
                    action_box = gr.Textbox(
                        label="操作结果",
                        value="空闲。可以点「筛选并投递」或「仅采集」。",
                        interactive=False,
                        lines=1,
                        max_lines=2,
                    )
                    with gr.Row(elem_classes=["status-row"]):
                        login_box = gr.Textbox(label="登录状态", interactive=False, max_lines=1)
                        task_box = gr.Textbox(label="当前任务", interactive=False, max_lines=1)
                        progress_box = gr.Textbox(label="进度", interactive=False, max_lines=1)
                        collected_box = gr.Textbox(label="已采集", interactive=False, max_lines=1)
                        applied_box = gr.Textbox(label="已投递", interactive=False, max_lines=1)
                    apply_count = gr.Slider(1, 200, value=150, step=1, label="本批投递数量")
                    log_box = gr.Textbox(label="实时日志", lines=6, interactive=False)

                with gr.Row(elem_id="job-finder"):
                    gr.Textbox(
                        label="搜索职位",
                        placeholder="只搜职位名称和岗位详情，匹配的词会标黄",
                        elem_id="job-keyword",
                        scale=4,
                    )
                    gr.Checkbox(
                        label="只看已投递",
                        elem_id="applied-only",
                        scale=0,
                        min_width=148,
                    )

                with gr.Column(visible=False, elem_id="job-modal") as job_dialog:
                    with gr.Column(elem_id="job-dialog"):
                        detail_box = gr.Markdown()
                close_detail = gr.Button("关闭", elem_id="job-dialog-dismiss", visible="hidden")
                detail_pick = gr.Textbox(elem_id="job-detail-pick", show_label=False)
                open_detail = gr.Button("打开详情", elem_id="job-detail-open", visible="hidden")

                job_table = gr.HTML(value=initial_jobs, elem_id="job-table")

            with gr.Tab("筛选设置"):
                with gr.Column(elem_id="filter-panel"):
                    gr.Markdown(
                        "保底默认 4K，公司默认至少 20 人。"
                        "HR 活跃可自选到本月活跃；实习/正式/校招/社招可多选，全选表示都投。"
                    )
                    with gr.Row():
                        save_filters = gr.Button("保存筛选条件", variant="primary")
                        restore_filters = gr.Button("恢复默认筛选")
                    filter_message = gr.Textbox(label="保存结果", interactive=False, max_lines=2)
                    target_url = gr.Textbox(
                        label="职位页面",
                        value=profile.get("target_url") or DEFAULT_PROFILE["target_url"],
                    )
                    filter_cities = gr.Textbox(label="城市，逗号分隔", value=_join(profile.get("cities")))
                    with gr.Row():
                        salary_floor = gr.Number(
                            label="保底薪资（K）",
                            value=profile.get("salary_floor_k") if profile.get("salary_floor_k") is not None else 4,
                            precision=0,
                        )
                        filter_salary_min = gr.Number(
                            label="优先薪资（K）",
                            value=profile.get("salary_min_k") or 10,
                            precision=0,
                        )
                        prefer_salary_uncapped = gr.Checkbox(
                            label="优先薪资不设上限",
                            value=bool(profile.get("prefer_salary_uncapped", True)),
                        )
                        min_score = gr.Number(
                            label="自动投递最低分",
                            value=profile.get("min_apply_score") or 80,
                            precision=0,
                        )
                        experience_years = gr.Number(
                            label="经验达到几年就排除",
                            value=(profile.get("hard_rules") or {}).get("reject_experience_at_least_years") or 3,
                            precision=0,
                        )
                        min_company_size = gr.Number(
                            label="公司至少多少人，0 为不限",
                            value=20 if profile.get("min_company_size") is None else profile.get("min_company_size"),
                            precision=0,
                        )
                    job_types = gr.CheckboxGroup(
                        label="岗位类型（硬性，可全选）",
                        choices=list(JOB_TYPE_CHOICES),
                        value=profile.get("job_types") or list(JOB_TYPE_CHOICES),
                    )
                    gr.Markdown(
                        "勾选校招或实习后，用简历里的大学毕业年份核对届别。"
                        "岗位写明只招某一届、而简历不是这一届，就会排除。写了「优先」或「也可」的届别不算硬性限制。\n\n"
                        "- **实习**：在校就能投，不需要毕业证。\n"
                        "- **校招**：面向应届的某一届，毕业前的秋招、春招就可以投，入职一般在拿到毕业证之后。\n"
                        "- **正式**：要有毕业证才能入职。还没毕业时，只有岗位写明可以发 offer、提前实习并提供转正，才考虑。\n"
                        "- **社招**：面向已经毕业、正在社会上工作的人，同样需要毕业证。在读期间不投纯社招。\n\n"
                        "筛选说明会写明这个岗位被判断成实习、校招、正式还是社招。",
                        elem_classes=["job-type-note"],
                    )
                    with gr.Row():
                        filter_roles = gr.Textbox(
                            label="目标岗位，一行一个",
                            lines=8,
                            value="\n".join(profile.get("target_roles") or []),
                        )
                        blocked_titles = gr.Textbox(
                            label="屏蔽岗位，逗号或换行",
                            lines=8,
                            value=_join(profile.get("blocked_titles")),
                        )
                    with gr.Row():
                        preferred_keywords = gr.Textbox(
                            label="优先关键词",
                            lines=4,
                            value=_join(profile.get("preferred_keywords")),
                        )
                        blocked_keywords = gr.Textbox(
                            label="屏蔽关键词",
                            lines=4,
                            value=_join(profile.get("blocked_keywords")),
                        )
                    with gr.Row():
                        hr_activity_max = gr.Dropdown(
                            label="HR 活跃最旧可接受",
                            choices=list(HR_ACTIVITY_CHOICES),
                            value=profile.get("hr_activity_max") or "本月活跃",
                        )
                        prefer_active_hr = gr.Checkbox(
                            label="当前在线/刚刚活跃/今日活跃额外加分",
                            value=bool(profile.get("prefer_active_hr", True)),
                        )
                    with gr.Column(elem_id="resume-card"):
                        gr.Markdown("**简历**")
                        gr.Markdown("只用于 AI 筛选，关闭程序时自动清空。", elem_classes=["hint"])
                        resume_box = gr.Textbox(
                            label="当前状态",
                            interactive=False,
                            lines=1,
                            max_lines=2,
                        )
                        resume_file = gr.File(label="上传 PDF", file_types=[".pdf"])
                        with gr.Row(elem_id="resume-actions"):
                            resume_button = gr.Button("解析并保存", variant="primary")
                            clear_resume = gr.Button("删除简历")

            with gr.Tab("模型配置"):
                model_settings = resolve_model_settings(profile)
                with gr.Column(elem_id="model-panel"):
                    gr.Markdown(
                        "默认是本机 Ollama 的 `qwen3.5:9b`。"
                        "换地址或换模型后先测试，再保存。保存之前的筛选仍用当前已保存的模型。"
                    )
                    with gr.Row():
                        model_host = gr.Textbox(
                            label="服务地址",
                            value=model_settings["host"],
                        )
                        model_name = gr.Dropdown(
                            label="模型名称",
                            choices=[model_settings["name"]],
                            value=model_settings["name"],
                            allow_custom_value=True,
                        )
                    with gr.Row():
                        model_temperature = gr.Slider(
                            label="温度",
                            minimum=0,
                            maximum=2,
                            step=0.05,
                            value=model_settings["temperature"],
                        )
                        model_predict = gr.Slider(
                            label="单次最多生成 token",
                            minimum=32,
                            maximum=4096,
                            step=32,
                            value=model_settings["num_predict"],
                        )
                        model_timeout = gr.Slider(
                            label="等待超时（秒）",
                            minimum=20,
                            maximum=600,
                            step=10,
                            value=model_settings["timeout_seconds"],
                        )
                    with gr.Row():
                        model_keep_alive = gr.Textbox(
                            label="模型驻留时间",
                            value=model_settings["keep_alive"],
                        )
                        model_disable_think = gr.Checkbox(
                            label="关闭思考过程",
                            value=bool(model_settings["disable_think"]),
                        )
                    with gr.Row():
                        refresh_models = gr.Button("读取本机模型")
                        test_model = gr.Button("测试连接")
                        save_model = gr.Button("保存模型", variant="primary")
                        restore_model = gr.Button("恢复默认模型")
                    model_message = gr.Textbox(label="模型状态", interactive=False, lines=2)

        outputs = [
            login_box,
            task_box,
            progress_box,
            collected_box,
            applied_box,
            log_box,
            job_table,
            resume_box,
            start_crawl,
            action_box,
            start_apply,
        ]
        filter_outputs = [
            filter_message,
            target_url,
            filter_cities,
            salary_floor,
            filter_salary_min,
            prefer_salary_uncapped,
            min_score,
            experience_years,
            min_company_size,
            filter_roles,
            blocked_titles,
            preferred_keywords,
            blocked_keywords,
            prefer_active_hr,
            hr_activity_max,
            job_types,
        ]
        timer = gr.Timer(1)
        timer.tick(poll, outputs=outputs, show_progress="hidden")
        demo.load(
            lambda: poll(force_table=True),
            outputs=outputs,
            js="""
() => {
  const join = () => {
    const top = document.querySelector('#app-top');
    const nav = document.querySelector('.tab-wrapper');
    if (!top || !nav || nav.parentElement === top) return;
    top.appendChild(nav);
  };
  join();
  [40, 200, 600].forEach((ms) => setTimeout(join, ms));
  if (!window.__jobDialogOutside) {
    window.__jobDialogOutside = true;
    document.addEventListener('click', (event) => {
      const link = event.target.closest && event.target.closest('.job-detail-link');
      if (link) {
        event.preventDefault();
        const box = document.querySelector('#job-detail-pick textarea, #job-detail-pick input, textarea#job-detail-pick, input#job-detail-pick');
        const opener = document.querySelector('#job-detail-open');
        if (!box || !opener) return;
        const payload = JSON.stringify({
          row: link.dataset.row,
          title: link.dataset.title || '',
          company: link.dataset.company || '',
          n: Date.now()
        });
        const proto = Object.getOwnPropertyDescriptor(box.constructor.prototype, 'value');
        if (proto && proto.set) proto.set.call(box, payload);
        else box.value = payload;
        opener.click();
        let tries = 0;
        const replay = setInterval(() => {
          tries += 1;
          const modal = document.querySelector('#job-modal');
          const shown = modal && getComputedStyle(modal).display !== 'none' && !modal.hidden;
          if (shown) {
            modal.classList.remove('is-closing');
            const dialog = document.querySelector('#job-dialog');
            modal.style.animation = 'none';
            if (dialog) dialog.style.animation = 'none';
            void modal.offsetWidth;
            modal.style.animation = '';
            if (dialog) dialog.style.animation = '';
            if (window.__highlightJobHits) {
              window.__highlightJobHits();
              setTimeout(window.__highlightJobHits, 80);
              setTimeout(window.__highlightJobHits, 240);
            }
            clearInterval(replay);
          } else if (tries > 40) {
            clearInterval(replay);
          }
        }, 40);
        return;
      }
      const modal = document.querySelector('#job-modal');
      if (!modal) return;
      const style = getComputedStyle(modal);
      if (style.display === 'none' || style.visibility === 'hidden' || modal.hidden) return;
      const dialog = document.querySelector('#job-dialog');
      if (dialog && dialog.contains(event.target)) return;
      if (!modal.contains(event.target)) return;
      if (modal.classList.contains('is-closing')) return;
      modal.classList.add('is-closing');
      setTimeout(() => {
        const button = document.querySelector('#job-dialog-dismiss');
        if (button) button.click();
      }, 190);
    });
  }
  if (!window.__jobListFilter) {
    window.__jobListFilter = true;
    const escapeHtml = (value) => String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    const marked = (text, keyword) => {
      const source = String(text || '');
      const query = String(keyword || '').trim();
      if (!query) return escapeHtml(source);
      const lower = source.toLowerCase();
      const q = query.toLowerCase();
      let htmlText = '';
      let from = 0;
      let at = lower.indexOf(q, from);
      if (at < 0) return escapeHtml(source);
      while (at >= 0) {
        htmlText += escapeHtml(source.slice(from, at));
        htmlText += '<mark class="job-hit">' + escapeHtml(source.slice(at, at + query.length)) + '</mark>';
        from = at + query.length;
        at = lower.indexOf(q, from);
      }
      return htmlText + escapeHtml(source.slice(from));
    };
    const excerpt = (text, keyword) => {
      const source = String(text || '');
      const q = keyword.toLowerCase();
      const at = source.toLowerCase().indexOf(q);
      if (at < 0) return '';
      const start = Math.max(0, at - 14);
      const end = Math.min(source.length, at + keyword.length + 18);
      const slice = (start ? '…' : '') + source.slice(start, end) + (end < source.length ? '…' : '');
      return marked(slice, keyword);
    };
    const paintNode = (node, keyword) => {
      if (!node) return;
      const marks = node.querySelectorAll ? node.querySelectorAll('mark.job-hit') : [];
      marks.forEach((mark) => mark.replaceWith(document.createTextNode(mark.textContent || '')));
      if (node.normalize) node.normalize();
      const query = String(keyword || '').trim();
      if (!query) return;
      const q = query.toLowerCase();
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      nodes.forEach((textNode) => {
        const source = textNode.nodeValue || '';
        const lower = source.toLowerCase();
        if (!lower.includes(q)) return;
        const frag = document.createDocumentFragment();
        let from = 0;
        let at = lower.indexOf(q, from);
        while (at >= 0) {
          frag.append(source.slice(from, at));
          const mark = document.createElement('mark');
          mark.className = 'job-hit';
          mark.textContent = source.slice(at, at + query.length);
          frag.append(mark);
          from = at + query.length;
          at = lower.indexOf(q, from);
        }
        frag.append(source.slice(from));
        textNode.parentNode.replaceChild(frag, textNode);
      });
    };
    const highlightDetail = (keyword) => {
      const dialog = document.querySelector('#job-dialog');
      if (!dialog) return;
      const headings = Array.from(dialog.querySelectorAll('h3, h4'));
      const title = dialog.querySelector('h3');
      const detailHead = headings.find((item) => (item.textContent || '').includes('岗位详情'));
      const stop = headings.find((item) => (item.textContent || '').includes('采集信息'));
      if (title) paintNode(title, keyword);
      if (!detailHead) return;
      let node = detailHead.nextElementSibling;
      while (node && node !== stop) {
        const next = node.nextElementSibling;
        paintNode(node, keyword);
        node = next;
      }
    };
    window.__highlightJobHits = () => {
      const keywordBox = document.querySelector('#job-keyword textarea, #job-keyword input');
      highlightDetail(((keywordBox && keywordBox.value) || '').trim());
    };
    let scheduled = 0;
    let appliedOverride = null;
    const applyJobFilter = () => {
      const table = document.querySelector('#job-table');
      if (!table) return;
      const keywordBox = document.querySelector('#job-keyword textarea, #job-keyword input');
      const appliedBox = document.querySelector('#applied-only input[type="checkbox"]');
      const keyword = ((keywordBox && keywordBox.value) || '').trim();
      const q = keyword.toLowerCase();
      const appliedOnly = appliedOverride === null ? !!(appliedBox && appliedBox.checked) : appliedOverride;
      const rows = table.querySelectorAll('.job-sheet tbody tr');
      let shown = 0;
      rows.forEach((row) => {
        const title = row.dataset.title || '';
        const detail = row.dataset.detail || '';
        const titleHit = !!q && title.toLowerCase().includes(q);
        const detailHit = !!q && detail.toLowerCase().includes(q);
        const visible = (!q || titleHit || detailHit) && (!appliedOnly || row.dataset.applied === '1');
        row.classList.toggle('job-hidden', !visible);
        if (visible) shown += 1;
        const cell = row.querySelector('.job-title-cell');
        if (!cell) return;
        const stamp = keyword + '|' + (detailHit ? '1' : '0') + '|' + (titleHit ? '1' : '0');
        if (cell.dataset.mark === stamp) return;
        cell.dataset.mark = stamp;
        const titleHtml = marked(title, titleHit ? keyword : '');
        const extra = detailHit ? '<span class="job-hit-excerpt">' + excerpt(detail, keyword) + '</span>' : '';
        cell.innerHTML = titleHtml + extra;
      });
      const label = table.querySelector('.job-sheet-label');
      if (label) {
        const next = (keyword || appliedOnly)
          ? '当前职位（' + shown + ' / ' + rows.length + '）'
          : '当前职位（' + rows.length + '）';
        if (label.textContent !== next) label.textContent = next;
      }
      highlightDetail(keyword);
    };
    const scheduleFilter = () => {
      if (scheduled) return;
      scheduled = requestAnimationFrame(() => {
        scheduled = 0;
        applyJobFilter();
        appliedOverride = null;
      });
    };
    document.addEventListener('input', (event) => {
      if (event.target.closest && event.target.closest('#job-keyword')) scheduleFilter();
    });
    document.addEventListener('pointerdown', (event) => {
      if (!event.target.closest || !event.target.closest('#applied-only')) return;
      const box = document.querySelector('#applied-only input[type="checkbox"]');
      appliedOverride = !(box && box.checked);
      scheduleFilter();
    });
    document.addEventListener('change', (event) => {
      if (event.target.closest && event.target.closest('#applied-only')) scheduleFilter();
    });
    const watchTable = () => {
      const host = document.querySelector('#job-table');
      if (!host || host.dataset.filterWatch) return;
      host.dataset.filterWatch = '1';
      new MutationObserver(() => scheduleFilter()).observe(host, {childList: true});
      scheduleFilter();
    };
    watchTable();
    [300, 1000].forEach((ms) => setTimeout(() => { watchTable(); scheduleFilter(); }, ms));
  }
}
""",
        )

        open_detail.click(
            open_from_pick,
            inputs=detail_pick,
            outputs=[detail_box, job_dialog],
            show_progress="hidden",
            js="() => { const box = document.querySelector('#job-detail-pick textarea, #job-detail-pick input, textarea#job-detail-pick, input#job-detail-pick'); return box ? box.value : ''; }",
        )
        close_detail.click(lambda: gr.update(visible=False), outputs=job_dialog)
        start_browser.click(controller.start_browser, outputs=action_box)
        reload_browser.click(controller.reload_browser, outputs=action_box)
        start_crawl.click(toggle_crawl, outputs=[action_box, start_crawl])
        start_apply.click(toggle_apply, inputs=apply_count, outputs=[action_box, start_apply])
        pause_collect.click(controller.pause_or_resume, outputs=action_box)
        clear_jobs.click(controller.clear_jobs, outputs=action_box)
        close_browser.click(controller.close_browser, outputs=action_box)
        save_filters.click(
            _save_filters,
            inputs=[
                target_url,
                filter_cities,
                salary_floor,
                filter_salary_min,
                filter_roles,
                min_score,
                experience_years,
                blocked_titles,
                blocked_keywords,
                preferred_keywords,
                min_company_size,
                prefer_active_hr,
                hr_activity_max,
                job_types,
                prefer_salary_uncapped,
            ],
            outputs=filter_message,
        )
        restore_filters.click(_restore_defaults, outputs=filter_outputs)
        resume_button.click(
            lambda uploaded: controller.save_resume(_file_path(uploaded)) if _file_path(uploaded) else "请先选择 PDF",
            inputs=resume_file,
            outputs=filter_message,
        )
        clear_resume.click(controller.clear_resume_data, outputs=filter_message)
        model_inputs = [
            model_host,
            model_name,
            model_temperature,
            model_predict,
            model_timeout,
            model_keep_alive,
            model_disable_think,
        ]
        refresh_models.click(_refresh_models, inputs=[model_host, model_name], outputs=[model_name, model_message])
        test_model.click(_test_model, inputs=model_inputs, outputs=model_message)
        save_model.click(_save_model, inputs=model_inputs, outputs=model_message)
        restore_model.click(
            _restore_model,
            outputs=[
                model_message,
                model_host,
                model_name,
                model_temperature,
                model_predict,
                model_timeout,
                model_keep_alive,
                model_disable_think,
            ],
        )
    return demo


def main() -> None:
    app = build_app()
    app.launch(
        server_name="127.0.0.1",
        server_port=7860,
        theme=gr.themes.Soft(),
        css=APP_CSS,
        inbrowser=False,
    )


if __name__ == "__main__":
    main()
