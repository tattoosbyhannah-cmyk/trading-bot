#!/bin/bash
# Install Postgres 16 + pgvector and create the trading database.
# Run with: sudo bash db/install_postgres.sh

set -e

echo "=== Installing PostgreSQL 16 + pgvector ==="
apt-get update -qq
apt-get install -y postgresql-16 postgresql-16-pgvector

echo "=== Starting PostgreSQL ==="
systemctl enable postgresql
systemctl start postgresql

echo "=== Creating trading database ==="
sudo -u postgres psql -c "CREATE USER trading WITH PASSWORD 'trading_dev_2026';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE trading OWNER trading;" 2>/dev/null || true
sudo -u postgres psql -d trading -c "CREATE EXTENSION IF NOT EXISTS vector;"

echo "=== Running schema migration ==="
sudo -u postgres psql -d trading -f /home/hannah/trading-bot/db/schema.sql

echo "=== Granting permissions ==="
sudo -u postgres psql -d trading -c "GRANT ALL ON ALL TABLES IN SCHEMA public TO trading;"
sudo -u postgres psql -d trading -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO trading;"

echo "=== Done ==="
echo "Connection string: postgresql://trading:trading_dev_2026@localhost:5432/trading"
echo "Add to .env: DATABASE_URL=postgresql://trading:trading_dev_2026@localhost:5432/trading"
