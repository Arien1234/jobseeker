"""Local Ollama matching. Scores are stored only after a real response or a local hard rule."""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request

from core.defaults import DEFAULT_MODEL
from core.job_parser import clean_inline
from core.logging_config import get_logger
from core.models import RECOMMENDATIONS, recommendation_for_score
from core.rules import (
    candidate_graduation_year,
    evaluate_rules,
    has_graduated,
    listed_text,
    matching_terms,
    nature_label,
    salary_bounds_k,
)

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
只有下面几条才允许 hard_reject，summary 必须写成「必须拒绝：」加具体原因：城市不在已选城市、学历要求硕士或博士或研究生、经验年限达到排除线、薪资上限低于保底、薪资下限高于已设的优先上限、岗位名称命中屏蔽、描述命中屏蔽词、公司规模写明了并且上限低于用户设定的人数、岗位届别和候选人毕业年份不一致、尚未毕业却去投没有提前实习转正的正式或社招。
薪资先看保底：只有薪资上限低于保底才因太低而拒绝。低于优先薪资、但上限仍不低于保底的，要继续考虑，不得因为没到优先线就拒绝。prefer_salary_uncapped 为 false 时，薪资下限高于 salary_min_k 必须拒绝。prefer_salary_uncapped 为 true 时，薪资不设上限，越高越好，不得因为薪资高而拒绝。
工作地点只看职位地点。客户公司地址、公司总部地址不能当成工作地点。发布方是人力资源或人才经纪公司时，只要职位地点和内容合适，不要因此拒绝。
公司规模看页面写明的人数。客户公司人数优先，没有客户公司人数时看公司信息里的规模。达到用户设定的人数是加分。规模没写不要拒绝，也不要因此降到 80 分以下。
HR 活跃状态看页面写明的标签。比用户设定的活跃下限更旧的，由本地规则硬拒绝，你不要再 hard_reject。当前在线或刚刚活跃可在总结里加一句加分。活跃没写不要因此拒绝。
每个岗位都要先判断属性，summary 开头写「岗位属性：实习、校招、正式、社招」中的对应项，可以同时有多项。
实习：在校就能投，不需要毕业证。校招：面向应届的某一届，毕业前的秋招、春招就可以投，入职一般在拿到毕业证之后。岗位写明只招某一届时，必须和候选人的大学毕业年份一致，例如只招 26 届而候选人是 27 届，必须 hard_reject。写了「优先」或「也可」的届别不是硬性限制。
正式和社招：要有毕业证才能入职。候选人尚未毕业时，只有岗位写明可以发 offer、提前实习并提供转正，才可以接受，并在 summary 里写明这一点；否则 hard_reject。候选人毕业年份看 education.graduation_year，再对照简历原文里的大学起止时间或届别。
如果不是必须拒绝，summary 写清简历哪一段对上了职位描述的哪一项，以及可以接受的小瑕疵。
只输出一个 JSON 对象，不要 Markdown。字段为 score、recommendation、hard_reject、hard_reject_reasons、matched_skills、missing_requirements、unknown_fields、summary。summary 用中文，不超过 180 字。"""


class AIError(Exception):
    """The model could not be asked or its answer could not be used."""


class AIInterrupted(Exception):
    """The user stopped while the model was still working."""


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


def _json_objects(text: str) -> list[str]:
    value = strip_fence(text)
    found: list[str] = []
    if value:
        found.append(value)
    start = value.find("{")
    if start < 0:
        return found
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(value[start:], start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                found.append(value[start : index + 1])
                break
    end = value.rfind("}")
    if end > start:
        found.append(value[start : end + 1])
    return found


def _load_object(text: str) -> dict | None:
    repaired = re.sub(r",\s*([}\]])", r"\1", text)
    for item in (text, repaired):
        try:
            data = json.loads(item)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def parse_ai_payload(text: str) -> dict:
    data = None
    for candidate in _json_objects(text):
        data = _load_object(candidate)
        if data is not None and "score" in data:
            break
    else:
        data = None
    if not isinstance(data, dict) or "score" not in data or isinstance(data.get("score"), bool):
        raise AIJsonError("JSON 解析失败")
    try:
        score = int(float(data["score"]))
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
        return _stamp_nature(result, job)
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
        return _stamp_nature(result, job)
    _, high = salary_bounds_k(job.get("salary") or "")
    if high is None:
        return _stamp_nature(result, job)
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
    return _stamp_nature(result, job)


def _stamp_nature(result: dict, job: dict) -> dict:
    prefix = f"岗位属性：{nature_label(job)}。"
    summary = clean_inline(str(result.get("summary") or ""))
    if not summary.startswith("岗位属性"):
        summary = clean_inline(prefix + summary)
    result["summary"] = summary[:180]
    return result


def _profile_with_resume(profile: dict, resume_text: str) -> dict:
    if not resume_text:
        return profile
    copied = dict(profile)
    copied["resume_text"] = resume_text
    return copied


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
        "salary_policy": (
            "salary_floor_k 是保底，只有薪资上限低于它才因太低拒绝。低于 salary_min_k 仍要考虑。"
            + (
                "优先薪资不设上限，越高越好。"
                if profile.get("prefer_salary_uncapped", True)
                else "prefer_salary_uncapped 为 false：薪资下限高于 salary_min_k 必须拒绝。"
            )
        ),
        "education": profile.get("education") or {},
        "graduation_year": candidate_graduation_year(profile),
        "graduated": has_graduated(profile),
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


def _close_socket(box: dict) -> None:
    sock = box.get("sock")
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    try:
        sock.close()
    except Exception:
        pass


class _TrackedConnection(http.client.HTTPConnection):
    def __init__(self, host, port=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, *, box=None, **kwargs):
        super().__init__(host, port, timeout=timeout, **kwargs)
        self._box = box

    def connect(self):
        super().connect()
        if self._box is not None:
            self._box["sock"] = self.sock
            if self._box.get("cancel"):
                _close_socket(self._box)


class _CaptureHandler(urllib.request.HTTPHandler):
    def __init__(self, box: dict):
        super().__init__()
        self._box = box

    def http_open(self, req):
        def factory(host, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, **kwargs):
            return _TrackedConnection(host, timeout=timeout, box=self._box, **kwargs)

        return self.do_open(factory, req)


def _run_interruptible(worker, stop_event, timeout: int):
    """Run worker(box) until it finishes, the deadline passes, or the user stops."""
    box = {"cancel": False, "done": False}
    def run() -> None:
        try:
            box["value"] = worker(box)
        except Exception as exc:
            box["error"] = exc
        finally:
            box["done"] = True

    threading.Thread(target=run, name="ollama-call", daemon=True).start()
    deadline = time.time() + timeout
    while not box["done"]:
        if stop_event is not None and stop_event.is_set():
            box["cancel"] = True
            _close_socket(box)
            raise AIInterrupted("已停止，模型这次不等了")
        if time.time() >= deadline:
            box["cancel"] = True
            _close_socket(box)
            raise TimeoutError("Ollama 超时")
        time.sleep(0.2)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def resolve_model_settings(profile: dict | None = None) -> dict:
    """Saved model settings win. Missing fields fall back to the built-in default, then environment."""
    saved = {}
    if isinstance(profile, dict) and isinstance(profile.get("model"), dict):
        saved = profile["model"]
    merged = dict(DEFAULT_MODEL)
    if not str(saved.get("host") or "").strip():
        merged["host"] = OLLAMA_URL
    if not str(saved.get("name") or "").strip():
        merged["name"] = MODEL
    for key, value in saved.items():
        if key in merged and value is not None and value != "":
            merged[key] = value
    return normalize_model_settings(
        merged["host"],
        merged["name"],
        merged["temperature"],
        merged["num_predict"],
        merged["timeout_seconds"],
        merged["disable_think"],
        merged["keep_alive"],
    )


def normalize_model_settings(host, name, temperature, num_predict, timeout_seconds, disable_think, keep_alive) -> dict:
    address = str(host or "").strip().rstrip("/")
    if not address.startswith(("http://", "https://")):
        raise ValueError("服务地址要以 http:// 或 https:// 开头")
    model_name = str(name or "").strip()
    if not model_name:
        raise ValueError("请填写模型名称")
    try:
        heat = float(temperature)
    except (TypeError, ValueError) as exc:
        raise ValueError("温度要是数字") from exc
    if not 0 <= heat <= 2:
        raise ValueError("温度要在 0 到 2 之间")
    try:
        predict = int(num_predict)
        wait = int(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("生成长度和超时要是整数") from exc
    if not 32 <= predict <= 4096:
        raise ValueError("单次最多生成 token 要在 32 到 4096 之间")
    if not 20 <= wait <= 600:
        raise ValueError("等待超时要在 20 到 600 秒之间")
    alive = str(keep_alive or "").strip() or "30m"
    return {
        "host": address,
        "name": model_name,
        "temperature": round(heat, 2),
        "num_predict": predict,
        "timeout_seconds": wait,
        "disable_think": bool(disable_think),
        "keep_alive": alive,
    }


def list_model_names(host: str) -> list[str]:
    address = str(host or "").strip().rstrip("/")
    request = urllib.request.Request(f"{address}/api/tags")
    with urllib.request.urlopen(request, timeout=8) as response:
        data = json.loads(response.read().decode("utf-8"))
    names = []
    for item in data.get("models") or []:
        model_name = str(item.get("name") or item.get("model") or "").strip()
        if model_name and model_name not in names:
            names.append(model_name)
    return names


def probe_model(settings: dict) -> str:
    """Ask the configured model for a tiny JSON reply."""
    payload = {
        "model": settings["name"],
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": "只输出 JSON：{\"ok\": true}"},
            {"role": "user", "content": "回复 ok"},
        ],
        "options": {"temperature": 0, "num_predict": 32},
        "keep_alive": settings["keep_alive"],
    }
    if settings.get("disable_think"):
        payload["think"] = False
    request = urllib.request.Request(
        f"{settings['host']}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    wait = min(int(settings["timeout_seconds"]), 90)
    try:
        with urllib.request.urlopen(request, timeout=wait) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if settings.get("disable_think") and exc.code == 400:
            payload.pop("think", None)
            request = urllib.request.Request(
                f"{settings['host']}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=wait) as response:
                    body = json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                return f"连不上 {settings['host']} 的 {settings['name']}：{type(exc).__name__}"
        else:
            return f"连不上 {settings['name']}：HTTP {exc.code}"
    except Exception as exc:
        return f"连不上 {settings['host']} 的 {settings['name']}：{type(exc).__name__}"
    content = str(((body.get("message") or {}).get("content") or "")).strip()
    if not content:
        return f"{settings['name']} 没有返回内容"
    return f"已连上 {settings['name']}。保存后，之后的筛选会用这个模型。"


def _post_chat(
    user_message: str,
    *,
    settings: dict,
    include_think: bool,
    stop_event=None,
) -> str:
    payload = {
        "model": settings["name"],
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "options": {
            "temperature": settings["temperature"],
            "num_predict": settings["num_predict"],
        },
        "keep_alive": settings["keep_alive"],
    }
    if include_think and settings.get("disable_think"):
        payload["think"] = False
    request = urllib.request.Request(
        f"{settings['host']}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    def worker(box: dict) -> str:
        opener = urllib.request.build_opener(_CaptureHandler(box))
        with opener.open(request, timeout=settings["timeout_seconds"]) as response:
            return response.read().decode("utf-8")

    raw = _run_interruptible(worker, stop_event, settings["timeout_seconds"])
    try:
        data = json.loads(raw)
        content = data["message"]["content"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AIJsonError("JSON 解析失败") from exc
    text = str(content or "").strip()
    if not text:
        raise AIError("Ollama 调用失败: 空回复")
    return text


def _pause_for_retry(seconds: float, stop_event) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            raise AIInterrupted("已停止，模型这次不等了")
        time.sleep(0.2)


def call_ollama(user_message: str, timeout: int = 140, stop_event=None, settings: dict | None = None) -> str:
    """Ask the local model. HTTP 500 and empty replies are retried; stop returns immediately."""
    active = dict(settings or resolve_model_settings(None))
    if settings is None:
        active["timeout_seconds"] = timeout
    last_error: Exception | None = None
    allow_think_flag = True
    for attempt in range(3):
        if stop_event is not None and stop_event.is_set():
            raise AIInterrupted("已停止，模型这次不等了")
        if attempt:
            _pause_for_retry(2 * attempt, stop_event)
        try:
            return _post_chat(
                user_message,
                settings=active,
                include_think=allow_think_flag,
                stop_event=stop_event,
            )
        except AIInterrupted:
            raise
        except urllib.error.HTTPError as exc:
            last_error = exc
            if allow_think_flag and exc.code == 400:
                allow_think_flag = False
                try:
                    return _post_chat(
                        user_message,
                        settings=active,
                        include_think=False,
                        stop_event=stop_event,
                    )
                except AIInterrupted:
                    raise
                except urllib.error.HTTPError as again:
                    exc = again
                    last_error = again
            if exc.code in (500, 502, 503) and attempt < 2:
                logger.warning("Ollama HTTP %s，准备重试", exc.code)
                continue
            logger.error("Ollama 调用失败: HTTP %s", exc.code)
            raise AIError(f"Ollama 调用失败: HTTP {exc.code}") from exc
        except AIError as exc:
            last_error = exc
            if attempt < 2:
                logger.warning("Ollama 回复不可用，准备重试")
                continue
            logger.error("Ollama 调用失败: %s", exc)
            raise
        except TimeoutError as exc:
            logger.warning("Ollama 超时，先跳过这个岗位")
            raise AIError("Ollama 调用失败: 超时") from exc
        except Exception as exc:
            last_error = exc
            if stop_event is not None and stop_event.is_set():
                raise AIInterrupted("已停止，模型这次不等了") from exc
            if attempt < 2:
                logger.warning("Ollama 调用失败: %s，准备重试", type(exc).__name__)
                continue
            logger.error("Ollama 调用失败: %s", type(exc).__name__)
            raise AIError("Ollama 调用失败") from exc
    logger.error("Ollama 调用失败")
    raise AIError("Ollama 调用失败") from last_error


def analyze_job(job: dict, profile: dict, resume_text: str, api_key: str | None = None, stop_event=None) -> dict:
    profile = _profile_with_resume(profile, resume_text)
    rejected = local_rejection(job, profile)
    if rejected is not None:
        return rejected
    decision = evaluate_rules(job, profile)
    message = build_user_message(job, profile, resume_text)
    settings = resolve_model_settings(profile)
    content = ""
    payload = None
    for attempt in range(2):
        content = call_ollama(message, stop_event=stop_event, settings=settings)
        try:
            payload = parse_ai_payload(content)
            break
        except AIJsonError:
            if attempt == 0:
                logger.warning("JSON 解析失败，再请求一次")
                continue
            logger.error("JSON 解析失败: %s", clean_inline(content)[:80])
            raise AIJsonError("模型没有返回完整 JSON")
    if payload is None:
        raise AIJsonError("模型没有返回完整 JSON")
    result = apply_policy(payload, job, profile, decision.unknown_fields)
    result["source"] = settings["name"]
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
            result = analyze_job(job, profile, resume_text, stop_event=stop_event)
            database.save_analysis(job["id"], result)
            done += 1
            if on_log:
                on_log(f"已分析 {label}：{result['score']} {result['recommendation']}")
        except AIInterrupted:
            break
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
