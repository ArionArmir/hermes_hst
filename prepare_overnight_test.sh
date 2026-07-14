#!/bin/bash
# Prepara il sistema per un test overnight con i simboli attualmente
# configurati in config/trading_params.yaml. Non avvia/ferma processi né
# tocca Redis da solo: mostra stato e istruzioni, chiede conferma prima di
# ogni azione che modifica file, e non fa mai nulla di distruttivo in
# automatico.
#
# Nota sui log: qui si usa sempre `mv` per archiviarli, mai troncamento
# in-place — troncare un file di log mentre un processo lo tiene aperto lo
# ha fatto crashare in passato in questo progetto.

set -e
cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || true

echo "=== Simboli configurati (config/trading_params.yaml) ==="
python3 -c "
import yaml
with open('config/trading_params.yaml') as f:
    config = yaml.safe_load(f)
print(config['symbols'])
"

echo ""
echo "=== Stato processi attuale ==="
python3 -c "
import sys
sys.path.insert(0, 'dashboard')
from utils import process_manager as pm
for s in ('engine', 'inference', 'sentiment'):
    print(f'{s}: {pm.status(s)}')
"

echo ""
read -p "Archiviare i log attuali in logs/archive/<timestamp>/? (s/N): " archive_answer
if [[ "$archive_answer" == "s" || "$archive_answer" == "S" ]]; then
    timestamp=$(date +%Y%m%d_%H%M%S)
    mkdir -p "logs/archive/$timestamp"
    mv logs/*.log "logs/archive/$timestamp/" 2>/dev/null || true
    echo "Log spostati in logs/archive/$timestamp/"
fi

echo ""
read -p "Rimuovere data/trades_history.csv per partire con uno storico vuoto? (s/N): " trades_answer
if [[ "$trades_answer" == "s" || "$trades_answer" == "S" ]]; then
    rm -f data/trades_history.csv
    echo "trades_history.csv rimosso (verrà ricreato alla prima chiusura)"
fi

echo ""
echo "Redis NON viene toccato da questo script."
echo "Per chiudere le posizioni aperte prima del test, usa il pulsante"
echo "\"Reset posizioni (emergenza)\" dalla dashboard (pagina Controllo):"
echo "passa dal normale flusso di chiusura dell'Engine, non un FLUSHALL."

echo ""
echo "=== Prossimi passi (manuali, un terminale per processo) ==="
echo "1. ./start.sh engine"
echo "2. ./start.sh inference"
echo "3. ./start.sh sentiment"
echo "4. streamlit run dashboard/app.py"
echo ""
echo "Monitora in tempo reale: tail -f logs/trading_\$(date +%Y-%m-%d).log"
echo "Verifica al mattino: python3 verify_overnight.py"
