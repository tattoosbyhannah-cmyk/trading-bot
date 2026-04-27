[1mdiff --git a/db/connection.py b/db/connection.py[m
[1mindex 7c49609..c111614 100644[m
[1m--- a/db/connection.py[m
[1m+++ b/db/connection.py[m
[36m@@ -35,14 +35,22 @@[m [m_DB_TO_CANONICAL = {[m
 [m
 [m
 def _normalize_row(row: dict) -> dict:[m
[31m-    """Translate DB column names to the canonical keys the orchestrator expects."""[m
[32m+[m[32m    """Translate DB column names and types to canonical forms.[m
[32m+[m
[32m+[m[32m    Column renames: created_at → timestamp (via _DB_TO_CANONICAL)[m
[32m+[m[32m    Type coercions: Decimal → float, datetime → ISO string (for timestamp key)[m
[32m+[m[32m    """[m
     if row is None:[m
         return None[m
[32m+[m[32m    from decimal import Decimal[m
     out = {}[m
     for k, v in row.items():[m
         canon = _DB_TO_CANONICAL.get(k, k)[m
[31m-        # Convert datetime objects to ISO strings[m
[31m-        if hasattr(v, "isoformat") and canon == "timestamp":[m
[32m+[m[32m        # Coerce Decimal → float at the boundary (all NUMERIC columns)[m
[32m+[m[32m        if isinstance(v, Decimal):[m
[32m+[m[32m            v = float(v)[m
[32m+[m[32m        # Convert datetime objects to ISO strings for the timestamp key[m
[32m+[m[32m        elif hasattr(v, "isoformat") and canon == "timestamp":[m
             v = v.isoformat()[m
         out[canon] = v[m
     return out[m
[1mdiff --git a/db/queries.py b/db/queries.py[m
[1mindex cb00bcb..661dc5f 100644[m
[1m--- a/db/queries.py[m
[1m+++ b/db/queries.py[m
[36m@@ -9,7 +9,7 @@[m [mDB column names (e.g. created_at) to canonical keys (e.g. timestamp).[m
 """[m
 [m
 import json[m
[31m-from datetime import datetime, timedelta[m
[32m+[m[32mfrom datetime import datetime, timedelta, timezone[m
 from pathlib import Path[m
 [m
 LOGS = Path(__file__).resolve().parent.parent / "logs"[m
[36m@@ -38,7 +38,7 @@[m [mdef _fetch_rows(sql: str, params: tuple = ()) -> list:[m
 def load_recent_decisions(symbol: str, days: int = 3) -> list:[m
     """Load recent decisions. SQL first, JSONL fallback."""[m
     try:[m
[31m-        cutoff = (datetime.now() - timedelta(days=days)).isoformat()[m
[32m+[m[32m        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()[m
         rows = _fetch_rows("""[m
             SELECT decision_id, calculation_run_id, symbol, decision,[m
                    confidence, entry_price, stop_loss, stop_loss_pct,[m
[36m@@ -257,7 +257,46 @@[m [mdef load_ratchets_by_calc_id(calc_id: str) -> list:[m
     return results[m
 [m
 [m
[31m-# ── 8. Today's agent call counts ─────────────────────────────────────────────[m
[32m+[m[32m# ── 8. Latest calculation_run_id ──────────────────────────────────────────────[m
[32m+[m
[32m+[m[32mdef find_latest_calc_id(symbol: str = None) -> str:[m
[32m+[m[32m    """Find the most recent calculation_run_id. SQL first, JSONL fallback."""[m
[32m+[m[32m    try:[m
[32m+[m[32m        if symbol:[m
[32m+[m[32m            rows = _fetch_rows("""[m
[32m+[m[32m                SELECT calculation_run_id FROM decisions[m
[32m+[m[32m                WHERE symbol = %s AND calculation_run_id IS NOT NULL[m
[32m+[m[32m                ORDER BY created_at DESC LIMIT 1[m
[32m+[m[32m            """, (symbol.upper(),))[m
[32m+[m[32m        else:[m
[32m+[m[32m            rows = _fetch_rows("""[m
[32m+[m[32m                SELECT calculation_run_id FROM decisions[m
[32m+[m[32m                WHERE calculation_run_id IS NOT NULL[m
[32m+[m[32m                ORDER BY created_at DESC LIMIT 1[m
[32m+[m[32m            """)[m
[32m+[m[32m        if rows and rows[0].get("calculation_run_id"):[m
[32m+[m[32m            return rows[0]["calculation_run_id"][m
[32m+[m[32m    except Exception:[m
[32m+[m[32m        pass[m
[32m+[m
[32m+[m[32m    # JSONL fallback[m
[32m+[m[32m    path = LOGS / "decision_outcomes.jsonl"[m
[32m+[m[32m    if not path.exists():[m
[32m+[m[32m        return ""[m
[32m+[m[32m    for line in reversed(path.read_text().strip().split("\n")):[m
[32m+[m[32m        if not line:[m
[32m+[m[32m            continue[m
[32m+[m[32m        try:[m
[32m+[m[32m            r = json.loads(line)[m
[32m+[m[32m            cid = r.get("calculation_run_id", "")[m
[32m+[m[32m            if cid and (not symbol or r.get("symbol") == symbol.upper()):[m
[32m+[m[32m                return cid[m
[32m+[m[32m        except Exception:[m
[32m+[m[32m            continue[m
[32m+[m[32m    return ""[m
[32m+[m
[32m+[m
[32m+[m[32m# ── 9. Today's agent call counts ─────────────────────────────────────────────[m
 [m
 def load_todays_agent_calls() -> list:[m
     """Load today's agent calls for health check."""[m
