"""Parse Zhaopin card and detail fragments with the inspected class names."""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

from core.html_tree import Node, parse_html
from core.paths import SELECTORS_PATH

JOB_DETAIL_RE = re.compile(r"/jobdetail/([A-Za-z0-9]+)\.htm", re.IGNORECASE)
EDUCATION_MARKERS = ("学历", "初中", "高中", "中专", "中技", "大专", "本科", "硕士", "博士", "mba", "emba")


class ParseError(Exception):
    """Raised when a required visible field cannot be read."""


def load_selectors(path=None) -> dict:
    selector_path = path or SELECTORS_PATH
    data = json.loads(selector_path.read_text(encoding="utf-8"))
    classes = data.get("classes") or {}
    if "card" not in classes or "detail_description" not in classes:
        raise ParseError("selectors.json 缺少已确认的职位结构")
    return data


def clean_inline(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


_HR_ACTIVITY_RANK = (
    "当前在线",
    "刚刚活跃",
    "今日活跃",
    "3日内活跃",
    "本周活跃",
    "本月活跃",
    "在线",
)


def hr_activity_text(*parts: str) -> str:
    """Pick the strongest HR activity label and keep reply volume if present."""
    labels: list[str] = []
    replies = ""
    for part in parts:
        value = clean_inline(part or "")
        if not value:
            continue
        if value.startswith("总回复"):
            replies = value
            continue
        if value not in labels:
            labels.append(value)
    if not labels and not replies:
        return ""
    ranked = [item for item in _HR_ACTIVITY_RANK if item in labels]
    chosen = ranked[0] if ranked else (labels[0] if labels else "")
    if chosen and replies:
        return f"{chosen} · {replies}"
    return chosen or replies


def hr_activity_from_html(html: str, classes: dict | None = None) -> str:
    classes = classes or load_selectors()["classes"]
    tree = parse_html(html)
    parts: list[str] = []
    for key in (
        "panel_publisher_online",
        "panel_publisher_activity",
        "detail_publisher_online",
        "detail_publisher_activity",
    ):
        name = classes.get(key)
        if not name:
            continue
        for node in tree.select(name):
            text = clean_inline(node.text())
            if text:
                parts.append(text)
    return hr_activity_text(*parts)


def company_size_text(text: str) -> str:
    ranges = []
    for match in re.finditer(r"(\d+)\s*[-~～到至]\s*(\d+)\s*人", text or ""):
        low, high = int(match.group(1)), int(match.group(2))
        if high >= 50:
            ranges.append((low, high))
    if ranges:
        low, high = max(ranges, key=lambda item: item[1])
        return f"{low}-{high}人"
    at_least = [int(number) for number in re.findall(r"(\d+)\s*人以上", text or "")]
    if at_least:
        return f"{max(at_least)}人以上"
    return ""


def company_size_from_html(html: str, classes: dict | None = None) -> str:
    """Read the size shown for this job, not sizes from the similar-job list."""
    classes = classes or load_selectors()["classes"]
    tree = parse_html(html)
    for item in tree.select(classes["join_company_item"]):
        label = item.select_one(classes["join_company_label"])
        value = item.select_one(classes["join_company_value"])
        if label is None or value is None or "客户公司人数" not in label.text():
            continue
        size = company_size_text(value.text())
        if size:
            return size
    for item in tree.select(classes["company_summary_item"]):
        if item.select_one(classes["company_summary_scale"]) is None:
            continue
        size = company_size_text(item.text())
        if size:
            return size
    return ""


def clean_block(text: str) -> str:
    normalized = (text or "").replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in normalized.split("\n")]
    compact: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank and compact:
                compact.append("")
            blank = True
            continue
        compact.append(line)
        blank = False
    return "\n".join(compact).strip()


def classify_meta(text: str) -> str:
    value = clean_inline(text)
    lowered = value.lower()
    if any(marker in lowered for marker in EDUCATION_MARKERS):
        return "education"
    if "经验" in value or "应届" in value or "在校" in value or re.search(r"\d+\s*年", value):
        return "experience"
    return "location"


def split_meta(items: list[str]) -> dict[str, str]:
    result = {"location": "", "experience": "", "education": ""}
    for item in items:
        kind = classify_meta(item)
        if not result[kind]:
            result[kind] = item
    return result


def canonical_job_url(url: str) -> str:
    if not url or url.startswith("javascript:"):
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://www.zhaopin.com" + url
    match = JOB_DETAIL_RE.search(urlsplit(url).path)
    if not match:
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return ""
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return f"https://www.zhaopin.com/jobdetail/{match.group(1)}.htm"


