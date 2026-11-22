"""JobHunter AI desktop console."""

from __future__ import annotations

import atexit

import pandas as pd
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
#job-toolbar .row, #filter-panel .row, .main-actions, .status-row {
  flex-wrap: wrap !important;
  gap: 10px !important;
}
.main-actions > *, .status-row > *, #filter-panel .row > *, #job-toolbar .row > * {
  flex: 1 1 180px !important;
  min-width: min(180px, 100%) !important;
}
.main-actions button { min-height: 42px; width: 100%; }
#job-toolbar textarea, #filter-panel textarea { min-height: 42px !important; }
#job-table {
  width: 100% !important;
  margin-top: 8px;
  overflow-x: auto !important;
}
#job-table table { font-size: 14px; min-width: 860px; }
#job-dialog {
  position: fixed !important;
  z-index: 3000 !important;
  left: 50% !important;
  top: 8vh !important;
  transform: translateX(-50%);
  width: min(760px, calc(100vw - 32px)) !important;
  max-height: 80vh !important;
  overflow: auto !important;
  padding: 18px 18px 14px !important;
  border-radius: 14px !important;
  background: var(--body-background-fill, #0b0f19) !important;
  border: 1px solid var(--border-color-primary, #243044) !important;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.45);
}
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
  .main-actions > *, .status-row > * { flex-basis: 100% !important; }
  #job-table table { min-width: 640px; font-size: 13px; }
}
"""


def _frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in columns})
    frame = pd.DataFrame(rows)
    for name in columns:
        if name not in frame.columns:
            frame[name] = ""
    return frame[columns]


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
    return gr.update(value=label, variant="stop" if label == "停止采集" else "primary")


def poll():
    state = controller.view()
    jobs = state["jobs"] or []
    return (
        state["login"],
        state["task"],
        state["progress"],
        str(state["collected"]),
        str(state["applied"]),
        state["logs"],
        _frame(jobs, JOB_COLUMNS),
        state["resume"],
        _crawl_update(),
    )


def toggle_crawl():
    message = controller.toggle_crawl()
    return message, _crawl_update()


def open_job_detail(evt: gr.SelectData):
    index = getattr(evt, "index", None)
    if not isinstance(index, (list, tuple)) or len(index) < 2:
        return gr.update(), gr.update()
    column = int(index[1])
    if column < 0 or column >= len(JOB_COLUMNS) or JOB_COLUMNS[column] != "岗位详情":
        return gr.update(), gr.update()
    title = ""
    company = ""
    row_value = getattr(evt, "row_value", None)
    if isinstance(row_value, dict):
        title = str(row_value.get("职位名称") or "")
        company = str(row_value.get("公司") or "")
    elif isinstance(row_value, (list, tuple)) and len(row_value) >= 4:
        title = str(row_value[2] or "")
        company = str(row_value[3] or "")
    detail = controller.db.job_detail_markdown(int(index[0]), title, company)
    return detail, gr.update(visible=True)


def build_app() -> gr.Blocks:
    profile = load_profile()
    initial_jobs = _frame(controller.view().get("jobs") or [], JOB_COLUMNS)

    with gr.Blocks(title="JobHunter AI") as demo:
        with gr.Column(elem_id="app-top"):
            gr.Markdown("## JobHunter")

        with gr.Tabs():
            with gr.Tab("岗位列表"):
                with gr.Column(elem_id="job-toolbar"):
                    with gr.Row(elem_classes=["main-actions"]):
                        start_browser = gr.Button("启动浏览器")
                        start_crawl = gr.Button("开始采集", variant="primary")
                        start_apply = gr.Button("筛选并投递", variant="primary")
                        pause_collect = gr.Button("暂停")
                        stop_collect = gr.Button("停止投递")
                        clear_jobs = gr.Button("清空列表")
                        close_browser = gr.Button("关闭浏览器")
                    action_box = gr.Textbox(label="操作结果", interactive=False, max_lines=1)
                    with gr.Row(elem_classes=["status-row"]):
                        login_box = gr.Textbox(label="登录状态", interactive=False, max_lines=1)
                        task_box = gr.Textbox(label="当前任务", interactive=False, max_lines=1)
                        progress_box = gr.Textbox(label="进度", interactive=False, max_lines=1)
                        collected_box = gr.Textbox(label="已采集", interactive=False, max_lines=1)
                        applied_box = gr.Textbox(label="本批已投递", interactive=False, max_lines=1)
                    apply_count = gr.Slider(1, 200, value=150, step=1, label="本批投递数量")
                    log_box = gr.Textbox(label="实时日志", lines=6, interactive=False)

                with gr.Column(visible=False, elem_id="job-dialog") as job_dialog:
                    detail_box = gr.Markdown()
                    close_detail = gr.Button("关闭详情")

                job_table = gr.Dataframe(
                        value=initial_jobs,
                        headers=JOB_COLUMNS,
                        label=f"当前职位（{len(initial_jobs)}）",
                        interactive=False,
                        wrap=True,
                        elem_id="job-table",
                    )

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
            poll,
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
}
""",
        )

        job_table.select(open_job_detail, outputs=[detail_box, job_dialog])
        close_detail.click(lambda: gr.update(visible=False), outputs=job_dialog)
        start_browser.click(controller.start_browser, outputs=action_box)
        start_crawl.click(toggle_crawl, outputs=[action_box, start_crawl])
        start_apply.click(controller.start_apply, inputs=apply_count, outputs=action_box)
        pause_collect.click(controller.pause_or_resume, outputs=action_box)
        stop_collect.click(controller.stop, outputs=action_box)
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
