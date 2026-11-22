"""Local Ollama matching. Scores are stored only after a real response or a local hard rule."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from core.job_parser import clean_inline
from core.logging_config import get_logger
from core.models import RECOMMENDATIONS, recommendation_for_score
from core.rules import evaluate_rules, listed_text, matching_terms, salary_bounds_k

logger = get_logger("ai")
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

SYSTEM_PROMPT = """你是求职匹配助手。必须先读完简历原文，再读完职位描述。职位描述里的技能标签、岗位职责和任职要求都要一起看。
用这些完整信息判断这个岗位值不值得投。简历里已经有这个职位的基础技能，就可以投。基础技能是职位最主要的语言或方向，能在简历技能或项目里对上即可。不会加分项、不会次要语言、经验年限略短，都只是小瑕疵，给 80 到 88，推荐投递。不要因为这些打到 80 分以下。
方向完全不同、基础技能一项都对不上时，才低于 70。简历里的项目或技能能对上、薪资也达到优先线时，给 90 以上。
禁止编造简历中不存在的项目、公司或经历。简历原文为空时，总结中不得出现任何项目名称。
总结要写出简历里哪一段和职位描述里的哪一项对上了。
matched_skills 只能填写画像技能列表中的技能，并且该技能必须出现在职位名称或职位描述中。
学历、工作经验、薪资、地点只要职位没有写明，就必须放入 unknown_fields，不得当成符合要求。
只有下面几条才允许 hard_reject，summary 必须写成「必须拒绝：」加具体原因：城市不在已选城市、学历要求硕士或博士或研究生、经验年限达到排除线、薪资上限低于保底、岗位名称命中屏蔽、描述命中屏蔽词、公司规模写明了并且上限低于用户设定的人数。
薪资没有上限。上限越高越好，不得因为薪资高、或薪资区间的下限偏低而降分或拒绝。只要上限不低于保底就可以接受。
工作地点只看职位地点。客户公司地址、公司总部地址不能当成工作地点。发布方是人力资源或人才经纪公司时，只要职位地点和内容合适，不要因此拒绝。
公司规模看页面写明的人数。客户公司人数优先，没有客户公司人数时看公司信息里的规模。达到用户设定的人数是加分。规模没写不要拒绝，也不要因此降到 80 分以下。
HR 活跃状态看页面写明的标签。比用户设定的活跃下限更旧的，由本地规则硬拒绝，你不要再 hard_reject。当前在线或刚刚活跃可在总结里加一句加分。活跃没写不要因此拒绝。
岗位类型（实习/正式/校招/社招）由本地硬规则处理，你不要因为类型再 hard_reject。
如果不是必须拒绝，summary 写清简历哪一段对上了职位描述的哪一项，以及可以接受的小瑕疵。
只输出一个 JSON 对象，不要 Markdown。字段为 score、recommendation、hard_reject、hard_reject_reasons、matched_skills、missing_requirements、unknown_fields、summary。summary 用中文，不超过 180 字。"""


class AIError(Exception):
    """The model could not be asked or its answer could not be used."""


class AIJsonError(AIError):
    """The model response was not the expected JSON object."""


def strip_think(text: str) -> str:
    return _THINK_BLOCK.sub("", text or "").strip()


def strip_fence(text: str) -> str:
    value = strip_think(text).strip()
    if value.startswith("```"):
        lines = value.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [clean_inline(str(item)) for item in value if clean_inline(str(item))]


def parse_ai_payload(text: str) -> dict:
    try:
        data = json.loads(strip_fence(text))
    except json.JSONDecodeError as exc:
        raise AIJsonError("JSON 解析失败") from exc
    if not isinstance(data, dict) or "score" not in data or isinstance(data.get("score"), bool):
        raise AIJsonError("JSON 解析失败")
    try:
        score = int(data["score"])
    except (TypeError, ValueError) as exc:
        raise AIJsonError("JSON 解析失败") from exc
    score = max(0, min(100, score))
    recommendation = data.get("recommendation")
    if recommendation not in RECOMMENDATIONS:
        recommendation = recommendation_for_score(score)
    return {
        "score": score,
        "recommendation": recommendation,
        "hard_reject": bool(data.get("hard_reject")),
        "hard_reject_reasons": _string_list(data.get("hard_reject_reasons")),
        "matched_skills": _string_list(data.get("matched_skills")),
        "missing_requirements": _string_list(data.get("missing_requirements")),
        "unknown_fields": _string_list(data.get("unknown_fields")),
        "summary": clean_inline(str(data.get("summary") or ""))[:180],
    }


def _skills_in_job(job: dict, profile: dict, claimed: list[str]) -> list[str]:
    haystack = f"{job.get('title') or ''}\n{job.get('description') or ''}".lower()
    claimed_text = " ".join(claimed).lower()
    matched = []
    for skill in profile.get("skills") or []:
        needle = str(skill).lower()
        if needle and needle in haystack and needle in claimed_text:
            matched.append(str(skill))
    return matched


def apply_policy(payload: dict, job: dict, profile: dict, extra_unknown: list[str] | None = None) -> dict:
    result = dict(payload)
    unknown = []
    for name in list(result.get("unknown_fields") or []) + list(extra_unknown or []):
        if name not in unknown:
            unknown.append(name)
    for field in ("education", "experience", "salary", "location"):
        if not (job.get(field) or "").strip() and field not in unknown:
            unknown.append(field)
    result["unknown_fields"] = unknown
    result["matched_skills"] = _skills_in_job(job, profile, result.get("matched_skills") or [])
    if result.get("hard_reject"):
        if not result["hard_reject_reasons"]:
            result["hard_reject_reasons"] = ["模型判定命中排除条件"]
        result["recommendation"] = "直接拒绝"
        result["score"] = min(int(result["score"]), 59)
        summary = clean_inline(str(result.get("summary") or ""))
        if not summary.startswith("必须拒绝"):
            reasons = "；".join(result.get("hard_reject_reasons") or [])
            result["summary"] = clean_inline(f"必须拒绝：{reasons or summary}")[:180]
        return result
    critical_unknown = [name for name in unknown if name in {"education", "experience", "salary", "location"}]
    score = int(result["score"])
    if critical_unknown and score >= 80:
        score = min(score, 79)
        summary = result.get("summary") or ""
        note = "关键信息未知，不能视为符合，已改为人工复核。"
        result["summary"] = clean_inline(f"{summary} {note}")[:180]
    result["score"] = score
    result["recommendation"] = recommendation_for_score(score)
    result["hard_reject"] = False
    return _favor_reachable_jobs(result, job, profile)


def _favor_reachable_jobs(result: dict, job: dict, profile: dict) -> dict:
    """Keep strong matches easy to find, including slightly lower pay and clearly higher pay."""
    if result.get("hard_reject"):
        return result
    unknown = set(result.get("unknown_fields") or [])
    if unknown & {"education", "experience", "salary", "location"}:
        return result
    _, high = salary_bounds_k(job.get("salary") or "")
    if high is None:
        return result
    prefer = float(profile.get("salary_min_k") or 0)
    floor_value = profile.get("salary_floor_k")
    floor = float(floor_value if floor_value is not None else prefer)
    skills = result.get("matched_skills") or []
    preferred_hits = matching_terms(
        f"{job.get('title') or ''}\n{job.get('description') or ''}",
        listed_text(profile.get("preferred_keywords")),
    )
    score = int(result["score"])
    if high >= prefer and len(skills) >= 2 and 80 <= score < 90:
        score = min(94, score + 4)
    elif high >= floor and len(skills) >= 1 and 60 <= score < 80:
        score = 82
    elif high >= prefer and preferred_hits and len(skills) >= 1 and 80 <= score < 96:
        score = min(96, score + 2)
    activity = job.get("hr_activity") or ""
    if profile.get("prefer_active_hr", True) and any(
        marker in activity for marker in ("当前在线", "刚刚活跃", "今日活跃")
    ):
        score = min(98, score + 2)
    result["score"] = score
    result["recommendation"] = recommendation_for_score(score)
    return result


def local_rejection(job: dict, profile: dict) -> dict | None:
    decision = evaluate_rules(job, profile)
    if not decision.hard_reject:
        return None
    summary = "；".join(decision.reasons)[:180]
    result = {
        "score": 0,
        "recommendation": "直接拒绝",
        "hard_reject": True,
        "hard_reject_reasons": decision.reasons,
        "matched_skills": [],
        "missing_requirements": [],
        "unknown_fields": decision.unknown_fields,
        "summary": summary,
        "source": "local_rule",
    }
    result["raw_json"] = json.dumps(result, ensure_ascii=False)
    return result


def build_user_message(job: dict, profile: dict, resume_text: str) -> str:
    resume = resume_text or ""
    truncated = False
    if len(resume) > 12000:
        resume = resume[:12000]
        truncated = True
    public_profile = {
        "target_roles": profile.get("target_roles") or [],
        "cities": profile.get("cities") or [],
        "salary_floor_k": profile.get("salary_floor_k"),
        "salary_min_k": profile.get("salary_min_k"),
        "salary_max_k": profile.get("salary_max_k"),
        "salary_type": profile.get("salary_type"),
        "salary_policy": "salary_floor_k 是保底，只有薪资上限低于它才直接拒绝。薪资没有上限，越高越好。",
        "education": profile.get("education") or {},
        "skills": profile.get("skills") or [],
        "projects": profile.get("projects") or [],
        "blocked_titles": listed_text(profile.get("blocked_titles")),
        "blocked_keywords": listed_text(profile.get("blocked_keywords")),
        "preferred_keywords": listed_text(profile.get("preferred_keywords")),
        "project_policy": profile.get("project_policy") or "",
        "hard_rules": profile.get("hard_rules") or {},
    }
    payload = {
        "profile": public_profile,
        "resume_text": resume,
        "resume_truncated": truncated,
        "resume_uploaded": bool(resume_text),
        "job": {
            "title": job.get("title") or "",
            "company": job.get("company") or "",
            "salary": job.get("salary") or "",
            "location": job.get("location") or "",
            "education": job.get("education") or "",
            "experience": job.get("experience") or "",
            "company_size": job.get("company_size") or "",
            "min_company_size": profile.get("min_company_size") if profile.get("min_company_size") is not None else 20,
            "hr_activity": job.get("hr_activity") or "",
            "hr_activity_max": profile.get("hr_activity_max") or "本月活跃",
            "prefer_active_hr": bool(profile.get("prefer_active_hr", True)),
            "job_types": profile.get("job_types") or ["实习", "正式", "校招", "社招"],
            "prefer_salary_uncapped": bool(profile.get("prefer_salary_uncapped", True)),
            "description": job.get("description") or "",
            "description_note": "description 是完整职位信息，包含技能标签、岗位职责和任职要求，必须整段阅读。hr_activity 是职位发布者活跃状态。",
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def call_ollama(user_message: str, timeout: int = 180) -> str:
    last_error = None
    for include_think in (True, False):
        payload = {
            "model": MODEL,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "options": {"temperature": 0.1},
        }
        if include_think:
            payload["think"] = False
        request = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            last_error = exc
            if include_think and exc.code == 400:
                continue
            logger.error("Ollama 调用失败: HTTP %s", exc.code)
            raise AIError(f"Ollama 调用失败: HTTP {exc.code}") from exc
        except Exception as exc:
            logger.error("Ollama 调用失败: %s", type(exc).__name__)
            raise AIError("Ollama 调用失败") from exc
    else:
        logger.error("Ollama 调用失败: HTTP %s", getattr(last_error, "code", ""))
        raise AIError("Ollama 调用失败")
    try:
        data = json.loads(raw)
        content = data["message"]["content"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        logger.error("JSON 解析失败")
        raise AIJsonError("JSON 解析失败") from exc
    if not str(content or "").strip():
        logger.error("Ollama 调用失败: 空回复")
        raise AIError("Ollama 调用失败: 空回复")
    return str(content)


def analyze_job(job: dict, profile: dict, resume_text: str, api_key: str | None = None) -> dict:
    rejected = local_rejection(job, profile)
    if rejected is not None:
        return rejected
    decision = evaluate_rules(job, profile)
    message = build_user_message(job, profile, resume_text)
    content = call_ollama(message)
    try:
        payload = parse_ai_payload(content)
    except AIJsonError:
        logger.error("JSON 解析失败")
        raise
    result = apply_policy(payload, job, profile, decision.unknown_fields)
    result["source"] = MODEL
    stored = dict(result)
    result["raw_json"] = json.dumps(stored, ensure_ascii=False)
    return result


def analyze_pending(database, profile: dict, stop_event=None, on_log=None) -> dict:
    resume_text = database.resume_text()
    done = 0
    failed = 0
    for job in database.list_unanalyzed():
        if stop_event is not None and stop_event.is_set():
            break
        label = f"{job.get('title') or ''} / {job.get('company') or ''}"
        try:
            result = analyze_job(job, profile, resume_text)
            database.save_analysis(job["id"], result)
            done += 1
            if on_log:
                on_log(f"已分析 {label}：{result['score']} {result['recommendation']}")
        except AIError:
            failed += 1
            if on_log:
                on_log(f"分析失败 {label}")
        except Exception:
            logger.error("数据库保存失败")
            failed += 1
            if on_log:
                on_log(f"分析失败 {label}")
    return {"analyzed": done, "failed": failed}
