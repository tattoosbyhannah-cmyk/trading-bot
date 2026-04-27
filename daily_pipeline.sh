#!/usr/bin/env bash
# Daily trading pipeline: majority vote across assets + outcome scoring.
#
# Order of operations:
#   1. Abort immediately if KILL_SWITCH file is present.
#   2. Activate venv.
#   3. Run majority_vote_orchestrator.py sequentially on USO, UNG, GLD.
#   4. Run score_outcomes.py to score any matured decisions.
#
# Per-symbol output goes to logs/daily_SYMBOL_YYYYMMDD.log.
# Pipeline-level events go to logs/daily_pipeline.log.

set -u

TRADING_BOT_DIR="$HOME/trading-bot"
LOG_DIR="$TRADING_BOT_DIR/logs"
DATE_TAG="$(date +%Y%m%d)"
MAIN_LOG="$LOG_DIR/daily_pipeline.log"
VENV="$TRADING_BOT_DIR/venv/bin/activate"
# Symbols loaded from instrument registry (config/instruments.yaml)
# Fallback to hardcoded list if registry unavailable
SYMBOLS_STR=$(python3 -c "from config.instrument_registry import registry; print(' '.join(registry.get_active_symbols()))" 2>/dev/null)
if [ -z "$SYMBOLS_STR" ]; then
    SYMBOLS=(USO UNG GLD)
else
    read -ra SYMBOLS <<< "$SYMBOLS_STR"
fi

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date -Iseconds)] $*" | tee -a "$MAIN_LOG"
}

cd "$TRADING_BOT_DIR" || { log "FATAL: cannot cd to $TRADING_BOT_DIR"; exit 1; }

# 1. Kill switch check
if [ -f "$TRADING_BOT_DIR/KILL_SWITCH" ]; then
    log "KILL SWITCH ENGAGED — aborting daily pipeline"
    log "Reason: $(cat "$TRADING_BOT_DIR/KILL_SWITCH")"
    exit 0  # Exit 0 so systemd doesn't treat halt as failure
fi

# 2. Activate venv
# shellcheck disable=SC1090
if ! source "$VENV"; then
    log "FATAL: failed to activate venv at $VENV"
    exit 1
fi

log "=== Starting daily pipeline for $DATE_TAG ==="

# 3. Per-symbol majority vote + paper trade execution
ERRORS=""
for SYMBOL in "${SYMBOLS[@]}"; do
    SYMBOL_LOG="$LOG_DIR/daily_${SYMBOL}_${DATE_TAG}.log"
    log "Running majority vote + execute: $SYMBOL -> $SYMBOL_LOG"
    if python "$TRADING_BOT_DIR/majority_vote_orchestrator.py" "$SYMBOL" --execute \
        > "$SYMBOL_LOG" 2>&1; then
        log "  $SYMBOL: OK"
    else
        EXIT_CODE=$?
        log "  $SYMBOL: FAILED (exit $EXIT_CODE)"
        ERRORS="${ERRORS}${SYMBOL}: exit ${EXIT_CODE}\n"
        python -c "
from alert_manager import alert_pipeline_failure
alert_pipeline_failure('$SYMBOL', 'Exit code $EXIT_CODE — see daily_${SYMBOL}_${DATE_TAG}.log')
" 2>/dev/null
    fi
done

# 4. Score matured outcomes
log "Running score_outcomes.py..."
if python "$TRADING_BOT_DIR/score_outcomes.py" >> "$MAIN_LOG" 2>&1; then
    log "Scorer: OK"
else
    EXIT_CODE=$?
    log "Scorer: FAILED (exit $EXIT_CODE)"
fi

# 5. Daily summary email
log "Sending daily summary email..."
python "$TRADING_BOT_DIR/send_daily_summary.py" "$ERRORS" 2>/dev/null
log "=== Pipeline complete ==="
