"""Extract resume text and remove contact details before anything is stored."""

from __future__ import annotations

import re
from pathlib import Path

_EMAIL = re.compile(r"[\w.+\-]+@[\w.\-]+\.\w+")
_PHONE = re.compile(r"1[3-9]\d{9}")
_GITHUB = re.compile(r"https?://github\.com/\S+", re.IGNORECASE)
_CONTACT_LINE = re.compile(r"手机号|邮箱|微信|E-mail|email|电话", re.IGNORECASE)


def redact_personal_info(text: str) -> str:
    lines = (text or "").splitlines()
    kept: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if _CONTACT_LINE.search(stripped):
            continue
        nxt = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if _CONTACT_LINE.search(nxt) and 1 <= len(stripped) <= 4:
            continue
        cleaned = _GITHUB.sub("", _EMAIL.sub("", _PHONE.sub("", line)))
        kept.append(cleaned)
    return "\n".join(kept).strip()


def extract_resume_text(path: Path) -> str:
    import pymupdf

    document = pymupdf.open(path)
    try:
        pages = [page.get_text() or "" for page in document]
    finally:
        document.close()
    return "\n".join(pages).strip()
