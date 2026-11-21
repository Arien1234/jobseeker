"""Redaction and the decision to click 立即投递. No browser and no model."""

from core.application import should_auto_apply
from core.auto_apply import classify_apply_text, dismiss_label
from core.resume_parser import redact_personal_info

PROFILE = {"auto_apply": True, "min_apply_score": 80}


def test_stay_on_page_instead_of_chat():
    assert dismiss_label(["继续沟通", "留在此页"]) == "留在此页"
    assert dismiss_label(["继续沟通"]) is None
    assert dismiss_label(["关闭", "继续沟通"]) == "关闭"


def test_redact_drops_contact_lines_and_short_name():
    raw = "\n".join(
        [
            "张三",
            "手机号：13800138000",
            "邮箱：person@example.com",
            "微信：wechat-id",
            "https://github.com/example/project",
            "示例大学 计算机 本科",
            "使用 Python 和 FastAPI",
        ]
    )
    cleaned = redact_personal_info(raw)
    assert "张三" not in cleaned
    assert "13800138000" not in cleaned
    assert "person@example.com" not in cleaned
    assert "微信" not in cleaned
    assert "github.com" not in cleaned
    assert "示例大学" in cleaned
    assert "FastAPI" in cleaned


def test_auto_apply_requires_a_clear_match():
    ok, _reason = should_auto_apply(
        {"score": 91, "recommendation": "优先投递", "hard_reject": False, "unknown_fields": []},
        PROFILE,
    )
    assert ok
    blocked, reason = should_auto_apply(
        {"score": 96, "recommendation": "人工复核", "hard_reject": False, "unknown_fields": ["salary"]},
        PROFILE,
    )
    assert blocked is False
    assert "salary" in reason
    low, _low_reason = should_auto_apply(
        {"score": 70, "recommendation": "人工复核", "hard_reject": False, "unknown_fields": []},
        PROFILE,
    )
    assert low is False


def test_apply_success_text():
    assert classify_apply_text("已向对方发送简历和打招呼语") == "submitted"
    assert classify_apply_text("请完成安全验证") == "blocked"
    assert classify_apply_text("职位详情") == "failed"
