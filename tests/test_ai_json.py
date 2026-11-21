"""AI JSON parsing and the score policy. These tests do not call the network."""

import pytest

from core.ai_filter import AIJsonError, apply_policy, parse_ai_payload
from core.models import recommendation_for_score
from core.rules import evaluate_rules, salary_bounds_k

PROFILE = {
    "cities": ["广州", "深圳", "东莞"],
    "salary_min_k": 10,
    "skills": ["Python", "FastAPI"],
    "hard_rules": {
        "reject_cities_outside": True,
        "reject_degrees": ["硕士", "博士", "研究生", "MBA", "EMBA"],
        "reject_experience_at_least_years": 3,
        "reject_salary_max_below_min": True,
    },
}


def _job(**overrides):
    job = {
        "title": "Python开发工程师",
        "company": "示例科技",
        "salary": "10-12K",
        "location": "深圳 南山",
        "education": "本科",
        "experience": "经验不限",
        "description": "使用 Python 和 FastAPI 开发接口",
    }
    job.update(overrides)
    return job


def test_score_bands():
    assert recommendation_for_score(90) == "优先投递"
    assert recommendation_for_score(100) == "优先投递"
    assert recommendation_for_score(89) == "推荐投递"
    assert recommendation_for_score(80) == "推荐投递"
    assert recommendation_for_score(79) == "人工复核"
    assert recommendation_for_score(70) == "人工复核"
    assert recommendation_for_score(69) == "不推荐"


def test_fenced_json_and_skill_filter():
    text = """```json
{"score": 95, "recommendation": "优先投递", "hard_reject": false,
 "hard_reject_reasons": [], "matched_skills": ["Python", "Rust"],
 "missing_requirements": [], "unknown_fields": [], "summary": "岗位匹配"}
```"""
    payload = parse_ai_payload(text)
    result = apply_policy(payload, _job(), PROFILE)
    assert result["score"] == 95
    assert result["recommendation"] == "优先投递"
    assert result["matched_skills"] == ["Python"]


def test_invalid_json_raises():
    with pytest.raises(AIJsonError):
        parse_ai_payload("这不是 JSON")
    with pytest.raises(AIJsonError):
        parse_ai_payload('{"recommendation": "优先投递"}')


def test_unknown_salary_cannot_be_treated_as_match():
    payload = parse_ai_payload(
        '{"score": 96, "recommendation": "优先投递", "hard_reject": false, "summary": "看起来合适"}'
    )
    result = apply_policy(payload, _job(salary=""), PROFILE)
    assert "salary" in result["unknown_fields"]
    assert result["score"] <= 79
    assert result["recommendation"] == "人工复核"


def test_model_hard_reject_is_kept():
    payload = parse_ai_payload(
        '{"score": 93, "recommendation": "优先投递", "hard_reject": true, "hard_reject_reasons": ["地点不符"], "summary": "排除"}'
    )
    result = apply_policy(payload, _job(), PROFILE)
    assert result["hard_reject"] is True
    assert result["recommendation"] == "直接拒绝"
    assert result["score"] <= 59


def test_local_rules_do_not_assume_unknown_fields():
    assert salary_bounds_k("5000-8000元·14薪") == (5, 8)
    assert salary_bounds_k("1.2-1.5万") == (12, 15)
    assert salary_bounds_k("面议") == (None, None)
    low_salary = evaluate_rules(_job(salary="5000-8000元·14薪"), PROFILE)
    assert low_salary.hard_reject
    degree = evaluate_rules(_job(education="硕士"), PROFILE)
    assert degree.hard_reject
    experience = evaluate_rules(_job(experience="3-5年"), PROFILE)
    assert experience.hard_reject
    city = evaluate_rules(_job(location="北京"), PROFILE)
    assert city.hard_reject
    unknown = evaluate_rules(_job(education="", experience="", salary=""), PROFILE)
    assert unknown.hard_reject is False
    assert {"education", "experience", "salary"} <= set(unknown.unknown_fields)
    accepted = evaluate_rules(_job(), PROFILE)
    assert accepted.hard_reject is False


def test_blocks_and_salary_floor():
    flexible = dict(PROFILE)
    flexible["salary_floor_k"] = 7
    flexible["blocked_titles"] = ["客服", "销售专员"]
    flexible["blocked_keywords"] = ["无底薪"]
    lowered = evaluate_rules(_job(salary="8-10K"), flexible)
    assert lowered.hard_reject is False
    high_pay = evaluate_rules(_job(salary="15-25K"), flexible)
    assert high_pay.hard_reject is False
    too_low = evaluate_rules(_job(salary="4-6K"), flexible)
    assert too_low.hard_reject
    blocked_title = evaluate_rules(_job(title="在线客服"), flexible)
    assert blocked_title.hard_reject
    blocked_word = evaluate_rules(_job(description="无底薪，提成制"), flexible)
    assert blocked_word.hard_reject


def test_company_size_respects_the_custom_minimum():
    profile = dict(PROFILE)
    profile["min_company_size"] = 100
    assert evaluate_rules(_job(company_size="20-99人"), profile).hard_reject
    assert evaluate_rules(_job(company_size="500-999人"), profile).hard_reject is False
    assert evaluate_rules(_job(company_size="10000人以上"), profile).hard_reject is False
    assert evaluate_rules(_job(company_size=""), profile).hard_reject is False
    profile["min_company_size"] = 0
    assert evaluate_rules(_job(company_size="20-99人"), profile).hard_reject is False


def test_one_basic_skill_can_still_be_applied():
    flexible = dict(PROFILE)
    flexible["salary_floor_k"] = 7
    payload = parse_ai_payload(
        '{"score": 64, "recommendation": "不推荐", "hard_reject": false, "matched_skills": ["Python"], "summary": "会基础的 Python"}'
    )
    result = apply_policy(payload, _job(salary="8-12K", description="熟悉 Python 即可"), flexible)
    assert result["score"] >= 80
    assert result["recommendation"] == "推荐投递"


def test_slightly_lower_pay_can_still_be_recommended():
    flexible = dict(PROFILE)
    flexible["salary_floor_k"] = 7
    flexible["preferred_keywords"] = ["Python"]
    payload = parse_ai_payload(
        '{"score": 76, "recommendation": "人工复核", "hard_reject": false, "matched_skills": ["Python"], "summary": "方向接近"}'
    )
    result = apply_policy(payload, _job(salary="7-9K", description="使用 Python 开发接口"), flexible)
    assert result["score"] >= 80
    assert result["recommendation"] == "推荐投递"
    high = apply_policy(payload, _job(salary="18-25K"), flexible)
    assert high["hard_reject"] is False


def test_think_block_is_ignored():
    payload = parse_ai_payload(
        '<think>internal</think>\n{"score": 88, "recommendation": "推荐投递", "summary": "匹配",'
        ' "matched_skills": [], "risks": [], "hard_reject": false}'
    )
    assert payload["score"] == 88
