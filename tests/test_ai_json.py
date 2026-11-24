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


def test_json_buried_in_prose_still_parses():
    text = """筛选结果如下：
{"score": "86.0", "recommendation": "推荐投递", "hard_reject": false, "summary": "基础技能对得上",}
后面还有一句说明。"""
    payload = parse_ai_payload(text)
    assert payload["score"] == 86
    assert payload["recommendation"] == "推荐投递"
    assert payload["summary"] == "基础技能对得上"


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


def test_active_hr_gets_a_small_boost():
    flexible = dict(PROFILE)
    flexible["salary_floor_k"] = 7
    flexible["prefer_active_hr"] = True
    payload = parse_ai_payload(
        '{"score": 80, "recommendation": "推荐投递", "hard_reject": false, "matched_skills": ["Python"], "summary": "方向合适"}'
    )
    quiet = apply_policy(payload, _job(salary="10-12K", hr_activity="本月活跃"), flexible)
    active = apply_policy(payload, _job(salary="10-12K", hr_activity="当前在线 · 总回复 30+"), flexible)
    assert active["score"] >= quiet["score"]
    assert active["score"] >= 82


def test_hr_activity_and_job_type_are_hard_rules():
    profile = dict(PROFILE)
    profile["hr_activity_max"] = "本周活跃"
    profile["job_types"] = ["正式", "社招"]
    stale = evaluate_rules(_job(hr_activity="本月活跃"), profile)
    assert stale.hard_reject
    fresh = evaluate_rules(_job(hr_activity="刚刚活跃"), profile)
    assert fresh.hard_reject is False
    intern = evaluate_rules(_job(title="Python实习生", description="实习岗位"), profile)
    assert intern.hard_reject
    full = evaluate_rules(_job(title="Python开发工程师", description="全职社招"), profile)
    assert full.hard_reject is False


def test_pay_below_preferred_stays_and_pay_above_is_rejected_when_capped():
    profile = dict(PROFILE)
    profile["salary_floor_k"] = 4
    profile["salary_min_k"] = 17
    profile["prefer_salary_uncapped"] = False
    assert evaluate_rules(_job(salary="8-12K"), profile).hard_reject is False
    assert evaluate_rules(_job(salary="15-17K"), profile).hard_reject is False
    assert evaluate_rules(_job(salary="18-25K"), profile).hard_reject
    assert evaluate_rules(_job(salary="3-3.5K"), profile).hard_reject
    open_ended = dict(profile)
    open_ended["prefer_salary_uncapped"] = True
    assert evaluate_rules(_job(salary="18-25K"), open_ended).hard_reject is False


def test_cohort_and_graduation_rules():
    student = dict(PROFILE)
    student["job_types"] = ["实习", "校招", "正式", "社招"]
    student["education"] = {"graduation_year": 2027, "status": "在读", "degree": "本科"}
    student["resume_text"] = "东莞城市学院 人工智能本科 2023.09-2027.06"
    campus = evaluate_rules(_job(title="Python开发", description="招聘2027届，可实习"), student)
    assert campus.hard_reject is False
    wrong_class = evaluate_rules(_job(title="26届校招Python", description="只招2026届毕业生"), student)
    assert wrong_class.hard_reject
    assert "2026届" in "；".join(wrong_class.reasons)
    preferred_only = evaluate_rules(_job(description="校招，26届优先，27届也可投"), student)
    assert preferred_only.hard_reject is False
    formal = evaluate_rules(_job(title="Python开发工程师", description="全职正式，需毕业证"), student)
    assert formal.hard_reject
    assert "毕业证" in "；".join(formal.reasons)
    convertible = evaluate_rules(
        _job(title="Python开发工程师", description="全职正式，可提前实习并提供转正"),
        student,
    )
    assert convertible.hard_reject is False
    graduate = dict(student)
    graduate["education"] = {"graduation_year": 2024, "status": "已毕业", "degree": "本科"}
    graduate["resume_text"] = "2024届本科毕业"
    hired = evaluate_rules(_job(title="Python开发工程师", description="社招全职"), graduate)
    assert hired.hard_reject is False


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


def test_stop_does_not_wait_for_a_hung_model():
    import threading
    import time

    from core.ai_filter import AIInterrupted, _run_interruptible

    stop = threading.Event()

    def hang(box):
        time.sleep(30)
        return "late"

    stop.set()
    started = time.perf_counter()
    with pytest.raises(AIInterrupted):
        _run_interruptible(hang, stop, 30)
    assert time.perf_counter() - started < 1.5


def test_saved_model_wins_over_defaults():
    from core.ai_filter import MODEL, OLLAMA_URL, normalize_model_settings, resolve_model_settings

    saved = resolve_model_settings(
        {
            "model": {
                "host": "http://10.0.0.8:11434",
                "name": "qwen2.5:7b",
                "temperature": 0.4,
            }
        }
    )
    assert saved["host"] == "http://10.0.0.8:11434"
    assert saved["name"] == "qwen2.5:7b"
    assert saved["temperature"] == 0.4
    assert saved["num_predict"] == 480
    assert saved["disable_think"] is True

    fallback = resolve_model_settings({})
    assert fallback["name"] == MODEL
    assert fallback["host"] == OLLAMA_URL

    with pytest.raises(ValueError):
        normalize_model_settings("localhost:11434", "qwen3.5:9b", 0.1, 480, 140, True, "30m")
