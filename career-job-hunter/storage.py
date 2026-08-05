"""Persist processed job records in SQLite to avoid duplicate analysis."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "jobs.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    posted_at TEXT NOT NULL DEFAULT '',
    tech_stack TEXT NOT NULL DEFAULT '[]',
    visa_relocation_info TEXT NOT NULL DEFAULT '',
    core_responsibilities TEXT NOT NULL DEFAULT '[]',
    match_score INTEGER,
    match_reason TEXT NOT NULL DEFAULT '',
    work_location_type TEXT NOT NULL DEFAULT '',
    relocation_friendly INTEGER NOT NULL DEFAULT 0,
    processed_at TEXT NOT NULL
)
"""

MIGRATION_COLUMNS = (
    ("work_location_type", "TEXT NOT NULL DEFAULT ''"),
    ("relocation_friendly", "INTEGER NOT NULL DEFAULT 0"),
)


class JobStorage:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(CREATE_TABLE_SQL)
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
            for column_name, column_def in MIGRATION_COLUMNS:
                if column_name not in existing:
                    conn.execute(
                        f"ALTER TABLE jobs ADD COLUMN {column_name} {column_def}"
                    )
            conn.commit()

    def is_job_processed(self, job_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE job_id = ? LIMIT 1",
                (str(job_id),),
            ).fetchone()
        return row is not None

    def save_job(self, job_data: Dict[str, Any]) -> None:
        job_id = job_data.get("job_id")
        if not job_id:
            raise ValueError("job_data must include a job_id")

        tech_stack = job_data.get("tech_stack") or []
        if not isinstance(tech_stack, list):
            tech_stack = [str(tech_stack)]

        responsibilities = job_data.get("core_responsibilities") or []
        if not isinstance(responsibilities, list):
            responsibilities = [str(responsibilities)]

        match_score = job_data.get("match_score")
        if match_score is not None:
            try:
                match_score = int(match_score)
            except (TypeError, ValueError):
                match_score = None

        processed_at = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, title, company, location, url, description, posted_at,
                    tech_stack, visa_relocation_info, core_responsibilities,
                    match_score, match_reason, work_location_type, relocation_friendly,
                    processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    url = excluded.url,
                    description = excluded.description,
                    posted_at = excluded.posted_at,
                    tech_stack = excluded.tech_stack,
                    visa_relocation_info = excluded.visa_relocation_info,
                    core_responsibilities = excluded.core_responsibilities,
                    match_score = excluded.match_score,
                    match_reason = excluded.match_reason,
                    work_location_type = excluded.work_location_type,
                    relocation_friendly = excluded.relocation_friendly,
                    processed_at = excluded.processed_at
                """,
                (
                    str(job_id),
                    str(job_data.get("title") or ""),
                    str(job_data.get("company") or ""),
                    str(job_data.get("location") or ""),
                    str(job_data.get("url") or ""),
                    str(job_data.get("description") or ""),
                    str(job_data.get("posted_at") or ""),
                    json.dumps(tech_stack, ensure_ascii=False),
                    str(job_data.get("visa_relocation_info") or ""),
                    json.dumps(responsibilities, ensure_ascii=False),
                    match_score,
                    str(job_data.get("match_reason") or ""),
                    str(job_data.get("work_location_type") or ""),
                    1 if job_data.get("relocation_friendly") else 0,
                    processed_at,
                ),
            )
            conn.commit()

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY match_score DESC, processed_at DESC"
            ).fetchall()
        return [_row_to_dict(row) for row in rows]


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "job_id": row["job_id"],
        "title": row["title"],
        "company": row["company"],
        "location": row["location"],
        "url": row["url"],
        "description": row["description"],
        "posted_at": row["posted_at"],
        "tech_stack": json.loads(row["tech_stack"] or "[]"),
        "visa_relocation_info": row["visa_relocation_info"],
        "core_responsibilities": json.loads(row["core_responsibilities"] or "[]"),
        "match_score": row["match_score"],
        "match_reason": row["match_reason"],
        "work_location_type": row["work_location_type"],
        "relocation_friendly": bool(row["relocation_friendly"]),
        "processed_at": row["processed_at"],
    }


if __name__ == "__main__":
    storage = JobStorage(Path("jobs_test.db"))

    sample = {
        "job_id": "12345",
        "title": "Platform Engineer",
        "company": "Acme Cloud",
        "location": "Dublin, Ireland",
        "url": "https://example.com/jobs/12345",
        "description": "Manage Kubernetes clusters on AWS.",
        "posted_at": "2024-08-25T15:00:00Z",
        "tech_stack": ["Kubernetes", "AWS", "Terraform", "Go"],
        "visa_relocation_info": "Visa Sponsorship: mentioned; Relocation Support: not mentioned; Remote: hybrid",
        "core_responsibilities": [
            "Operate Kubernetes platform on AWS",
            "Maintain Terraform infrastructure",
            "Improve CI/CD pipelines",
        ],
        "match_score": 8,
        "match_reason": "Strong K8s/Terraform overlap; visa sponsorship mentioned.",
    }

    print("processed before save:", storage.is_job_processed("12345"))
    storage.save_job(sample)
    print("processed after save:", storage.is_job_processed("12345"))
