"""JobHunter AI desktop console."""

from __future__ import annotations

import pandas as pd
import gradio as gr

from core.logging_config import setup_logging
from core.profile_store import load_profile
from core.task_manager import CollectController

setup_logging()
controller = CollectController()

JOB_COLUMNS = [
    "编号",
    "职位名称",
    "公司",
    "薪资",
    "地点",
    "学历",
    "工作经验",
    "公司规模",
    "AI评分",
    "AI推荐结果",
    "筛选说明",
    "投递状态",
]


def _frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


def _file_path(uploaded) -> str:
    if uploaded is None:
        return ""
    if isinstance(uploaded, str):
        return uploaded
    return getattr(uploaded, "name", "") or ""


def _save_filters(url, cities, salary_floor, salary_min, roles, min_score, experience_years, blocked_titles, blocked_keywords, preferred_keywords, min_company_size):
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
    )


def poll():
    state = controller.view()
    return (
        state["login"],
        state["task"],
        state["progress"],
        str(state["collected"]),
        str(state["applied"]),
        state["logs"],
        _frame(state["jobs"], JOB_COLUMNS),
        state["resume"],
    )


def build_app() -> gr.Blocks:
    profile = load_profile()
    with gr.Blocks(title="JobHunter AI") as demo:
        gr.Markdown(
            "启动浏览器后，职位页会自动采集，换一页就删掉旧职位并重新采集。"
            "筛选和投递是同一步：本机模型按筛选条件处理已采集的职位，投出达到数量就停。"
            "登录和安全验证会停下来交给你。"
        )
        with gr.Row():
            start_browser = gr.Button("启动浏览器")
            start_apply = gr.Button("筛选并投递", variant="primary")
            pause_collect = gr.Button("暂停")
            stop_collect = gr.Button("停止")
            close_browser = gr.Button("关闭浏览器")
        action_box = gr.Textbox(label="操作结果", interactive=False)
        with gr.Row():
            login_box = gr.Textbox(label="登录状态", interactive=False)
            task_box = gr.Textbox(label="当前任务", interactive=False)
            progress_box = gr.Textbox(label="进度", interactive=False)
        with gr.Row():
            collected_box = gr.Textbox(label="已采集", interactive=False)
            applied_box = gr.Textbox(label="本批已投递", interactive=False)
        apply_count = gr.Slider(1, 200, value=20, step=1, label="本批投递数量")
        with gr.Accordion("筛选条件", open=True):
            gr.Markdown(
                "必须拒绝：城市不在已选城市、学历要求硕士及以上、经验达到排除年限、"
                "薪资上限低于保底、屏蔽岗位、屏蔽关键词、公司规模写明了且上限低于下面的人数。"
                "规模没写不会拒绝。简历里有这个岗位的基础技能就可以投。"
            )
            target_url = gr.Textbox(
                label="职位页面",
                value=profile.get("target_url") or "https://www.zhaopin.com/jobs/?pageMode=recommend",
            )
            filter_cities = gr.Textbox(label="城市，逗号分隔", value="，".join(profile.get("cities") or []))
            with gr.Row():
                salary_floor = gr.Number(
                    label="保底薪资（K）",
                    value=profile.get("salary_floor_k") if profile.get("salary_floor_k") is not None else 7,
                    precision=0,
                )
                filter_salary_min = gr.Number(
                    label="优先薪资（K）",
                    value=profile.get("salary_min_k") or 10,
                    precision=0,
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
                    value=100 if profile.get("min_company_size") is None else profile.get("min_company_size"),
                    precision=0,
                )
            with gr.Row():
                filter_roles = gr.Textbox(
                    label="目标岗位，一行一个",
                    lines=6,
                    value="\n".join(profile.get("target_roles") or []),
                )
                blocked_titles = gr.Textbox(
                    label="屏蔽岗位，逗号或换行",
                    lines=6,
                    value="，".join(profile.get("blocked_titles") or []),
                )
            with gr.Row():
                preferred_keywords = gr.Textbox(
                    label="优先关键词",
                    lines=3,
                    value="，".join(profile.get("preferred_keywords") or []),
                )
                blocked_keywords = gr.Textbox(
                    label="屏蔽关键词",
                    lines=3,
                    value="，".join(profile.get("blocked_keywords") or []),
                )
            save_filters = gr.Button("保存筛选条件")
            filter_message = gr.Textbox(label="保存结果", interactive=False)
        with gr.Accordion("简历", open=False):
            resume_box = gr.Textbox(label="简历状态", interactive=False)
            resume_file = gr.File(label="上传 PDF 简历", file_types=[".pdf"])
            resume_button = gr.Button("解析并保存简历")
        log_box = gr.Textbox(label="实时日志", lines=10, interactive=False)
        clear_jobs = gr.Button("清空并重新采集")
        job_table = gr.Dataframe(label="当前页面职位", interactive=False)

        outputs = [
            login_box,
            task_box,
            progress_box,
            collected_box,
            applied_box,
            log_box,
            job_table,
            resume_box,
        ]
        timer = gr.Timer(1)
        timer.tick(poll, outputs=outputs, show_progress="hidden")
        demo.load(poll, outputs=outputs)

        start_browser.click(controller.start_browser, outputs=action_box)
        start_apply.click(controller.start_apply, inputs=apply_count, outputs=action_box)
        pause_collect.click(controller.pause_or_resume, outputs=action_box)
        stop_collect.click(controller.stop, outputs=action_box)
        close_browser.click(controller.close_browser, outputs=action_box)
        clear_jobs.click(controller.clear_jobs, outputs=action_box)
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
            ],
            outputs=filter_message,
        )
        resume_button.click(
            lambda uploaded: controller.save_resume(_file_path(uploaded)) if _file_path(uploaded) else "请先选择 PDF",
            inputs=resume_file,
            outputs=filter_message,
        )
    return demo


def main() -> None:
    app = build_app()
    app.launch(
        server_name="127.0.0.1",
        server_port=7860,
        theme=gr.themes.Soft(),
        inbrowser=False,
    )


if __name__ == "__main__":
    main()
