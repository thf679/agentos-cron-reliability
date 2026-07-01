#!/usr/bin/env bash
# backup-hermes.sh — Full Hermes config backup for disaster recovery
# Archives: config.yaml, .env, skills/, okf_memory/ (git bundle), cron/jobs.json
# Output: ~/workspace/backups/hermes-config/hermes-backup-YYYYMMDD-HHMMSS.tar.gz
# Retention: keep last 3 backups

set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/workspace/.hermes}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/workspace/backups/hermes-config}"
TIMESTAMP=$(date -u +%Y%m%d-%H%M%S)
ARCHIVE="${BACKUP_DIR}/hermes-backup-${TIMESTAMP}.tar.gz"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "🔄 Hermes backup ${TIMESTAMP}"

# ── Copy essential files to tmpdir ──
mkdir -p "${TMPDIR}/hermes"

# Config + secrets
cp "${HERMES_HOME}/config.yaml" "${TMPDIR}/hermes/" 2>/dev/null || echo "⚠️ no config.yaml"
cp "${HERMES_HOME}/.env" "${TMPDIR}/hermes/" 2>/dev/null || echo "⚠️ no .env"

# Skills (personas)
if [ -d "${HERMES_HOME}/skills" ]; then
  cp -r "${HERMES_HOME}/skills" "${TMPDIR}/hermes/"
else
  echo "⚠️ no skills/"
fi

# Cron job definitions
if [ -f "${HERMES_HOME}/cron/jobs.json" ]; then
  cp "${HERMES_HOME}/cron/jobs.json" "${TMPDIR}/hermes/"
else
  echo "⚠️ no cron/jobs.json"
fi

# Claude Code project context
if [ -f "${HERMES_HOME}/CLAUDE.md" ]; then
  cp "${HERMES_HOME}/CLAUDE.md" "${TMPDIR}/hermes/"
fi

# Agent workspaces (only the shared scripts, not per-agent output)
if [ -d "${HERMES_HOME}/agents/_shared" ]; then
  cp -r "${HERMES_HOME}/agents/_shared" "${TMPDIR}/hermes/agents/"
fi

# OKF knowledge base as git bundle (includes full phase/project history)
if [ -d "${HERMES_HOME}/okf_memory" ]; then
  cd "${HERMES_HOME}/okf_memory"
  git bundle create "${TMPDIR}/hermes/okf_memory.bundle" --all 2>/dev/null || echo "⚠️ git bundle failed"
else
  echo "⚠️ no okf_memory/"
fi

# ── AgentOS Mission Control Dashboard source files ──
DASHBOARD_SRC="${HERMES_HOME}/projects/repo-003/src"
DASHBOARD_BACKUP="${TMPDIR}/hermes/dashboard/"
if [ -d "$DASHBOARD_SRC" ]; then
  mkdir -p "$DASHBOARD_BACKUP"
  for f in refresh.js index.html dashboard.html tokens.css components.js; do
    if [ -f "${DASHBOARD_SRC}/${f}" ]; then
      cp "${DASHBOARD_SRC}/${f}" "${DASHBOARD_BACKUP}/"
      echo "  ✅ dashboard/${f}"
    else
      echo "  ⚠️ dashboard/${f} — not found"
    fi
  done
  # Also back up tests and CI config
  if [ -f "${HERMES_HOME}/projects/repo-003/Makefile" ]; then
    cp "${HERMES_HOME}/projects/repo-003/Makefile" "${DASHBOARD_BACKUP}/"
    echo "  ✅ dashboard/Makefile"
  fi
  if [ -d "${HERMES_HOME}/projects/repo-003/tests" ]; then
    cp -r "${HERMES_HOME}/projects/repo-003/tests" "${DASHBOARD_BACKUP}/"
    echo "  ✅ dashboard/tests/"
  fi
  if [ -f "${HERMES_HOME}/projects/repo-003/.github/workflows/ci.yml" ]; then
    mkdir -p "${DASHBOARD_BACKUP}/.github/workflows"
    cp "${HERMES_HOME}/projects/repo-003/.github/workflows/ci.yml" "${DASHBOARD_BACKUP}/.github/workflows/"
    echo "  ✅ dashboard/.github/workflows/ci.yml"
  fi
else
  echo "⚠️ no dashboard src/"
fi

# ── Create archive ──
mkdir -p "${BACKUP_DIR}"
cd "${TMPDIR}"
tar czf "${ARCHIVE}" hermes/

SIZE=$(du -h "${ARCHIVE}" | cut -f1)
echo "✅ Backup: ${ARCHIVE} (${SIZE})"

# ── Retention: keep last 14 ──
cd "${BACKUP_DIR}"
ls -1t hermes-backup-*.tar.gz 2>/dev/null | tail -n +4 | while read -r old; do
  rm -f "$old"
  echo "🗑️ Removed old: $old"
done

echo "📦 Backups on disk: $(ls -1 hermes-backup-*.tar.gz 2>/dev/null | wc -l)"
