#!/usr/bin/env python3
"""
Migration script: ChromaDB → Postgres + pgvector.

Reads all chunks from ChromaDB, embeds them, and inserts into Postgres.
Also migrates JSONL log files into Postgres tables.

Usage:
    python db/migrate_chromadb_to_pg.py              # migrate everything
    python db/migrate_chromadb_to_pg.py --chunks-only # just RAG chunks
    python db/migrate_chromadb_to_pg.py --logs-only   # just JSONL logs
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import db_cursor, is_available
from db.vector_store import PgVectorStore, _get_embed_model

BOTDIR = Path(__file__).resolve().parent.parent
CHROMADB_PATH = str(BOTDIR / "chromadb-data")


def migrate_chunks():
    """Migrate all ChromaDB chunks to Postgres."""
    import chromadb
    client = chromadb.PersistentClient(path=CHROMADB_PATH)

    print("Loading embedding model...")
    model = _get_embed_model()

    store = PgVectorStore()
    total = 0

    for coll_name in ["methodology", "risk_mgmt", "commodities", "papers"]:
        coll = client.get_collection(coll_name)
        count = coll.count()
        print(f"\n  Migrating {coll_name}: {count} chunks...")

        # Get all chunks (ChromaDB limits to 10000 per get)
        results = coll.get(limit=count, include=["documents", "metadatas", "embeddings"])

        docs = results["documents"]
        metas = results["metadatas"]
        ids = results["ids"]
        embeddings = results.get("embeddings")

        # If ChromaDB didn't return embeddings, re-embed
        if embeddings is None or len(embeddings) == 0 or embeddings[0] is None:
            print(f"    Re-embedding {len(docs)} chunks...")
            embeddings = model.encode(docs)
        # Ensure embeddings are Python lists (not numpy arrays)
        embeddings = [e.tolist() if hasattr(e, 'tolist') else list(e) for e in embeddings]

        # Insert in batches
        BATCH = 100
        for i in range(0, len(docs), BATCH):
            batch_docs = docs[i:i + BATCH]
            batch_metas = metas[i:i + BATCH]
            batch_ids = ids[i:i + BATCH]
            batch_embs = embeddings[i:i + BATCH]

            # Enrich metadata with collection name
            for m in batch_metas:
                m["collection"] = coll_name

            store.add(coll_name, batch_docs, batch_metas, batch_ids, batch_embs)
            total += len(batch_docs)
            print(f"    {min(i + BATCH, len(docs))}/{len(docs)}", end="\r")

        print(f"    {coll_name}: {len(docs)} chunks migrated")

    print(f"\n  Total chunks migrated: {total}")
    return total


def migrate_logs():
    """Migrate JSONL log files to Postgres tables."""
    log_migrations = [
        ("agent_calls.jsonl", "reasoning_runs", _migrate_agent_call),
        ("decision_outcomes.jsonl", "decisions", _migrate_decision),
        ("fill_records.jsonl", "fill_records", _migrate_fill),
        ("stop_ratchets.jsonl", "stop_ratchets", _migrate_ratchet),
        ("intraday_trades.jsonl", "intraday_trades", _migrate_intraday_trade),
        ("intraday_signals.jsonl", "intraday_signals", _migrate_intraday_signal),
    ]

    for filename, table, migrate_fn in log_migrations:
        path = BOTDIR / "logs" / filename
        if not path.exists():
            print(f"  {filename}: not found, skipping")
            continue

        lines = [l for l in path.read_text().strip().split("\n") if l.strip()]
        print(f"  {filename}: {len(lines)} entries → {table}")

        migrated = 0
        for line in lines:
            try:
                entry = json.loads(line)
                migrate_fn(entry)
                migrated += 1
            except Exception as e:
                pass  # Skip malformed entries

        print(f"    {migrated}/{len(lines)} migrated")


def _migrate_agent_call(entry: dict):
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO reasoning_runs
                (calculation_run_id, symbol, agent, model_tier, status,
                 latency_sec, decision_fields, error, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            entry.get("calculation_run_id"),
            entry.get("symbol"),
            entry.get("agent"),
            entry.get("model_tier"),
            entry.get("status", "ok"),
            entry.get("latency_sec"),
            json.dumps(entry.get("decision_fields", {})),
            entry.get("error"),
            entry.get("timestamp"),
        ))


def _migrate_decision(entry: dict):
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO decisions
                (decision_id, calculation_run_id, symbol, asset_class,
                 decision, confidence, entry_price, stop_loss, stop_loss_pct,
                 price_target, price_target_pct, position_size_pct,
                 literature_winner, agent_consensus, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (decision_id) DO NOTHING
        """, (
            entry.get("decision_id"),
            entry.get("calculation_run_id"),
            entry.get("symbol"),
            entry.get("asset_class"),
            entry.get("decision"),
            entry.get("confidence"),
            entry.get("entry_price"),
            entry.get("stop_loss"),
            entry.get("stop_loss_pct"),
            entry.get("price_target"),
            entry.get("price_target_pct"),
            entry.get("position_size_pct"),
            entry.get("literature_winner"),
            json.dumps(entry.get("agent_consensus", {}), default=str),
            entry.get("timestamp"),
        ))