def dedup_key_for(job: dict) -> str:
    canonical = canonical_job_url(job.get("url") or "")
    if "/jobdetail/" in canonical:
        return "url:" + canonical
    if canonical:
        return "url:" + canonical
    title = clean_inline(job.get("title") or "")
    company = clean_inline(job.get("company") or "")
    location = clean_inline(job.get("location") or "")
    digest = hashlib.sha256(f"{title}|{company}|{location}".encode("utf-8")).hexdigest()
    return "combo:" + digest


def combo_key_for(job: dict) -> str:
    title = clean_inline(job.get("title") or "")
    company = clean_inline(job.get("company") or "")
    location = clean_inline(job.get("location") or "")
    digest = hashlib.sha256(f"{title}|{company}|{location}".encode("utf-8")).hexdigest()
    return "combo:" + digest


def card_signature(job: dict) -> str:
    return "|".join(clean_inline(job.get(key) or "") for key in ("title", "company", "location", "salary"))


def _node_label(node: Node | None) -> str:
    if node is None:
        return ""
    return clean_inline(node.attrs.get("aria-label") or node.attrs.get("title") or node.text())


def _section_body(title_node: Node, body_class: str) -> str:
    current = title_node.parent
    while current is not None:
        body = current.select_one(body_class)
        if body is not None and body is not title_node:
            body_text = clean_block(body.text())
            card_text = clean_block(current.text())
            title_text = clean_inline(title_node.text())
            if title_text and card_text.startswith(title_text):
                card_text = card_text[len(title_text) :].strip()
            return card_text if len(card_text) >= len(body_text) else body_text
        current = current.parent
    return ""


def compose_description(*parts: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for line in clean_block(part).splitlines():
            key = clean_inline(line)
            if key and key not in seen:
                seen.add(key)
                lines.append(line)
    return "\n".join(lines).strip()


def parse_card_html(html: str, classes: dict | None = None) -> dict:
    classes = classes or load_selectors()["classes"]
    tree = parse_html(html)
    title = _node_label(tree.select_one(classes["card_title"]))
    if not title:
        raise ParseError("职位解析失败: 未找到职位名称")
    salary = _node_label(tree.select_one(classes["card_salary"]))
    company = _node_label(tree.select_one(classes["card_company"]))
    location = _node_label(tree.select_one(classes["card_location"]))
    return {
        "title": title,
        "company": company,
        "salary": salary,
        "location": location,
        "education": "",
        "experience": "",
        "description": "",
        "url": "",
    }


def parse_panel_html(html: str, classes: dict | None = None) -> dict:
    classes = classes or load_selectors()["classes"]
    tree = parse_html(html)
    title = _node_label(tree.select_one(classes["panel_title"]))
    salary = _node_label(tree.select_one(classes["panel_salary"]))
    tags = [clean_inline(node.text()) for node in tree.select(classes["panel_tag"])]
    tags = [tag for tag in tags if tag]
    meta = split_meta(tags)
    description = ""
    for title_node in tree.select(classes["panel_section_title"]):
        if clean_inline(title_node.text()) == "职位描述":
            description = _section_body(title_node, classes["panel_section_body"])
            break
    link = tree.select_one(classes["detail_link"])
    href = link.attrs.get("href", "") if link else ""
    return {
        "title": title,
        "salary": salary,
        "company": "",
        "location": meta["location"],
        "education": meta["education"],
        "experience": meta["experience"],
        "description": description,
        "hr_activity": hr_activity_from_html(html, classes),
        "url": canonical_job_url(href),
    }


def parse_detail_html(html: str, classes: dict | None = None) -> dict:
    classes = classes or load_selectors()["classes"]
    tree = parse_html(html)
    title = _node_label(tree.select_one(classes["detail_title"]))
    if not title:
        raise ParseError("职位解析失败: 详情页没有职位名称")
    salary = _node_label(tree.select_one(classes["detail_salary"]))
    company = _node_label(tree.select_one(classes["detail_company"]))
    meta_root = tree.select_one(classes["detail_meta"])
    items: list[str] = []
    if meta_root is not None:
        for item in meta_root.by_tag("li"):
            if classes["detail_client_company"] in item.classes:
                continue
            text = clean_inline(item.text())
            if text:
                items.append(text)
    meta = split_meta(items)
    skills = [clean_inline(node.text()) for node in tree.select(classes["detail_skill"])]
    skills = [skill for skill in skills if skill]
    body_node = tree.select_one(classes["detail_description"])
    body = clean_block(body_node.text() if body_node else "")
    return {
        "title": title,
        "company": company,
        "salary": salary,
        "location": meta["location"],
        "education": meta["education"],
        "experience": meta["experience"],
        "description": compose_description("\n".join(skills), body),
        "hr_activity": hr_activity_from_html(html, classes),
        "url": "",
    }
