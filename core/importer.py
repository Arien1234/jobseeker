"""Import jobs from a CSV file into the existing jobs table."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.database import Database, now_text
from core.models import SOURCE_NAME

COLUMN_MAP = {
    "title": "title",
    "职位名称": "title",
    "岗位": "title",
    "company": "company",
    "公司": "company",
    "公司名称": "company",
    "salary": "salary",
    "薪资": "salary",
    "薪资范围": "salary",
    "location": "location",
    "地点": "location",
    "工作地点": "location",
    "education": "education",
    "学历": "education",
    "学历要求": "education",
    "experience": "experience",
    "经验": "experience",
    "工作经验": "experience",
    "description": "description",
    "描述": "description",
    "职位描述": "description",
    "jd": "description",
    "url": "url",
    "链接": "url",
    "职位链接": "url",
    "source": "source",
    "来源": "source",
}


def import_csv(database: Database, path: Path) -> dict:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    renamed = {}
    for column in frame.columns:
        key = COLUMN_MAP.get(str(column).strip())
        if key:
            renamed[column] = key
    frame = frame.rename(columns=renamed)
    inserted = 0
    duplicated = 0
    failed = 0
    for record in frame.to_dict(orient="records"):
        job = {field: str(record.get(field) or "").strip() for field in (
            "title", "company", "salary", "location", "education", "experience", "description", "url", "source"
        )}
        if not job["source"]:
            job["source"] = "CSV导入" if SOURCE_NAME not in str(path) else SOURCE_NAME
        job["collected_at"] = now_text()
        try:
            outcome = database.upsert_job(job)
        except Exception:
            failed += 1
            continue
        if outcome == "inserted":
            inserted += 1
        else:
            duplicated += 1
    return {"inserted": inserted, "duplicated": duplicated, "failed": failed}