def _migrate_fill(entry: dict):
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO fill_records
                (calculation_run_id, symbol, side, decision_price,
                 expected_price, filled_price, quantity, slippage_bps,
                 spread_estimate_bps, total_cost_bps, order_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            entry.get("calculation_run_id"),
            entry.get("symbol"),
            entry.get("side"),
            entry.get("decision_price"),
            entry.get("expected_price"),
            entry.get("filled_price"),
            entry.get("quantity"),
            entry.get("slippage_bps"),
            entry.get("spread_estimate_bps"),
            entry.get("total_cost_bps"),
            entry.get("order_id"),
            entry.get("timestamp"),
        ))


def _migrate_ratchet(entry: dict):
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO stop_ratchets
                (symbol, side, old_stop, new_stop, current_price,
                 entry_price, atr_dollars, atr_units_favorable, reason, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            entry.get("symbol"),
            entry.get("side"),
            entry.get("old_stop"),
            entry.get("new_stop"),
            entry.get("current_price"),
            entry.get("entry_price"),
            entry.get("atr_dollars"),
            entry.get("atr_units_favorable"),
            entry.get("reason"),
            entry.get("timestamp"),
        ))


def _migrate_intraday_trade(entry: dict):
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO intraday_trades
                (symbol, direction, entry_price, exit_price, exit_reason,
                 hold_time_minutes, pnl_pct, signal_strength,
                 signal_components, shares, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            entry.get("symbol"),
            entry.get("direction"),
            entry.get("entry_price"),
            entry.get("exit_price"),
            entry.get("exit_reason"),
            entry.get("hold_time_minutes"),
            entry.get("pnl_pct"),
            entry.get("signal_strength"),
            json.dumps(entry.get("signal_components", {})),
            entry.get("shares"),
            entry.get("timestamp"),
        ))


def _migrate_intraday_signal(entry: dict):
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO intraday_signals
                (symbol, direction, strength, components, entry_price, reason, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            entry.get("symbol"),
            entry.get("direction"),
            entry.get("strength"),
            json.dumps(entry.get("components", {})),
            entry.get("entry_price"),
            entry.get("reason"),
            entry.get("timestamp"),
        ))


if __name__ == "__main__":
    if not is_available():
        print("ERROR: Postgres is not available. Run db/install_postgres.sh first.")
        sys.exit(1)

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    print("=== Trading Bot Migration: ChromaDB → Postgres ===\n")

    if mode in ("all", "--chunks-only"):
        print("Migrating RAG chunks...")
        migrate_chunks()

    if mode in ("all", "--logs-only"):
        print("\nMigrating JSONL logs...")
        migrate_logs()

    # Verify
    print("\n=== Verification ===")
    store = PgVectorStore()
    stats = store.get_stats()
    print(f"  Total chunks in Postgres: {stats['total']}")
    for coll, count in stats["by_collection"].items():
        print(f"    {coll}: {count}")

    print("\nMigration complete. ChromaDB data preserved as backup.")
