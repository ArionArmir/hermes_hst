#!/bin/bash
# Installa le unit systemd UTENTE di Hermes (~/.config/systemd/user/).
# Idempotente: rilanciarlo aggiorna le unit e ricarica il daemon.
#
# Prerequisito su WSL2: systemd abilitato in /etc/wsl.conf:
#     [boot]
#     systemd=true
# poi da PowerShell: wsl --shutdown, e riaprire il terminale.
#
# Dopo l'installazione:
#   systemctl --user start hermes-engine hermes-inference hermes-sentiment
# Avvio automatico al boot senza login (una volta sola):
#   sudo loginctl enable-linger $USER
set -e
cd "$(dirname "$0")"

if [ ! -d /run/systemd/system ]; then
    echo "❌ systemd non è attivo su questo sistema."
    echo "   Su WSL2: aggiungi 'systemd=true' nella sezione [boot] di /etc/wsl.conf,"
    echo "   poi esegui 'wsl --shutdown' da PowerShell e riapri il terminale."
    exit 1
fi

UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

for unit in systemd/hermes-*.service; do
    cp "$unit" "$UNIT_DIR/"
    echo "✅ installata $(basename "$unit")"
done

systemctl --user daemon-reload
systemctl --user enable hermes-engine hermes-inference hermes-sentiment hermes-dashboard
echo ""
echo "✅ Unit abilitate. Comandi utili:"
echo "   systemctl --user start hermes-engine hermes-inference hermes-sentiment"
echo "   systemctl --user status hermes-engine"
echo "   journalctl --user -u hermes-engine -f"
echo ""
if ! loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
    echo "ℹ️  Per l'avvio automatico al boot (senza login): sudo loginctl enable-linger $USER"
fi
