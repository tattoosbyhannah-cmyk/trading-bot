-- Trading Bot Database Schema
-- Postgres 16 + pgvector

CREATE EXTENSION IF NOT EXISTS vector;

-- ── RAG Document Chunks ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS document_chunks (
    doc_id TEXT NOT NULL,
    chunk_ix INT NOT NULL,
    collection TEXT NOT NULL,           -- methodology, risk_mgmt, commodities, papers
    source_class TEXT NOT NULL,         -- academic_paper, practitioner_book, etc.
    authority_tier INT NOT NULL DEFAULT 3,
    asset_class TEXT NOT NULL DEFAULT 'general',
    published_at DATE,
    published_date_int INT,             -- YYYYMMDD for fast range queries
    embedding_version TEXT NOT NULL DEFAULT 'nomic-embed-text-v1',
    checksum TEXT,
    embedding vector(384),              -- nomic-embed-text-v1 dimension
    chunk_text TEXT NOT NULL,
    author TEXT,
    title TEXT,
    page INT,
    source_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (doc_id, chunk_ix)
);

-- Full-text search column (auto-generated)
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', chunk_text)) STORED;

CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON document_chunks
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_collection ON document_chunks (collection);
CREATE INDEX IF NOT EXISTS idx_chunks_asset_class ON document_chunks (asset_class);
CREATE INDEX IF NOT EXISTS idx_chunks_authority ON document_chunks (authority_tier);
CREATE INDEX IF NOT EXISTS idx_chunks_published ON document_chunks (published_date_int);
CREATE INDEX IF NOT EXISTS idx_chunks_source_id ON document_chunks (source_id);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON document_chunks USING gin(tsv);

-- ── Orders ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    calculation_run_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    qty NUMERIC NOT NULL,
    limit_price NUMERIC,
    stop_price NUMERIC,
    status TEXT NOT NULL,
    broker TEXT NOT NULL DEFAULT 'alpaca',
    broker_order_id TEXT,
    idempotency_key TEXT UNIQUE,
    submitted_at TIMESTAMPTZ DEFAULT NOW(),
    filled_at TIMESTAMPTZ,
    filled_avg_price NUMERIC,
    raw_response JSONB
);

-- ── Reasoning Runs (agent calls) ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS reasoning_runs (
    id SERIAL PRIMARY KEY,
    calculation_run_id TEXT,
    symbol TEXT,
    agent TEXT NOT NULL,
    model_tier TEXT,
    status TEXT NOT NULL DEFAULT 'ok',
    latency_sec NUMERIC,
    decision_fields JSONB,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reasoning_calc ON reasoning_runs (calculation_run_id);
CREATE INDEX IF NOT EXISTS idx_reasoning_agent ON reasoning_runs (agent);
CREATE INDEX IF NOT EXISTS idx_reasoning_symbol ON reasoning_runs (symbol);

-- ── Decisions ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    calculation_run_id TEXT,
    symbol TEXT NOT NULL,
    asset_class TEXT,
    decision TEXT NOT NULL,
    confidence INT,
    entry_price NUMERIC,
    stop_loss NUMERIC,
    stop_loss_pct NUMERIC,
    price_target NUMERIC,
    price_target_pct NUMERIC,
    position_size_pct NUMERIC,
    literature_winner TEXT,
    agent_consensus JSONB,
    -- Outcome fields (filled by scorer)
    price_1d NUMERIC,
    price_5d NUMERIC,
    price_30d NUMERIC,
    return_1d_pct NUMERIC,
    return_5d_pct NUMERIC,
    return_30d_pct NUMERIC,
    hit_stop BOOLEAN,
    hit_target BOOLEAN,
    stop_hit_day INT,
    target_hit_day INT,
    opportunity_cost_pct NUMERIC,
    scored_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_decisions_calc ON decisions (calculation_run_id);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions (symbol);

-- ── Policy Events (risk gatekeeper) ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS policy_events (
    id SERIAL PRIMARY KEY,
    calculation_run_id TEXT,
    symbol TEXT NOT NULL,
    risk_score INT NOT NULL,
    approval_status TEXT NOT NULL,
    checks JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Fill Records ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS fill_records (
    id SERIAL PRIMARY KEY,
    calculation_run_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    decision_price NUMERIC,
    expected_price NUMERIC,
    filled_price NUMERIC NOT NULL,
    quantity NUMERIC NOT NULL,
    slippage_bps NUMERIC,
    spread_estimate_bps NUMERIC,
    total_cost_bps NUMERIC,
    order_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Stop Ratchets ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS stop_ratchets (
    id SERIAL PRIMARY KEY,
    calculation_run_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT,
    old_stop NUMERIC,
    new_stop NUMERIC,
    current_price NUMERIC,
    entry_price NUMERIC,
    atr_dollars NUMERIC,
    atr_units_favorable NUMERIC,
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Intraday Trades ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS intraday_trades (
    id SERIAL PRIMARY KEY,
    calculation_run_id TEXT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price NUMERIC NOT NULL,
    exit_price NUMERIC,
    exit_reason TEXT,
    hold_time_minutes NUMERIC,
    pnl_pct NUMERIC,
    signal_strength NUMERIC,
    signal_components JSONB,
    shares INT,
    intraday_atr_pct NUMERIC,
    take_profit_pct NUMERIC,
    stop_loss_pct NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Intraday Signals ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS intraday_signals (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    strength NUMERIC,
    components JSONB,
    entry_price NUMERIC,
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
