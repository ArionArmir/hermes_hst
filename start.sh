#!/bin/bash
cd ~/hermes_hft
source venv/bin/activate

# Carica variabili d'ambiente da .env (senza errori)
if [ -f .env ]; then
    set -a; source .env; set +a
    echo "✅ Variabili d'ambiente caricate"
fi

# Verifica che Redis sia in esecuzione
if ! pgrep -x "redis-server" > /dev/null; then
    echo "⚠️ Redis non in esecuzione. Avvio..."
    sudo service redis-server start
    sleep 2
fi

echo "🚀 Avvio Trading Engine sui core 0-1..."
taskset -c 0,1 python -m src.engine.main
