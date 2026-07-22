#!/usr/bin/env bash
# Backup dei dati NON ricostruibili: stato del carry paper, tripwire, ledger
# investimenti, db segnali, liquidazioni raccolte. I parquet di mercato sono
# riscaricabili e restano fuori. Destinazione: disco Windows (/mnt/c),
# che sopravvive a un reinstall di WSL. Tiene le ultime 8 copie.
set -euo pipefail
DEST="/mnt/c/Users/Alexbi/hermes_backup"
mkdir -p "$DEST"
cd "$(dirname "$0")/.."
tar czf "$DEST/hermes_prezioso_$(date +%Y%m%d).tar.gz" \
    --ignore-failed-read \
    data/carry_paper data/invest data/liquidations data/hermes.db \
    docs/experiment_registry.jsonl docs/rapporti 2>/dev/null || true
ls -t "$DEST"/hermes_prezioso_*.tar.gz | tail -n +9 | xargs -r rm
echo "backup: $(ls -t "$DEST"/hermes_prezioso_*.tar.gz | head -1)"
