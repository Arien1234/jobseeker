"""Build a manual application queue. This module never submits an application."""

from __future__ import annotations

from core.logging_config import get_logger
from core.rules import evaluate_rules

logger = get_logger("apply")

REQUIRED_FIELDS = ("title", "company", "url", "description", "location", "education", "experience", "salary")
MANUAL_REASON = "已人工确认。请在智联招聘页面手动完成投递，程序不会点击立即投递，也不会把状态写成已投递。"


def should_auto_apply(result: dict, profile: dict) -> tuple[bool, str]:
    if not profile.get("auto_apply", True):
        return False, "自动投递已关闭"
    if result.get("hard_reject"):
        return False, "命中硬性排除"
    unknown = set(result.get("unknown_fields") or [])
    missing = unknown & {"education", "experience", "salary", "location"}
    if missing:
        return False, "关键信息未知，不自动投递: " + ",".join(sorted(missing))
    minimum = int(profile.get("min_apply_score") or 80)
    score = int(result.get("score") or 0)
    if score < minimum:
        return False, f"分数低于 {minimum}"
    if result.get("recommendation") not in ("优先投递", "推荐投递"):
        return False, str(result.get("recommendation") or "未达到推荐")
    return True, ""


def check_job(job: dict, profile: dict) -> tuple[bool, str]:
    missing = [field for field in REQUIRED_FIELDS if not str(job.get(field) or "").strip()]
    if missing:
        return False, "信息不完整: " + ",".join(missing)
    if job.get("ai_hard_reject"):
        return False, "命中硬性排除"
    if job.get("ai_recommendation") not in ("优先投递", "推荐投递"):
        return False, "不是 AI 推荐投递结果"
    if job.get("ai_score") is None or int(job["ai_score"]) < 80:
        return False, "AI 评分未达到推荐线"
    decision = evaluate_rules(job, profile)
    if decision.hard_reject:
        return False, "；".join(decision.reasons)
    if decision.unknown_fields:
        return False, "存在未知信息，不能进入投递队列: " + ",".join(decision.unknown_fields)
    return True, ""


def enqueue_recommended(database, profile: dict) -> dict:
    inserted = 0
    duplicated = 0
    blocked = 0
    for job in database.recommendable_jobs():
        try:
            ok, reason = check_job(job, profile)
            outcome = database.create_application(
                job["id"],
                "pending_confirm" if ok else "blocked",
                "等待人工确认" if ok else reason,
            )
        except Exception as exc:
            logger.error("投递任务失败: %s", type(exc).__name__)
            blocked += 1
            continue
        if outcome == "duplicate":
            duplicated += 1
            logger.info("投递任务重复: %s / %s", job.get("title"), job.get("company"))
            continue
        if ok:
            inserted += 1
        else:
            blocked += 1
            logger.error("投递任务失败: %s / %s %s", job.get("title"), job.get("company"), reason)
    return {"queued": inserted, "duplicated": duplicated, "blocked": blocked}


def confirm_application(database, application_id: int) -> str:
    row = database.get_application(application_id)
    if row is None:
        logger.error("投递任务失败: 任务不存在")
        return "任务不存在"
    if row["status"] != "pending_confirm":
        return f"当前状态为 {row['status']}，不能确认"
    database.update_application(application_id, "awaiting_manual_submit", MANUAL_REASON)
    return "已记录人工确认。请打开职位页面手动投递。"


def cancel_application(database, application_id: int) -> str:
    row = database.get_application(application_id)
    if row is None:
        logger.error("投递任务失败: 任务不存在")
        return "任务不存在"
    if row["status"] in {"cancelled"}:
        return "任务已经取消"
    database.update_application(application_id, "cancelled", "用户取消，未提交投递")
    return "已取消，未提交投递"
