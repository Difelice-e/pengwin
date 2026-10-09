#!/usr/bin/env bash
# Lancia un programma Python del progetto con la **Live App Key**.
#
#     bash ~/pengwin/cloud/server/con_live.sh -m src.reale.turno --consenti-dati-vecchi
#     bash ~/pengwin/cloud/server/con_live.sh server/betfair_prova.py connessione
#
# Le due chiavi (Betfair, «Application Keys», e mail di attivazione del 9/10/2026):
#   - Live    -> solo per giocare: il turno reale e la chiusura delle giocate
#                reali. Non e' ammessa in sola lettura.
#   - Delayed -> tutto il resto: fotografie delle quote e Goal/No Goal su carta.
#                E' la chiave prevista per la sola lettura e la simulazione.
#
# La Delayed resta in ~/.betfair/betfair.env, come prima. La Live sta in un file
# a parte, ~/.betfair/live.env (permessi 600), con una riga sola:
#
#     BETFAIR_APP_KEY=<la Live App Key>
#
# Le variabili gia' nell'ambiente hanno la precedenza su betfair.env (vedi
# src/reale/api.py), quindi qui basta esportare la Live prima di avviare Python.
# Il file non viene eseguito: se ne legge solo la riga BETFAIR_APP_KEY.
set -euo pipefail

LIVE="${PENGWIN_LIVE_ENV:-$HOME/.betfair/live.env}"
PYTHON="${PENGWIN_PYTHON:-$HOME/venv/bin/python}"
CLOUD="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $# -eq 0 ]]; then
    echo "uso: bash con_live.sh -m src.reale.turno [opzioni]  |  bash con_live.sh server/betfair_prova.py connessione" >&2
    exit 64
fi
if [[ ! -r "$LIVE" ]]; then
    echo "[!] manca $LIVE: crealo con la riga BETFAIR_APP_KEY=<Live App Key>, poi chmod 600" >&2
    exit 1
fi
if [[ "$(stat -c %a "$LIVE")" != "600" ]]; then
    echo "[!] $LIVE deve avere permessi 600: chmod 600 $LIVE" >&2
    exit 1
fi

chiave="$(grep -m1 '^BETFAIR_APP_KEY=' "$LIVE" | cut -d= -f2- | tr -d "\"' \r" || true)"
if [[ -z "$chiave" ]]; then
    echo "[!] $LIVE non contiene la riga BETFAIR_APP_KEY=<Live App Key>" >&2
    exit 1
fi
delayed="$(grep -m1 '^BETFAIR_APP_KEY=' "$HOME/.betfair/betfair.env" 2>/dev/null | cut -d= -f2- | tr -d "\"' \r" || true)"
if [[ "$chiave" == "$delayed" ]]; then
    echo "[!] in $LIVE c'e' la stessa chiave di betfair.env (la Delayed): metti la Live" >&2
    exit 1
fi

export BETFAIR_APP_KEY="$chiave"
cd "$CLOUD"
echo "(Live App Key ...${chiave: -4})" >&2
exec "$PYTHON" "$@"
