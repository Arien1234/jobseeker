"""Job identity and preservation of an existing AI score."""

from pathlib import Path

from core.database import Database
from core.job_parser import dedup_key_for


def _job(url: str = "", description: str = "负责 Python 接口开发") -> dict:
    return {
        "title": "Python开发工程师",
        "company": "示例科技",
        "salary": "10-12K",
        "location": "深圳 南山",
        "education": "本科",
        "experience": "经验不限",
        "description": description,
        "url": url,
        "source": "智联招聘",
    }


def _analysis() -> dict:
    return {
        "score": 88,
        "recommendation": "推荐投递",
        "summary": "技能匹配",
        "hard_reject": False,
        "raw_json": '{"score": 88}',
    }


def test_same_url_is_one_row_and_keeps_score(tmp_path: Path):
    database = Database(tmp_path / "jobs.db")
    url = "https://www.zhaopin.com/jobdetail/CCL100J200.htm?srccode=1"
    assert database.upsert_job(_job(url, "短描述")) == "inserted"
    with database.transaction() as conn:
        job_id = conn.execute("SELECT id FROM jobs").fetchone()["id"]
    database.save_analysis(job_id, _analysis())
    assert database.upsert_job(_job(url.split("?")[0], "这是一段更完整的职位描述，包含职责和任职要求")) == "duplicate"
    saved = database.get_job(job_id)
    assert database.count_jobs() == 1
    assert saved["ai_score"] == 88
    assert saved["ai_recommendation"] == "推荐投递"
    assert "更完整" in saved["description"]
    assert dedup_key_for(_job(url)) == dedup_key_for(_job(url.split("?")[0]))


def test_title_company_location_dedup_without_url(tmp_path: Path):
    database = Database(tmp_path / "jobs.db")
    first = _job("")
    second = _job("")
    second["salary"] = "11-12K"
    assert database.upsert_job(first) == "inserted"
    assert database.upsert_job(second) == "duplicate"
    assert database.count_jobs() == 1


def test_later_url_updates_the_same_combo_row(tmp_path: Path):
    database = Database(tmp_path / "jobs.db")
    assert database.upsert_job(_job("")) == "inserted"
    with database.transaction() as conn:
        job_id = conn.execute("SELECT id FROM jobs").fetchone()["id"]
    database.save_analysis(job_id, _analysis())
    assert database.upsert_job(_job("https://www.zhaopin.com/jobdetail/CCL100J201.htm")) == "duplicate"
    saved = database.get_job(job_id)
    assert database.count_jobs() == 1
    assert saved["url"].endswith("/jobdetail/CCL100J201.htm")
    assert saved["ai_score"] == 88


def test_different_urls_stay_separate(tmp_path: Path):
    database = Database(tmp_path / "jobs.db")
    first = _job("https://www.zhaopin.com/jobdetail/CCL100J201.htm")
    second = _job("https://www.zhaopin.com/jobdetail/CCL100J202.htm")
    second["title"] = "Agent开发工程师"
    assert database.upsert_job(first) == "inserted"
    assert database.upsert_job(second) == "inserted"
    assert database.count_jobs() == 2


def test_empty_title_is_rejected(tmp_path: Path):
    database = Database(tmp_path / "jobs.db")
    job = _job("https://www.zhaopin.com/jobdetail/CCL100J203.htm")
    job["title"] = " "
    try:
        database.upsert_job(job)
    except ValueError:
        rejected = True
    else:
        rejected = False
    assert rejected
    assert database.count_jobs() == 0
