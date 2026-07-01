#!/usr/bin/env bash
# cleanup-logs.sh — Monthly log retention (30-day, permanent deletion)
# Run: bash cleanup-logs.sh

set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/workspace/.hermes}"
DB="$HERMES_HOME/agent-logs.db"
RETENTION_DAYS=30

python3 << PYEOF
import sqlite3, os, sys
from datetime import datetime, timezone, timedelta

db_path = os.path.expanduser('$DB')
retention = $RETENTION_DAYS

cutoff = (datetime.now(timezone.utc) - timedelta(days=retention)).isoformat()

conn = sqlite3.connect(db_path)

# Create db/table if missing (safe to run fresh)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('''
    CREATE TABLE IF NOT EXISTS agent_logs (
        id TEXT PRIMARY KEY,
        agent_name TEXT NOT NULL,
        task_description TEXT NOT NULL,
        model_used TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        repo TEXT,
        branch TEXT,
        pr_number TEXT,
        commit_sha TEXT
    )
''')
conn.execute('CREATE INDEX IF NOT EXISTS idx_agent_name ON agent_logs(agent_name)')
conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON agent_logs(status)')
conn.execute('CREATE INDEX IF NOT EXISTS idx_created_at ON agent_logs(created_at DESC)')
conn.execute('CREATE INDEX IF NOT EXISTS idx_repo ON agent_logs(repo)')

# Migrate from old schema if needed
existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_logs)")}
for col, col_type in [("repo", "TEXT"), ("branch", "TEXT"), ("pr_number", "TEXT"), ("commit_sha", "TEXT")]:
    if col not in existing_cols:
        conn.execute(f"ALTER TABLE agent_logs ADD COLUMN {col} {col_type}")

# Count before
total_before = conn.execute('SELECT COUNT(*) FROM agent_logs').fetchone()[0]

# Delete old rows
cursor = conn.execute('DELETE FROM agent_logs WHERE created_at < ?', (cutoff,))
deleted = cursor.rowcount
conn.commit()
conn.execute('VACUUM')

total_after = conn.execute('SELECT COUNT(*) FROM agent_logs').fetchone()[0]
conn.close()

print(f"Monthly log cleanup ran: deleted {deleted} rows, {total_after} remaining (retention: {retention} days).")
PYEOF
