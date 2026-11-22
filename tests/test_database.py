"""Database initialization, migration, and CSV import."""

import sqlite3
from pathlib import Path

import pymupdf

from core.database import Database
from core.importer import import_csv
from core.resume_parser import extract_resume_text


def test_initialize_creates_tables(tmp_path: Path):
    database = Database(tmp_path / "jobs.db")
    with database.transaction() as conn:
        names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "applications", "collect_failures", "resume_document"} <= names
    assert database.count_jobs() == 0


def test_pending_jobs_skip_finished(tmp_path: Path):
    database = Database(tmp_path / "jobs.db")
    database.upsert_job(
        {
            "title": "Python开发",
            "company": "示例公司",
            "location": "深圳",
            "description": "负责 Python 接口",
            "url": "https://www.zhaopin.com/jobdetail/CCL100J201.htm",
        }
    )
    assert "url:https://www.zhaopin.com/jobdetail/CCL100J201.htm" in database.identity_keys()
    assert len(database.jobs_pending_apply()) == 1
    job_id = database.find_job_id({"title": "Python开发", "company": "示例公司", "url": "https://www.zhaopin.com/jobdetail/CCL100J201.htm"})
    database.record_application(job_id, "skipped", "分数不够")
    assert database.jobs_pending_apply() == []


def test_migration_keeps_existing_rows(tmp_path: Path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, title TEXT, dedup_key TEXT)")
    conn.execute("INSERT INTO jobs (title, dedup_key) VALUES ('旧职位', 'url:https://example.com/job')")
    conn.commit()
    conn.close()

    database = Database(path)
    with database.transaction() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
        row = connection.execute("SELECT * FROM jobs").fetchone()
    assert "ai_score" in columns
    assert "description" in columns
    assert row["title"] == "旧职位"
    assert row["ai_score"] is None


def test_csv_import_and_resume_pdf(tmp_path: Path):
    database = Database(tmp_path / "jobs.db")
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text(
        "职位名称,公司,薪资,地点,学历,工作经验,职位描述,链接\n"
        "Python开发,甲公司,10-12K,深圳,本科,经验不限,负责接口开发,https://www.zhaopin.com/jobdetail/CC1J2.htm\n",
        encoding="utf-8",
    )
    stats = import_csv(database, csv_path)
    assert stats["inserted"] == 1
    again = import_csv(database, csv_path)
    assert again["duplicated"] == 1
    assert database.count_jobs() == 1

    font = Path(r"C:\Windows\Fonts\simhei.ttf")
    document = pymupdf.open()
    page = document.new_page()
    if font.exists():
        page.insert_text((72, 72), "校园项目：问答助手", fontfile=str(font), fontname="hei")
        expected = "问答助手"
    else:
        page.insert_text((72, 72), "Campus QA assistant")
        expected = "Campus QA assistant"
    resume_path = tmp_path / "resume.pdf"
    document.save(resume_path)
    document.close()
    text = extract_resume_text(resume_path)
    assert expected in text
    database.save_resume(resume_path.name, text)
    assert expected in database.resume_text()
