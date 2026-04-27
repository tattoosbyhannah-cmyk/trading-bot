"""
DB Boundary Test — ensures no raw cursor calls leak outside db/.

All database access must go through db/queries.py or db/connection.py.
Direct cursor.fetchall() / cursor.fetchone() outside db/ is a violation.
"""

import subprocess
import sys
from pathlib import Path

BOTDIR = Path(__file__).resolve().parent.parent


def test_no_raw_cursor_outside_db():
    """No fetchall/fetchone calls should exist outside db/ directory."""
    result = subprocess.run(
        ["grep", "-rn", r"\.fetchall()\|\.fetchone()\|cur\.execute(",
         "--include=*.py", str(BOTDIR)],
        capture_output=True, text=True,
    )
    violations = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        # Skip allowed directories
        if any(skip in line for skip in ["/db/", "/venv/", "__pycache__",
                                          "/deprecated/", "/tests/",
                                          "trading_dashboard.py"]):
            continue
        violations.append(line)

    assert not violations, (
        f"Found {len(violations)} raw cursor call(s) outside db/:\n"
        + "\n".join(violations)
    )
