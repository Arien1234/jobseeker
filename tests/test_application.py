"""Manual application queue. Nothing in this module submits an application."""

from pathlib import Path

from core.application import cancel_application, check_job, confirm_application, enqueue_recommended
from core.database import Database

PROFILE = {
    "cities": ["广州", "深圳", "东莞"],
    "salary_min_k": 10,
    "hard_rules": {
        "reject_cities_outside": True,
        "reject_degrees": ["硕士", "博士", "研究生", "MBA", "EMBA"],
        "reject_experience_at_least_years": 3,
        "reject_salary_max_below_min": True,
    },
}


def _save(database: Database, **overrides) -> int:
    job = {
        "title": "Python开发工程师",
        "company": "示例科技",
        "salary": "10-12K",
        "location": "深圳 南山",
        "education": "本科",
        "experience": "经验不限",
        "description": "负责 Python 服务开发，包含接口、测试和部署。",
        "url": "https://www.zhaopin.com/jobdetail/CCL300J400.htm",
        "source": "智联招聘",
    }
    job.update(overrides)
    database.upsert_job(job)
    with database.transaction() as conn:
        return conn.execute("SELECT id FROM jobs ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _score(database: Database, job_id: int, score: int = 91):
    database.save_analysis(
        job_id,
        {
            "score": score,
            "recommendation": "优先投递" if score >= 90 else "推荐投递",
            "summary": "匹配",
            "hard_reject": False,
            "raw_json": "{}",
        },
    )


def test_queue_checks_and_confirmation(tmp_path: Path):
    database = Database(tmp_path / "jobs.db")
    ready_id = _save(database)
    _score(database, ready_id, 91)
    incomplete_id = _save(database, url="", title="全栈开发工程师")
    _score(database, incomplete_id, 88)
    result = enqueue_recommended(database, PROFILE)
    assert result["queued"] == 1
    assert result["blocked"] == 1
    again = enqueue_recommended(database, PROFILE)
    assert again["duplicated"] == 2
    rows = database.application_rows()
    pending = next(row for row in rows if row["职位名称"] == "Python开发工程师")
    message = confirm_application(database, int(pending["任务编号"]))
    assert "手动" in message
    confirmed = database.get_application(int(pending["任务编号"]))
    assert confirmed["status"] == "awaiting_manual_submit"
    assert "已投递" not in confirmed["status"]
    blocked = next(row for row in rows if row["职位名称"] == "全栈开发工程师")
    assert blocked["状态"] == "blocked"
    ready = database.get_job(ready_id)
    assert check_job(ready, PROFILE)[0] is True


def test_cancel_does_not_submit(tmp_path: Path):
    database = Database(tmp_path / "jobs.db")
    job_id = _save(database, url="https://www.zhaopin.com/jobdetail/CCL300J401.htm", title="Agent开发工程师")
    _score(database, job_id, 85)
    enqueue_recommended(database, PROFILE)
    application_id = database.application_rows()[0]["任务编号"]
    message = cancel_application(database, int(application_id))
    assert "未提交" in message
    assert database.get_application(int(application_id))["status"] == "cancelled"
