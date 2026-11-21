"""Deterministic exclusions. Unknown fields are never treated as a match."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class RuleResult:
    hard_reject: bool
    reasons: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)


def salary_bounds_k(text: str) -> tuple[float | None, float | None]:
    if not text or "面议" in text:
        return None, None
    main = text.split("·")[0]
    numbers = re.findall(r"\d+(?:\.\d+)?", main)
    if not numbers:
        return None, None
    values = [float(number) for number in numbers[:2]]
    if "万" in main:
        values = [value * 10 for value in values]
    elif max(values) >= 100:
        values = [value / 1000 for value in values]
    return values[0], values[-1]


def experience_years_min(text: str) -> int | None:
    if not text:
        return None
    if any(marker in text for marker in ("经验不限", "应届", "在校", "无需经验")):
        return 0
    if re.search(r"\d+\s*年\s*以下", text):
        return 0
    ranged = re.search(r"(\d+)\s*[-~～到至]\s*(\d+)\s*年", text)
    if ranged:
        return int(ranged.group(1))
    at_least = re.search(r"(\d+)\s*年\s*以上", text)
    if at_least:
        return int(at_least.group(1))
    return None


def listed_text(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def matching_terms(text: str, terms: list[str]) -> list[str]:
    haystack = (text or "").lower()
    found: list[str] = []
    for term in terms:
        token = term.strip()
        if token and token.lower() in haystack and token not in found:
            found.append(token)
    return found


def company_smaller_than(text: str, minimum: int) -> bool:
    """A closed range is too small only when its top end is under the setting."""
    ranged = re.search(r"(\d+)\s*[-~～到至]\s*(\d+)\s*人", text or "")
    if not ranged:
        return False
    return int(ranged.group(2)) < int(minimum)


def degree_rejected(education: str, rejected_degrees: list[str]) -> bool:
    if not education:
        return False
    lowered = education.lower()
    return any(degree.lower() in lowered for degree in rejected_degrees)


def evaluate_rules(job: dict, profile: dict) -> RuleResult:
    rules = profile.get("hard_rules") or {}
    reasons: list[str] = []
    unknown: list[str] = []

    location = re.sub(r"\s+", "", job.get("location") or "")
    cities = [str(city) for city in profile.get("cities") or []]
    if not location:
        unknown.append("location")
    elif rules.get("reject_cities_outside", True) and not any(city in location for city in cities):
        reasons.append(f"必须拒绝：工作地点是{job.get('location')}，不在已选城市（{'、'.join(cities)}）")

    education = (job.get("education") or "").strip()
    if not education:
        unknown.append("education")
    elif degree_rejected(education, list(rules.get("reject_degrees") or [])):
        reasons.append(f"必须拒绝：学历要求是{education}，硕士、博士、研究生不投")

    experience = (job.get("experience") or "").strip()
    if not experience:
        unknown.append("experience")
    else:
        minimum = experience_years_min(experience)
        threshold = int(rules.get("reject_experience_at_least_years") or 3)
        if minimum is None:
            unknown.append("experience")
        elif minimum >= threshold:
            reasons.append(f"必须拒绝：经验要求是{experience}，达到排除线（{threshold}年及以上）")

    salary = (job.get("salary") or "").strip()
    _, high = salary_bounds_k(salary)
    if not salary or high is None:
        unknown.append("salary")
    elif rules.get("reject_salary_max_below_min", True):
        floor = profile.get("salary_floor_k")
        if floor is None:
            floor = profile.get("salary_min_k") or 0
        if high < float(floor):
            reasons.append(f"必须拒绝：薪资上限是{salary}，低于保底{int(float(floor))}K。薪资没有上限，只有上限低于保底才拒绝")

    title = job.get("title") or ""
    description = job.get("description") or ""
    blocked_titles = matching_terms(title, listed_text(profile.get("blocked_titles")))
    if blocked_titles:
        reasons.append("必须拒绝：岗位名称命中屏蔽：" + "、".join(blocked_titles))
    blocked_keywords = matching_terms(f"{title}\n{description}", listed_text(profile.get("blocked_keywords")))
    if blocked_keywords:
        reasons.append("必须拒绝：职位信息命中屏蔽词：" + "、".join(blocked_keywords))

    minimum_size = int(profile.get("min_company_size") if profile.get("min_company_size") is not None else 100)
    size_text = (job.get("company_size") or "").strip()
    if minimum_size > 0 and size_text and company_smaller_than(size_text, minimum_size):
        reasons.append(f"必须拒绝：公司规模是{size_text}，低于设定的{minimum_size}人")

    return RuleResult(hard_reject=bool(reasons), reasons=reasons, unknown_fields=unknown)
