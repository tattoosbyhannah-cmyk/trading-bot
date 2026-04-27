#!/usr/bin/env python3
"""
Trading Bot Health Check — audits all systems for bugs, errors, and anomalies.

Usage:
    python health_check.py
    bothealth   (bash alias)
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env' if (Path(__file__).resolve().parent / '.env').exists() else None)

BOTDIR = Path(__file__).parent
LOGS = BOTDIR / "logs"
TODAY = datetime.now().strftime("%Y-%m-%d")
NOW = datetime.now(timezone.utc)
WEEKDAY = NOW.weekday() < 5  # Mon-Fri


# ── Result accumulator ────────────────────────────────────────────────────────

class Check:
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"

    def __init__(self, name: str):
        self.name = name
        self.status = self.GREEN
        self.details = []

    def ok(self, msg: str):
        self.details.append(("ok", msg))

    def warn(self, msg: str):
        if self.status == self.GREEN:
            self.status = self.YELLOW
        self.details.append(("warn", msg))

    def fail(self, msg: str):
        self.status = self.RED
        self.details.append(("fail", msg))

    @property
    def icon(self):
        return {"green": "\u2705", "yellow": "\u26a0\ufe0f ", "red": "\u274c"}[self.status]


# ── Infrastructure ────────────────────────────────────────────────────────────

def check_infrastructure() -> Check:
    c = Check("Infrastructure")

    # LLM servers (from model registry)
    try:
        from config.model_registry import model_registry
        endpoints = model_registry.get_all_endpoints()
        names = model_registry.get_all_model_names()
    except Exception:
        endpoints = {"http://127.0.0.1:8081/v1"}
        names = {}

    for endpoint in sorted(endpoints):
        display = names.get(endpoint, "unknown")
        health_url = endpoint.replace("/v1", "/v1/models")
        try:
            r = requests.get(health_url, timeout=5)
            if r.ok:
                model_id = r.json()["data"][0]["id"]
                c.ok(f"LLM ({display}): {model_id}")
            else:
                c.fail(f"LLM ({display}): HTTP {r.status_code}")
        except Exception as e:
            c.fail(f"LLM ({display}): not responding — {e}")

    # Kill switch
    ks = BOTDIR / "KILL_SWITCH"
    if ks.exists():
        c.warn(f"Kill switch ENGAGED: {ks.read_text().strip()[:80]}")
    else:
        c.ok("Kill switch: disengaged")

    # Disk space
    try:
        st = os.statvfs(str(BOTDIR))
        free_gb = (st.f_bavail * st.f_frsize) / (1024**3)
        if free_gb < 5:
            c.fail(f"Disk space: {free_gb:.1f} GB free (critical)")
        elif free_gb < 20:
            c.warn(f"Disk space: {free_gb:.1f} GB free")
        else:
            c.ok(f"Disk space: {free_gb:.1f} GB free")
    except Exception:
        c.warn("Disk space: could not check")

    # Vector store (Postgres + pgvector preferred, ChromaDB fallback)
    pg_ok = False
    try:
        from db.connection import is_available as pg_available
        if pg_available():
            from db.vector_store import PgVectorStore
            store = PgVectorStore()
            stats = store.get_stats()
            total = stats["total"]
            if total > 0:
                c.ok(f"Postgres pgvector: {total} chunks")
                pg_ok = True
            else:
                c.warn("Postgres pgvector: 0 chunks (run migration)")
    except Exception:
        pass

    if not pg_ok:
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(BOTDIR / "chromadb-data"))
            total = sum(client.get_collection(n).count()
                        for n in ["methodology", "risk_mgmt", "commodities", "papers"])
            c.ok(f"ChromaDB: {total} chunks (Postgres not available)")
        except Exception as e:
            c.fail(f"Vector store: {e}")

    return c


# ── Daily Pipeline ────────────────────────────────────────────────────────────

def check_daily_pipeline() -> Check:
    c = Check("Daily pipeline")

    log = LOGS / "daily_pipeline.log"
    if not log.exists():
        c.fail("daily_pipeline.log not found")
        return c

    lines = log.read_text().strip().split("\n")

    # Last run timestamp
    last_ts = None
    for line in reversed(lines):
        if "=== Pipeline complete ===" in line or "=== Starting daily pipeline" in line:
            try:
                ts_str = line.split("]")[0].lstrip("[")
                last_ts = datetime.fromisoformat(ts_str)
                break
            except Exception:
                pass

    if last_ts:
        # Promote naive timestamps to UTC for safe comparison
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        age_hours = (NOW - last_ts).total_seconds() / 3600
        if age_hours > 26 and WEEKDAY:
            c.warn(f"Last run: {last_ts.strftime('%Y-%m-%d %H:%M')} ({age_hours:.0f}h ago)")
        else:
            c.ok(f"Last run: {last_ts.strftime('%Y-%m-%d %H:%M')} ({age_hours:.0f}h ago)")
    else:
        c.warn("Could not parse last run timestamp")

    # Errors in last 50 lines
    tail = lines[-50:]
    errors = [l for l in tail if any(k in l.upper() for k in
              ["ERROR", "EXCEPTION", "TRACEBACK", "FAILED"])]
    if errors:
        c.warn(f"{len(errors)} error lines in last 50 log lines")
        for e in errors[-3:]:
            c.details.append(("warn", f"  {e.strip()[:120]}"))
    else:
        c.ok("No errors in recent log")

    # Today's decisions
    outcomes = LOGS / "decision_outcomes.jsonl"
    if outcomes.exists():
        today_decisions = []
        for line in outcomes.read_text().strip().split("\n"):
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("timestamp", "")[:10] == TODAY:
                    today_decisions.append(r)
            except Exception:
                pass
        if today_decisions:
            syms = ", ".join(r["symbol"] for r in today_decisions)
            c.ok(f"Today's decisions: {len(today_decisions)} ({syms})")
        elif WEEKDAY and NOW.hour >= 10:
            c.warn("No decisions today (expected by 10 AM on weekdays)")
        else:
            c.ok("No decisions today (expected)")
    else:
        c.warn("decision_outcomes.jsonl not found")

    # Playbook
    pb_file = BOTDIR / "playbook" / "daily_playbook.json"
    if pb_file.exists():
        try:
            pb = json.loads(pb_file.read_text())
            valid = pb.get("valid_until", "")[:10]
            syms = list(pb.get("symbols", {}).keys())
            if valid >= TODAY:
                c.ok(f"Playbook valid until {valid}, symbols: {', '.join(syms)}")
            else:
                c.warn(f"Playbook expired: valid_until={valid}")
        except Exception:
            c.warn("Playbook exists but couldn't parse")
    else:
        c.warn("No playbook found")

    return c


# ── Intraday System ───────────────────────────────────────────────────────────

def check_intraday() -> Check:
    c = Check("Intraday system")

    # Service status
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "intraday-swing.service"],
            capture_output=True, text=True, timeout=5)
        status = result.stdout.strip()
        if status == "active" or status == "activating":
            c.ok(f"intraday-swing.service: {status}")
        elif status == "inactive":
            c.ok(f"intraday-swing.service: inactive (normal outside market hours)")
        else:
            c.warn(f"intraday-swing.service: {status}")
    except Exception:
        c.warn("Could not check intraday-swing.service")

    # Errors in log
    log = LOGS / "intraday_swing.log"
    if log.exists():
        lines = log.read_text().strip().split("\n")
        tail = lines[-100:]
        errors = [l for l in tail if any(k in l for k in
                  ["Traceback", "Error", "FAILED", "error"])]
        today_errors = [l for l in errors if TODAY in l or
                        (len(l) > 25 and l[:10] == TODAY)]
        if today_errors:
            c.warn(f"{len(today_errors)} errors in today's intraday log")
            for e in today_errors[-2:]:
                c.details.append(("warn", f"  {e.strip()[:120]}"))
        else:
            c.ok("No errors in recent intraday log")
    else:
        c.ok("No intraday log yet")

    # Today's trades
    trades_log = LOGS / "intraday_trades.jsonl"
    if trades_log and trades_log.exists():
        today_trades = sum(1 for line in trades_log.read_text().strip().split("\n")
                           if line and TODAY in line)
        c.ok(f"Intraday trades today: {today_trades}")
    else:
        c.ok("No intraday trades log")

    # Today's signals
    sig_log = LOGS / "intraday_signals.jsonl"
    if sig_log and sig_log.exists():
        today_sigs = sum(1 for line in sig_log.read_text().strip().split("\n")
                         if line and TODAY in line)
        if today_sigs == 0 and WEEKDAY and 10 <= NOW.hour <= 16:
            c.warn("Zero intraday signals during market hours")
        else:
            c.ok(f"Intraday signals today: {today_sigs}")

    return c


# ── Agent Health ──────────────────────────────────────────────────────────────

def check_agents() -> Check:
    c = Check("Agent health")

    from db.queries import load_todays_agent_calls
    today_entries = load_todays_agent_calls()

    if not today_entries:
        if WEEKDAY and NOW.hour >= 10:
            c.warn("No agent calls today (expected by 10 AM)")
        else:
            c.ok("No agent calls today")
        return c

    # Per-agent counts
    by_agent = {}
    errors = 0
    for e in today_entries:
        agent = e.get("agent", "?")
        by_agent[agent] = by_agent.get(agent, 0) + 1
        if e.get("status") == "error":
            errors += 1

    expected = {"technical_analyst", "fundamentals_analyst", "sentiment_analyst",
                "bull_researcher", "bear_researcher", "literature_judge",
                "risk_gatekeeper", "master_orchestrator"}
    missing = expected - set(by_agent.keys())

    total = sum(by_agent.values())
    c.ok(f"{total} agent calls today across {len(by_agent)} agents")

    if missing and WEEKDAY and NOW.hour >= 10:
        c.warn(f"Missing agents today: {', '.join(sorted(missing))}")

    if errors:
        c.warn(f"{errors} agent calls failed today")

    # Model tier check
    fast_on_deep = 0
    deep_on_fast = 0
    for e in today_entries:
        tier = e.get("model_tier", "")
        lane = e.get("model_lane", "")
        if tier == "fast" and lane == "deep":
            fast_on_deep += 1
        if tier == "deep" and lane == "fast":
            deep_on_fast += 1
    if fast_on_deep or deep_on_fast:
        c.warn(f"Model tier mismatch: {fast_on_deep} fast-on-deep, {deep_on_fast} deep-on-fast")
    else:
        fast_count = sum(1 for e in today_entries if e.get("model_tier") == "fast")
        deep_count = sum(1 for e in today_entries if e.get("model_tier") == "deep")
        if fast_count and deep_count:
            c.ok(f"Model tiers: {fast_count} fast (8B) + {deep_count} deep (30B)")

    return c


# ── Timers ────────────────────────────────────────────────────────────────────

def check_timers() -> Check:
    c = Check("Timers")

    try:
        result = subprocess.run(
            ["systemctl", "--user", "list-timers", "--all", "--no-pager"],
            capture_output=True, text=True, timeout=10)
        lines = result.stdout.strip().split("\n")

        trading_timers = [l for l in lines if "trading-" in l or "intraday-" in l]
        if not trading_timers:
            c.warn("No trading timers found")
            return c

        for line in trading_timers:
            parts = line.split()
            # Find the UNIT column
            unit = None
            for p in parts:
                if "trading-" in p or "intraday-" in p:
                    unit = p
                    break
            if unit:
                # Check if NEXT is "-" (never scheduled)
                if parts[0] == "-":
                    c.warn(f"{unit}: not scheduled")
                else:
                    c.ok(f"{unit}: next {parts[0]} {parts[1]} {parts[2]}")
    except Exception as e:
        c.warn(f"Could not list timers: {e}")

    return c


# ── Outcome Tracking ─────────────────────────────────────────────────────────

def check_outcomes() -> Check:
    c = Check("Outcomes")

    from db.queries import load_all_decisions
    records = load_all_decisions()

    if not records:
        c.warn("No decision records found")
        return c

    total = len(records)
    scored = sum(1 for r in records if r.get("return_1d_pct") is not None)
    c.ok(f"{total} total decisions, {scored} scored")

    # Unscored older than 2 days
    cutoff = (NOW - timedelta(days=2)).isoformat()
    stale = [r for r in records
             if r.get("return_1d_pct") is None
             and r.get("timestamp", "") < cutoff
             and r.get("decision") in ("LONG", "SHORT")]
    if stale:
        syms = ", ".join(f"{r['symbol']} ({r['timestamp'][:10]})" for r in stale[-3:])
        c.warn(f"{len(stale)} unscored decisions older than 2 days: {syms}")

    # Open positions that should have been ratcheted
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(
            api_key=os.getenv("ALPACA_API_KEY_ID"),
            secret_key=os.getenv("ALPACA_SECRET_KEY"),
            paper=True,
        )
        positions = client.get_all_positions()
        if positions:
            c.ok(f"{len(positions)} open positions")
        else:
            c.ok("No open positions")
    except Exception:
        c.warn("Could not check Alpaca positions")

    # Milestone: SLV expansion readiness
    MILESTONE_DATE = "2026-04-30"
    MILESTONE_COUNT = 30
    if TODAY >= MILESTONE_DATE:
        if scored >= MILESTONE_COUNT:
            c.ok(f"MILESTONE: {scored} scored outcomes reached. "
                 f"System may be ready for SLV expansion. Review performance with Hannah.")
        else:
            remaining = MILESTONE_COUNT - scored
            c.ok(f"Milestone progress: {scored}/{MILESTONE_COUNT} scored outcomes "
                 f"({remaining} more needed for SLV expansion review)")
    else:
        c.ok(f"Milestone progress: {scored}/{MILESTONE_COUNT} scored "
             f"(target date: {MILESTONE_DATE})")

    # Ratchet log
    ratchet_log = LOGS / "stop_ratchets.jsonl"
    if ratchet_log.exists():
        today_ratchets = sum(1 for l in ratchet_log.read_text().strip().split("\n")
                             if l and TODAY in l)
        if today_ratchets:
            c.ok(f"{today_ratchets} stop ratchets today")

    return c


# ── Main ──────────────────────────────────────────────────────────────────────

def check_contracts() -> Check:
    """Run contract tests via pytest and report results."""
    c = Check("Contracts")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_llm_contracts.py",
             "-v", "--tb=short", "-q", "--no-header",
             # Skip slow LLM math tests by default
             "-k", "not TestNoMathBoundary"],
            capture_output=True, text=True, timeout=120,
            cwd=str(BOTDIR),
        )
        output = result.stdout + result.stderr
        # Parse pytest output for pass/fail counts
        # Look for "X passed" and "X failed"
        passed = 0
        failed = 0
        for line in output.split("\n"):
            if "passed" in line:
                m = re.search(r"(\d+) passed", line)
                if m:
                    passed = int(m.group(1))
            if "failed" in line:
                m = re.search(r"(\d+) failed", line)
                if m:
                    failed = int(m.group(1))

        if failed > 0:
            c.fail(f"{failed} contract tests FAILED, {passed} passed")
            # Show failed test names
            for line in output.split("\n"):
                if "FAILED" in line:
                    c.details.append(("fail", f"  {line.strip()[:100]}"))
        elif passed > 0:
            c.ok(f"{passed} contract tests passed")
        else:
            c.warn("Could not parse contract test results")
            c.details.append(("warn", output[:200]))
    except subprocess.TimeoutExpired:
        c.warn("Contract tests timed out (120s)")
    except Exception as e:
        c.warn(f"Contract tests failed to run: {e}")

    return c


def main():
    run_contracts = "--run-contracts" in sys.argv

    checks = [
        check_infrastructure(),
        check_daily_pipeline(),
        check_intraday(),
        check_agents(),
        check_timers(),
        check_outcomes(),
    ]

    if run_contracts:
        checks.append(check_contracts())

    # Traffic light summary
    print(f"\n{'='*50}")
    print(f"  TRADING BOT HEALTH CHECK")
    print(f"  {NOW.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    for ck in checks:
        # Build one-line summary from first ok detail
        summary_parts = [d[1] for d in ck.details if d[0] == "ok"]
        summary = summary_parts[0] if summary_parts else ""
        print(f"  {ck.icon} {ck.name:20s} {summary[:50]}")

    # Details for non-green items
    non_green = [ck for ck in checks if ck.status != Check.GREEN]
    if non_green:
        print(f"\n{'─'*50}")
        print(f"  DETAILS")
        print(f"{'─'*50}")
        for ck in non_green:
            print(f"\n  {ck.icon} {ck.name}:")
            for level, msg in ck.details:
                if level == "warn":
                    print(f"    \u26a0\ufe0f  {msg}")
                elif level == "fail":
                    print(f"    \u274c {msg}")
    else:
        print(f"\n  All systems green.")

    print(f"\n{'='*50}\n")

    # Exit code: 0 = all green, 1 = warnings, 2 = failures
    if any(ck.status == Check.RED for ck in checks):
        return 2
    if any(ck.status == Check.YELLOW for ck in checks):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
