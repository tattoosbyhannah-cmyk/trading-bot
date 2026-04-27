#!/usr/bin/env python3
"""
Alert Manager — sends email notifications for trading bot events via Gmail SMTP.

Alert types:
  - pipeline_failure: Symbol errored during daily pipeline
  - trade_executed: Paper trade placed successfully
  - stop_hit: Score detected a stop loss was hit
  - kill_switch_engaged: Kill switch activated
  - daily_summary: End-of-pipeline recap

All sends are wrapped in try/except — alert failures never crash the pipeline.

Usage:
    python alert_manager.py test          # Send a test email
    python alert_manager.py summary       # Send a mock daily summary
"""

import os
import smtplib
import logging
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

ALERT_EMAIL = os.getenv("ALERT_EMAIL")
ALERT_PASSWORD = os.getenv("ALERT_EMAIL_APP_PASSWORD")
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _send_email(subject: str, body: str):
    """Send an email via Gmail SMTP. Never raises — logs on failure."""
    if not ALERT_EMAIL or not ALERT_PASSWORD:
        logging.warning("Alert email not configured (ALERT_EMAIL or ALERT_EMAIL_APP_PASSWORD missing)")
        return

    try:
        msg = MIMEMultipart()
        msg["From"] = ALERT_EMAIL
        msg["To"] = ALERT_EMAIL
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(ALERT_EMAIL, ALERT_PASSWORD)
            server.send_message(msg)

    except Exception as e:
        logging.warning(f"Alert email failed: {e}")


# ── Alert Types ──────────────────────────────────────────────────────────────

def alert_pipeline_failure(symbol: str, error: str):
    """Alert: a symbol errored out during the daily pipeline."""
    subject = f"[TRADING BOT] Pipeline FAILURE: {symbol}"
    body = (
        f"Pipeline failure detected.\n\n"
        f"Symbol:    {symbol}\n"
        f"Error:     {error}\n"
        f"Timestamp: {datetime.now().isoformat()}\n\n"
        f"Check logs/daily_{symbol}_{datetime.now().strftime('%Y%m%d')}.log for details."
    )
    _send_email(subject, body)


def alert_trade_executed(symbol: str, direction: str, shares: float,
                         fill_price: float, spread_bps: float):
    """Alert: a paper trade was successfully placed."""
    subject = f"[TRADING BOT] Trade: {direction} {symbol}"
    body = (
        f"Paper trade executed.\n\n"
        f"Symbol:     {symbol}\n"
        f"Direction:  {direction}\n"
        f"Shares:     {shares}\n"
        f"Fill Price: ${fill_price:.2f}\n"
        f"Spread:     {spread_bps:.1f} bps\n"
        f"Timestamp:  {datetime.now().isoformat()}\n"
    )
    _send_email(subject, body)


def alert_stop_hit(symbol: str, direction: str, entry_price: float,
                   stop_price: float, loss_pct: float, decision_date: str):
    """Alert: score_outcomes detected a stop loss was hit."""
    subject = f"[TRADING BOT] STOP HIT: {symbol} ({loss_pct:+.1f}%)"
    body = (
        f"Stop loss triggered.\n\n"
        f"Symbol:        {symbol}\n"
        f"Direction:     {direction}\n"
        f"Entry Price:   ${entry_price:.2f}\n"
        f"Stop Price:    ${stop_price:.2f}\n"
        f"Loss:          {loss_pct:+.1f}%\n"
        f"Decision Date: {decision_date}\n"
        f"Detected:      {datetime.now().isoformat()}\n"
    )
    _send_email(subject, body)


def alert_kill_switch_engaged(reason: str):
    """Alert: kill switch was activated."""
    subject = "[TRADING BOT] KILL SWITCH ENGAGED"
    body = (
        f"Kill switch has been ENGAGED. All trading halted.\n\n"
        f"Reason:    {reason}\n"
        f"Timestamp: {datetime.now().isoformat()}\n\n"
        f"Run 'python kill_switch.py disengage' to resume trading."
    )
    _send_email(subject, body)


def alert_daily_summary(decisions: list, positions: list, equity: float,
                        errors: list):
    """Alert: end-of-pipeline daily summary."""
    subject = f"[TRADING BOT] Daily Summary — Equity ${equity:,.2f}"

    lines = [
        f"Daily Pipeline Summary",
        f"Timestamp: {datetime.now().isoformat()}",
        f"Portfolio Equity: ${equity:,.2f}",
        f"",
        f"── Decisions Made ──",
    ]

    if decisions:
        for d in decisions:
            lines.append(f"  {d['symbol']:5} {d['direction']:5} conf={d.get('confidence','?')} "
                         f"entry=${d.get('entry_price', 0) or 0:.2f}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("── Open Positions ──")
    if positions:
        for p in positions:
            lines.append(f"  {p['symbol']:5} {p['qty']} shares @ ${p['avg_entry']:.2f} "
                         f"(P&L: ${p['pnl']:.2f})")
    else:
        lines.append("  (none)")

    if errors:
        lines.append("")
        lines.append("── Errors ──")
        for e in errors:
            lines.append(f"  {e}")

    body = "\n".join(lines)
    _send_email(subject, body)


# ── Test ─────────────────────────────────────────────────────────────────────

def send_test():
    """Send a test email to verify SMTP configuration."""
    subject = "[TRADING BOT] Test Alert"
    body = (
        f"This is a test alert from the trading bot.\n\n"
        f"If you received this, email alerting is working correctly.\n"
        f"Timestamp: {datetime.now().isoformat()}\n"
    )
    print(f"Sending test email to {ALERT_EMAIL}...")
    _send_email(subject, body)
    print("Done (check inbox/spam).")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"

    if cmd == "test":
        send_test()
    elif cmd == "summary":
        alert_daily_summary(
            decisions=[
                {"symbol": "USO", "direction": "SHORT", "confidence": 8, "entry_price": 119.71},
                {"symbol": "UNG", "direction": "LONG", "confidence": 7, "entry_price": 10.81},
                {"symbol": "GLD", "direction": "HOLD", "confidence": 6, "entry_price": 442.50},
            ],
            positions=[
                {"symbol": "GLD", "qty": -1, "avg_entry": 448.02, "pnl": 6.19},
                {"symbol": "UNG", "qty": 2, "avg_entry": 10.78, "pnl": 0.25},
            ],
            equity=100006.25,
            errors=[],
        )
        print("Summary alert sent.")
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python alert_manager.py [test|summary]")
