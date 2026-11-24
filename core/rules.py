"""Deterministic exclusions. Unknown fields are never treated as a match."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date


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


HR_ACTIVITY_LEVELS = (
    "当前在线",
    "刚刚活跃",
    "今日活跃",
    "3日内活跃",
    "本周活跃",
    "本月活跃",
)

_OLD_ACTIVITY = re.compile(
    r"半年|几个月|两月|三月|三月前|很久|近期较少|很少活跃|不活跃|月前活跃|年前",
)


def hr_activity_level(text: str) -> int | None:
    """Return freshness rank. Lower is fresher. None means unknown or missing."""
    value = (text or "").split("·")[0].strip()
    if not value:
        return None
    if value == "在线" or value.startswith("当前在线"):
        return 0
    for index, label in enumerate(HR_ACTIVITY_LEVELS):
        if label in value:
            return index
    if _OLD_ACTIVITY.search(value):
        return len(HR_ACTIVITY_LEVELS)
    return None


def nature_label(job: dict) -> str:
    found = detect_job_types(job)
    ordered = [name for name in ("实习", "校招", "正式", "社招") if name in found]
    return "、".join(ordered) or "未识别"


def parse_resume_graduation_year(text: str) -> int | None:
    """University graduation year written on the resume, such as 2027届 or 2023-2027."""
    if not text:
        return None
    cohorts = [int(year) for year in re.findall(r"(20\d{2})\s*届", text) if 2020 <= int(year) <= 2040]
    if cohorts:
        return max(cohorts)
    best: tuple[int, int] | None = None
    for match in re.finditer(
        r"(20\d{2})\s*[.\-/年]\s*(?:\d{1,2})?\s*[-~～至到—]\s*(20\d{2})",
        text,
    ):
        end = int(match.group(2))
        if not 2020 <= end <= 2040:
            continue
        window = text[max(0, match.start() - 40) : match.end() + 40]
        score = end + (100 if any(token in window for token in ("本科", "大学", "学院", "在读")) else 0)
        if best is None or score > best[0]:
            best = (score, end)
    if best is not None:
        return best[1]
    graduated = re.search(r"(20\d{2})\s*年?\s*(?:0?6|0?7)?\s*月?\s*毕业", text)
    if graduated and 2020 <= int(graduated.group(1)) <= 2040:
        return int(graduated.group(1))
    return None


def candidate_graduation_year(profile: dict) -> int | None:
    parsed = parse_resume_graduation_year(str(profile.get("resume_text") or ""))
    if parsed:
        return parsed
    raw = (profile.get("education") or {}).get("graduation_year")
    try:
        year = int(raw)
    except (TypeError, ValueError):
        return None
    return year if 2020 <= year <= 2040 else None


def has_graduated(profile: dict, year: int | None = None) -> bool | None:
    """False while the candidate is still in university. None when the year is unknown."""
    status = str((profile.get("education") or {}).get("status") or "")
    if "在读" in status or "未毕业" in status:
        return False
    if year is None:
        year = candidate_graduation_year(profile)
    if year is None:
        return None
    today = date.today()
    if year > today.year:
        return False
    if year < today.year:
        return True
    return today.month >= 7


def required_cohorts(text: str) -> set[int] | None:
    """Graduating classes the posting explicitly recruits. Preferences such as 优先 are ignored."""
    found: set[int] = set()
    for match in re.finditer(r"(?<!\d)(20\d{2}|\d{2})\s*届", text or ""):
        year = int(match.group(1))
        if year < 100:
            year += 2000
        if not 2020 <= year <= 2040:
            continue
        before = text[max(0, match.start() - 6) : match.start()]
        after = text[match.end() : match.end() + 6]
        if any(flag in before or flag in after for flag in ("优先", "亦可", "也可", "欢迎")):
            continue
        if "不限" in before:
            continue
        found.add(year)
    return found or None


def offers_pre_graduation_conversion(text: str) -> bool:
    """A formal role can be considered before graduation only with an early internship and conversion."""
    if any(phrase in text for phrase in ("实习转正", "转正实习")):
        return True
    early = any(phrase in text for phrase in ("提前实习", "提前到岗", "offer后实习", "拿到offer后"))
    conversion = "转正" in text
    return early and conversion


def detect_job_types(job: dict) -> set[str]:
    blob = "\n".join(
        [
            str(job.get("title") or ""),
            str(job.get("experience") or ""),
            str(job.get("description") or ""),
            str(job.get("salary") or ""),
        ]
    )
    found: set[str] = set()
    if any(token in blob for token in ("实习", "实习生")):
        found.add("实习")
    if any(token in blob for token in ("校招", "校园招聘", "应届", "校园")):
        found.add("校招")
    if any(token in blob for token in ("社招", "社会招聘")):
        found.add("社招")
    if any(token in blob for token in ("正式", "全职", "正式编制")):
        found.add("正式")
    if not found:
        found.add("正式")
        if any(token in blob for token in ("应届", "经验不限", "在校")):
            found.add("校招")
    return found


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
    low, high = salary_bounds_k(salary)
    if not salary or high is None:
        unknown.append("salary")
    elif rules.get("reject_salary_max_below_min", True):
        floor = profile.get("salary_floor_k")
        if floor is None:
            floor = profile.get("salary_min_k") or 0
        if high < float(floor):
            reasons.append(f"必须拒绝：薪资上限是{salary}，低于保底{int(float(floor))}K")
        elif profile.get("prefer_salary_uncapped", True) is False:
            prefer = float(profile.get("salary_min_k") or 0)
            if prefer > 0 and low is not None and low > prefer:
                reasons.append(
                    f"必须拒绝：薪资下限是{salary}，高于优先薪资{int(prefer)}K。低于或等于优先薪资的会继续考虑"
                )

    title = job.get("title") or ""
    description = job.get("description") or ""
    blocked_titles = matching_terms(title, listed_text(profile.get("blocked_titles")))
    if blocked_titles:
        reasons.append("必须拒绝：岗位名称命中屏蔽：" + "、".join(blocked_titles))
    blocked_keywords = matching_terms(f"{title}\n{description}", listed_text(profile.get("blocked_keywords")))
    if blocked_keywords:
        reasons.append("必须拒绝：职位信息命中屏蔽词：" + "、".join(blocked_keywords))

    minimum_size = int(profile.get("min_company_size") if profile.get("min_company_size") is not None else 20)
    size_text = (job.get("company_size") or "").strip()
    if minimum_size > 0 and size_text and company_smaller_than(size_text, minimum_size):
        reasons.append(f"必须拒绝：公司规模是{size_text}，低于设定的{minimum_size}人")

    allowed_types = set(listed_text(profile.get("job_types")) or ["实习", "正式", "校招", "社招"])
    job_types = detect_job_types(job)
    if allowed_types and not (job_types & allowed_types):
        reasons.append(
            "必须拒绝：岗位属性是"
            + nature_label(job)
            + "，不在已选类型（"
            + "、".join(name for name in ("实习", "校招", "正式", "社招") if name in allowed_types)
            + "）"
        )

    blob = "\n".join([title, experience, description])
    graduation_year = candidate_graduation_year(profile)
    campus_selected = bool(allowed_types & {"实习", "校招"})
    campus_job = bool(job_types & {"实习", "校招"})
    if graduation_year and (campus_selected or campus_job):
        cohorts = required_cohorts(blob)
        if cohorts and graduation_year not in cohorts:
            shown = "、".join(f"{year}届" for year in sorted(cohorts))
            reasons.append(
                f"必须拒绝：岗位明确招聘{shown}，简历是{graduation_year}届，届别不一致"
            )
    graduated = has_graduated(profile, graduation_year)
    if graduated is False and not campus_job and job_types & {"正式", "社招"}:
        if not offers_pre_graduation_conversion(blob):
            year_text = f"{graduation_year}届" if graduation_year else "尚未毕业"
            reasons.append(
                "必须拒绝：岗位属性是"
                + nature_label(job)
                + f"，需要毕业证。简历{year_text}还在读。"
                + "没有毕业证时，只有岗位写明发 offer、提前实习并提供转正才投"
            )

    activity_max = (profile.get("hr_activity_max") or "本月活跃").strip()
    if activity_max in HR_ACTIVITY_LEVELS:
        limit = HR_ACTIVITY_LEVELS.index(activity_max)
        level = hr_activity_level(job.get("hr_activity") or "")
        if level is not None and level > limit:
            shown = (job.get("hr_activity") or "").strip() or "过久"
            reasons.append(f"必须拒绝：HR 活跃状态是{shown}，超过设定的{activity_max}")

    return RuleResult(hard_reject=bool(reasons), reasons=reasons, unknown_fields=unknown)
