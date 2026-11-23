"""SQLite storage with additive migrations and transactional writes."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from core.job_parser import canonical_job_url, combo_key_for, dedup_key_for
from core.logging_config import get_logger
from core.paths import DATABASE_PATH

logger = get_logger("database")


def application_can_retry(status: str, reason: str) -> bool:
    """Model outages are not a real apply result, so the next screening pass tries again."""
    if status != "failed":
        return False
    text = reason or ""
    return text.startswith("模型") or text.startswith("Ollama") or "JSON" in text

JOB_COLUMNS = {
    "title": "TEXT NOT NULL DEFAULT ''",
    "company": "TEXT NOT NULL DEFAULT ''",
    "salary": "TEXT NOT NULL DEFAULT ''",
    "location": "TEXT NOT NULL DEFAULT ''",
    "education": "TEXT NOT NULL DEFAULT ''",
    "experience": "TEXT NOT NULL DEFAULT ''",
    "description": "TEXT NOT NULL DEFAULT ''",
    "url": "TEXT NOT NULL DEFAULT ''",
    "source": "TEXT NOT NULL DEFAULT ''",
    "collected_at": "TEXT NOT NULL DEFAULT ''",
    "dedup_key": "TEXT",
    "ai_score": "INTEGER",
    "ai_recommendation": "TEXT",
    "ai_summary": "TEXT",
    "ai_hard_reject": "INTEGER NOT NULL DEFAULT 0",
    "ai_raw_json": "TEXT",
    "ai_analyzed_at": "TEXT",
    "status": "TEXT NOT NULL DEFAULT 'collected'",
    "company_size": "TEXT NOT NULL DEFAULT ''",
    "hr_activity": "TEXT NOT NULL DEFAULT ''",
    "prior_apply": "TEXT NOT NULL DEFAULT ''",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
}


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or DATABASE_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def transaction(self):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def initialize(self) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL DEFAULT '',
                    company TEXT NOT NULL DEFAULT '',
                    salary TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    education TEXT NOT NULL DEFAULT '',
                    experience TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    collected_at TEXT NOT NULL DEFAULT '',
                    dedup_key TEXT,
                    ai_score INTEGER,
                    ai_recommendation TEXT,
                    ai_summary TEXT,
                    ai_hard_reject INTEGER NOT NULL DEFAULT 0,
                    ai_raw_json TEXT,
                    ai_analyzed_at TEXT,
                    status TEXT NOT NULL DEFAULT 'collected',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
            for name, declaration in JOB_COLUMNS.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            conn.execute(
                """
                UPDATE jobs
                SET dedup_key = 'legacy:' || id
                WHERE dedup_key IS NULL OR dedup_key = ''
                """
            )
            rows = conn.execute("SELECT id, dedup_key FROM jobs ORDER BY id").fetchall()
            seen: set[str] = set()
            for row in rows:
                key = row["dedup_key"]
                if key in seen:
                    conn.execute(
                        "UPDATE jobs SET dedup_key = ? WHERE id = ?",
                        (f"{key}#dup{row['id']}", row["id"]),
                    )
                else:
                    seen.add(key)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dedup ON jobs(dedup_key)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS collect_failures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    company TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS resume_document (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    filename TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
                """
            )

    def upsert_job(self, job: dict) -> str:
        title = (job.get("title") or "").strip()
        if not title:
            raise ValueError("职位缺少名称，已拒绝写入")
        url_key = dedup_key_for(job)
        combo_key = combo_key_for(job)
        timestamp = job.get("collected_at") or now_text()
        try:
            with self.transaction() as conn:
                existing = None
                if url_key.startswith("url:"):
                    existing = conn.execute("SELECT * FROM jobs WHERE dedup_key = ?", (url_key,)).fetchone()
                if existing is None:
                    existing = conn.execute("SELECT * FROM jobs WHERE dedup_key = ?", (combo_key,)).fetchone()
                if existing is not None:
                    description = existing["description"] or ""
                    incoming = job.get("description") or ""
                    if incoming and len(incoming) >= len(description):
                        description = incoming
                    stored_key = existing["dedup_key"]
                    if url_key.startswith("url:") and stored_key != url_key:
                        taken = conn.execute("SELECT id FROM jobs WHERE dedup_key = ?", (url_key,)).fetchone()
                        if taken is None:
                            stored_key = url_key
                    prior = (job.get("prior_apply") or "").strip() or (existing["prior_apply"] or "")
                    conn.execute(
                        """
                        UPDATE jobs
                        SET title = ?, company = ?, salary = ?, location = ?, education = ?,
                            experience = ?, description = ?, company_size = ?, hr_activity = ?,
                            prior_apply = ?, url = ?, source = ?, collected_at = ?, dedup_key = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            title,
                            job.get("company") or existing["company"],
                            job.get("salary") or existing["salary"],
                            job.get("location") or existing["location"],
                            job.get("education") or existing["education"],
                            job.get("experience") or existing["experience"],
                            description,
                            job.get("company_size") or existing["company_size"] or "",
                            job.get("hr_activity") or existing["hr_activity"] or "",
                            prior,
                            canonical_job_url(job.get("url") or "") or existing["url"],
                            job.get("source") or existing["source"],
                            timestamp,
                            stored_key,
                            now_text(),
                            existing["id"],
                        ),
                    )
                    return "duplicate"
                conn.execute(
                    """
                    INSERT INTO jobs (
                        title, company, salary, location, education, experience, description,
                        company_size, hr_activity, prior_apply, url, source, collected_at, dedup_key, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'collected', ?, ?)
                    """,
                    (
                        title,
                        job.get("company") or "",
                        job.get("salary") or "",
                        job.get("location") or "",
                        job.get("education") or "",
                        job.get("experience") or "",
                        job.get("description") or "",
                        job.get("company_size") or "",
                        job.get("hr_activity") or "",
                        (job.get("prior_apply") or "").strip(),
                        canonical_job_url(job.get("url") or ""),
                        job.get("source") or "",
                        timestamp,
                        url_key if url_key.startswith("url:") else combo_key,
                        timestamp,
                        timestamp,
                    ),
                )
                return "inserted"
        except ValueError:
            raise
        except Exception:
            logger.error("数据库保存失败")
            raise

    def job_exists(self, job: dict) -> bool:
        url_key = dedup_key_for(job)
        combo_key = combo_key_for(job)
        title = (job.get("title") or "").strip()
        company = (job.get("company") or "").strip()
        location = (job.get("location") or "").strip()
        with self.transaction() as conn:
            if url_key.startswith("url:"):
                found = conn.execute("SELECT 1 FROM jobs WHERE dedup_key = ?", (url_key,)).fetchone()
                if found is not None:
                    return True
            found = conn.execute("SELECT 1 FROM jobs WHERE dedup_key = ?", (combo_key,)).fetchone()
            if found is not None:
                return True
            if title and company:
                found = conn.execute(
                    "SELECT 1 FROM jobs WHERE title = ? AND company = ? AND location = ?",
                    (title, company, location),
                ).fetchone()
        return found is not None

    def identity_keys(self) -> set[str]:
        """Keys already stored, so collection can skip known cards without a query each time."""
        found: set[str] = set()
        with self.transaction() as conn:
            rows = conn.execute("SELECT dedup_key, title, company, location FROM jobs").fetchall()
        for row in rows:
            key = row["dedup_key"] or ""
            if key:
                found.add(key)
            found.add(combo_key_for(dict(row)))
        return found

    def jobs_pending_apply(self) -> list[dict]:
        with self.transaction() as conn:
            rows = conn.execute(
                """
                SELECT j.*, a.status AS application_status, a.reason AS application_reason
                FROM jobs j
                LEFT JOIN applications a ON a.job_id = j.id
                  AND a.id = (SELECT MAX(id) FROM applications WHERE job_id = j.id)
                WHERE a.status IS NULL
                   OR a.status NOT IN ('submitted', 'skipped', 'failed')
                   OR (
                        a.status = 'failed'
                        AND (
                            a.reason LIKE '模型%'
                            OR a.reason LIKE 'Ollama%'
                            OR a.reason LIKE '%JSON%'
                        )
                   )
                ORDER BY j.id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def find_job_id(self, job: dict) -> int | None:
        url_key = dedup_key_for(job)
        combo_key = combo_key_for(job)
        title = (job.get("title") or "").strip()
        company = (job.get("company") or "").strip()
        location = (job.get("location") or "").strip()
        with self.transaction() as conn:
            if url_key.startswith("url:"):
                row = conn.execute("SELECT id FROM jobs WHERE dedup_key = ?", (url_key,)).fetchone()
                if row is not None:
                    return int(row["id"])
            row = conn.execute("SELECT id FROM jobs WHERE dedup_key = ?", (combo_key,)).fetchone()
            if row is not None:
                return int(row["id"])
            if title and company:
                row = conn.execute(
                    "SELECT id FROM jobs WHERE title = ? AND company = ? AND location = ? ORDER BY id DESC LIMIT 1",
                    (title, company, location),
                ).fetchone()
        return int(row["id"]) if row is not None else None

    def existing_application_status(self, job: dict) -> str | None:
        job_id = self.find_job_id(job)
        if job_id is None:
            return None
        with self.transaction() as conn:
            row = conn.execute("SELECT status FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        return row["status"] if row else None

    def record_application(self, job_id: int, status: str, reason: str) -> str:
        timestamp = now_text()
        with self.transaction() as conn:
            existing = conn.execute("SELECT id, status FROM applications WHERE job_id = ?", (job_id,)).fetchone()
            if existing is not None and existing["status"] == "submitted" and status != "submitted":
                return "duplicate"
            if existing is not None:
                conn.execute(
                    "UPDATE applications SET status = ?, reason = ?, updated_at = ? WHERE id = ?",
                    (status, reason, timestamp, existing["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO applications (job_id, status, reason, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (job_id, status, reason, timestamp, timestamp),
                )
            if status == "submitted":
                conn.execute(
                    "UPDATE jobs SET status = 'submitted', updated_at = ? WHERE id = ?",
                    (timestamp, job_id),
                )
        return "updated" if existing is not None else "inserted"

    def save_analysis(self, job_id: int, result: dict) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET ai_score = ?, ai_recommendation = ?, ai_summary = ?, ai_hard_reject = ?,
                    ai_raw_json = ?, ai_analyzed_at = ?, status = CASE
                        WHEN status IN ('queued', 'awaiting_manual_submit') THEN status
                        ELSE 'analyzed'
                    END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    int(result["score"]),
                    result["recommendation"],
                    result.get("summary") or "",
                    1 if result.get("hard_reject") else 0,
                    result.get("raw_json") or "",
                    now_text(),
                    now_text(),
                    job_id,
                ),
            )

    def list_unanalyzed(self) -> list[dict]:
        with self.transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                WHERE ai_score IS NULL AND ai_analyzed_at IS NULL
                ORDER BY id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_job(self, job_id: int) -> dict | None:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def record_failure(self, *, url: str, title: str, company: str, stage: str, reason: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO collect_failures (url, title, company, stage, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (url or "", title or "", company or "", stage, reason[:500], now_text()),
            )

    def count_jobs(self) -> int:
        with self.transaction() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])

    def job_rows(self, limit: int | None = None) -> list[dict]:
        sql = """
                SELECT j.id, j.title, j.company, j.salary, j.location, j.education, j.experience,
                       j.company_size, j.hr_activity, j.prior_apply, j.ai_score, j.ai_recommendation, j.ai_summary,
                       a.status AS application_status, a.reason AS application_reason
                FROM jobs j
                LEFT JOIN applications a ON a.job_id = j.id
                  AND a.id = (SELECT MAX(id) FROM applications WHERE job_id = j.id)
                ORDER BY j.id ASC
        """
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (int(limit),)
        with self.transaction() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = []
        for index, row in enumerate(rows, start=1):
            status = row["application_status"] or ""
            reason = row["application_reason"] or ""
            prior = (row["prior_apply"] or "").strip() or "未识别"
            if status == "submitted":
                delivery = "已投递·继续沟通" if "继续沟通" in reason or prior == "已投递" else "已投递"
            elif status == "failed" and application_can_retry(status, reason):
                delivery = "待重试"
            elif status == "failed":
                delivery = "投递失败"
            elif row["ai_score"] is None:
                delivery = "待筛选"
            else:
                delivery = "未投递"
            summary = row["ai_summary"] or ""
            if not summary and application_can_retry(status, reason):
                summary = reason
            if row["ai_score"] is not None or summary:
                summary = f"筛选前：{prior}。" + summary
            result.append(
                {
                    "序号": index,
                    "岗位详情": "查看",
                    "职位名称": row["title"],
                    "公司": row["company"],
                    "薪资": row["salary"],
                    "地点": row["location"],
                    "学历": row["education"],
                    "工作经验": row["experience"],
                    "公司规模": row["company_size"] or "页面没写",
                    "HR活跃": row["hr_activity"] or "页面没写",
                    "AI评分": "" if row["ai_score"] is None else int(row["ai_score"]),
                    "AI推荐结果": row["ai_recommendation"] or "",
                    "筛选前": prior,
                    "筛选说明": summary,
                    "投递状态": delivery,
                }
            )
        return result

    def job_detail_markdown(self, row_index: int, title: str = "", company: str = "") -> str:
        detail_sql = """
                SELECT j.*, a.status AS application_status, a.reason AS application_reason
                FROM jobs j
                LEFT JOIN applications a ON a.job_id = j.id
                  AND a.id = (SELECT MAX(id) FROM applications WHERE job_id = j.id)
        """
        with self.transaction() as conn:
            chosen = conn.execute(
                detail_sql + " ORDER BY j.id ASC LIMIT 1 OFFSET ?",
                (max(int(row_index), 0),),
            ).fetchone()
            if chosen is not None and title and company and (chosen["title"] != title or chosen["company"] != company):
                chosen = None
            if chosen is None and title and company:
                chosen = conn.execute(
                    detail_sql + " WHERE j.title = ? AND j.company = ? ORDER BY j.id ASC LIMIT 1",
                    (title, company),
                ).fetchone()
        if chosen is None:
            return "没有找到这条岗位。"
        prior = (chosen["prior_apply"] or "").strip() or "未识别"
        jd = (chosen["description"] or "").strip() or "采集时没有读到职位描述。"
        score = "" if chosen["ai_score"] is None else str(int(chosen["ai_score"]))
        lines = [
            f"### {chosen['title']}",
            f"**{chosen['company']}**",
            "",
            "#### 岗位详情",
            jd,
            "",
            "#### 采集信息",
            f"- 薪资：{chosen['salary'] or '页面没写'}",
            f"- 地点：{chosen['location'] or '页面没写'}",
            f"- 学历：{chosen['education'] or '页面没写'}",
            f"- 经验：{chosen['experience'] or '页面没写'}",
            f"- 公司规模：{chosen['company_size'] or '页面没写'}",
            f"- HR 活跃：{chosen['hr_activity'] or '页面没写'}",
            f"- 筛选前：{prior}",
            f"- 链接：{chosen['url'] or '没有'}",
            "",
            "#### 筛选结果",
            f"- AI 评分：{score or '还没筛选'}",
            f"- AI 推荐：{chosen['ai_recommendation'] or '还没筛选'}",
            f"- 说明：{('筛选前：' + prior + '。') if score or chosen['ai_summary'] else '还没筛选'}"
            f"{chosen['ai_summary'] or ''}",
            f"- 投递记录：{(chosen['application_status'] or '无')}"
            + (f"（{chosen['application_reason']}）" if chosen["application_reason"] else ""),
        ]
        return "\n".join(lines)

    def submitted_urls(self) -> set[str]:
        with self.transaction() as conn:
            rows = conn.execute(
                """
                SELECT j.url FROM jobs j
                JOIN applications a ON a.job_id = j.id
                WHERE a.status = 'submitted' AND j.url != ''
                """
            ).fetchall()
        return {row["url"] for row in rows}

    def jobs_in_collect_order(self) -> list[dict]:
        with self.transaction() as conn:
            rows = conn.execute(
                """
                SELECT j.*, a.status AS application_status
                FROM jobs j
                LEFT JOIN applications a ON a.job_id = j.id
                  AND a.id = (SELECT MAX(id) FROM applications WHERE job_id = j.id)
                ORDER BY j.id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_job_list(self) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM applications")
            conn.execute("DELETE FROM jobs")

    def recommendable_jobs(self, minimum_score: int = 80) -> list[dict]:
        with self.transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                WHERE ai_score >= ?
                  AND ai_hard_reject = 0
                  AND ai_recommendation IN ('优先投递', '推荐投递')
                ORDER BY ai_score DESC, id
                """,
                (minimum_score,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_application(self, job_id: int, status: str, reason: str) -> str:
        timestamp = now_text()
        with self.transaction() as conn:
            existing = conn.execute("SELECT id, status FROM applications WHERE job_id = ?", (job_id,)).fetchone()
            if existing is not None:
                return "duplicate"
            conn.execute(
                """
                INSERT INTO applications (job_id, status, reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, status, reason, timestamp, timestamp),
            )
            if status == "pending_confirm":
                conn.execute(
                    "UPDATE jobs SET status = 'queued', updated_at = ? WHERE id = ?",
                    (timestamp, job_id),
                )
        return "inserted"

    def update_application(self, application_id: int, status: str, reason: str) -> str:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
            if row is None:
                return "missing"
            conn.execute(
                "UPDATE applications SET status = ?, reason = ?, updated_at = ? WHERE id = ?",
                (status, reason, now_text(), application_id),
            )
            return row["status"]

    def get_application(self, application_id: int) -> dict | None:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
        return dict(row) if row else None

    def application_rows(self, limit: int = 200) -> list[dict]:
        with self.transaction() as conn:
            rows = conn.execute(
                """
                SELECT a.id, j.title, j.company, a.status, a.reason, j.ai_score, j.ai_recommendation
                FROM applications a
                JOIN jobs j ON j.id = a.job_id
                ORDER BY a.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "任务编号": row["id"],
                    "职位名称": row["title"],
                    "公司": row["company"],
                    "状态": row["status"],
                    "原因": row["reason"],
                    "AI评分": "" if row["ai_score"] is None else int(row["ai_score"]),
                    "AI推荐结果": row["ai_recommendation"] or "",
                }
            )
        return result

    def save_resume(self, filename: str, content: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO resume_document (id, filename, content, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    filename = excluded.filename,
                    content = excluded.content,
                    updated_at = excluded.updated_at
                """,
                (filename, content, now_text()),
            )

    def resume_text(self) -> str:
        with self.transaction() as conn:
            row = conn.execute("SELECT content FROM resume_document WHERE id = 1").fetchone()
        return row["content"] if row else ""

    def resume_filename(self) -> str:
        with self.transaction() as conn:
            row = conn.execute("SELECT filename FROM resume_document WHERE id = 1").fetchone()
        return row["filename"] if row else ""

    def clear_resume(self) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM resume_document")
