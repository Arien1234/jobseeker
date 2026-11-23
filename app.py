"""JobHunter AI desktop console."""

from __future__ import annotations

import atexit
import html
import json

import gradio as gr

from core.defaults import DEFAULT_PROFILE, HR_ACTIVITY_CHOICES, JOB_TYPE_CHOICES
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
#job-toolbar, #filter-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 18px 0 8px;
  position: relative;
  z-index: 1;
}
#job-toolbar label, #filter-panel label, #job-table label {
  position: static !important;
  display: block !important;
  transform: none !important;
  inset: auto !important;
  margin: 0 0 6px !important;
}
#job-toolbar .row, #filter-panel .row, .status-row {
  flex-wrap: wrap !important;
  gap: 10px !important;
}
.browser-actions, .work-actions {
  flex-wrap: nowrap !important;
  gap: 10px !important;
}
.browser-actions > *, .work-actions > *, .status-row > *, #filter-panel .row > *, #job-toolbar .row > * {
  flex: 1 1 0 !important;
  min-width: 0 !important;
}
.browser-actions button, .work-actions button {
  min-height: 42px;
  width: 100%;
  white-space: nowrap !important;
}
#job-toolbar textarea, #filter-panel textarea { min-height: 42px !important; }
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
#filter-panel { padding-bottom: 64px; }
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
        for name in JOB_COLUMNS:
            if name == "岗位详情":
                title = html.escape(str(row.get("职位名称") or "").replace("\n", " "), quote=True)
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
            else:
                cells.append(f"<td>{html.escape(str(row.get(name) if row.get(name) is not None else ''))}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
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
