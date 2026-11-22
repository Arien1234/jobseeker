"""Remove applications wrongly marked from whole-page 继续沟通 scans."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.database import Database


def main() -> int:
    db = Database()
    with db.transaction() as conn:
        cur = conn.execute(
            "DELETE FROM applications WHERE reason LIKE ?",
            ("%继续沟通%已投过%",),
        )
        cleared = cur.rowcount
        conn.execute(
            """
            UPDATE jobs
            SET status = 'collected'
            WHERE status = 'submitted'
              AND id NOT IN (SELECT job_id FROM applications WHERE status = 'submitted')
            """
        )
    print(f"cleared_false_applied {cleared}")
    print(f"jobs {db.count_jobs()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
