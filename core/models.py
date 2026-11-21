"""Shared job fields and recommendation bands."""

from __future__ import annotations

SOURCE_NAME = "智联招聘"
RECOMMENDATIONS = ("优先投递", "推荐投递", "人工复核", "不推荐", "直接拒绝")
QUEUE_RECOMMENDATIONS = ("优先投递", "推荐投递")


def recommendation_for_score(score: int) -> str:
    if score >= 90:
        return "优先投递"
    if score >= 80:
        return "推荐投递"
    if score >= 70:
        return "人工复核"
    return "不推荐"
