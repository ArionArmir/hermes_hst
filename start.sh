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

SERVICE="${1:-engine}"

case "$SERVICE" in
    engine)
        MODULE="src.engine.main"
        ;;
    inference)
        MODULE="src.inference.main"
        ;;
    sentiment)
        MODULE="src.sentiment.ollama_client"
        ;;
    *)
        echo "❌ Servizio sconosciuto: '$SERVICE' (usa: engine | inference | sentiment)"
        exit 1
        ;;
esac

# Termina eventuali istanze residue dello stesso modulo, anche in altri terminali,
# per evitare che un vecchio processo resti in RAM con codice non aggiornato
STALE_PIDS=$(pgrep -f "python -m ${MODULE}")
if [ -n "$STALE_PIDS" ]; then
    echo "⚠️ Istanza residua di ${MODULE} trovata (PID: $STALE_PIDS), la termino..."
    kill $STALE_PIDS
    sleep 1
    STILL_ALIVE=$(pgrep -f "python -m ${MODULE}")
    if [ -n "$STILL_ALIVE" ]; then
        echo "⚠️ Non risponde al SIGTERM, forzo la chiusura (PID: $STILL_ALIVE)..."
        kill -9 $STILL_ALIVE
        sleep 1
    fi
fi

echo "🚀 Avvio ${MODULE}..."
if [ "$SERVICE" = "engine" ]; then
    exec taskset -c 0,1 python -m "$MODULE"
else
    exec python -m "$MODULE"
fi
