#!/usr/bin/env bash
# Attiva le fotografie delle quote (ogni 2 ore), la cattura della chiusura
# (ogni 5 minuti) e la contabilizzazione del Goal/No Goal su carta (ogni giorno). Da lanciare come root DOPO che la prova di connessione e'
# riuscita e ~pengwin/.betfair/betfair.env contiene anche SUPABASE_KEY.
#
#     ssh root@<ip>      (l'utente pengwin non ha password ne' sudo)
#     bash /home/pengwin/pengwin/cloud/server/attiva_timer.sh
#
# Nessun timer piazza ordini: leggono quote e aggiornano giocate gia'
# fatte. Lo stato si controlla con:  systemctl list-timers 'pengwin-*'
# e i log con:  journalctl -u pengwin-chiusura -n 50
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Va lanciato come root" >&2; exit 1; }
QUI=$(cd "$(dirname "$0")" && pwd)
ENV=/home/pengwin/.betfair/betfair.env
grep -q '^SUPABASE_KEY=sb_secret_' "$ENV" || {
    echo "[!] in $ENV manca SUPABASE_KEY=sb_secret_...: i timer non potrebbero scrivere" >&2
    exit 1
}
install -m 644 "$QUI"/systemd/pengwin-*.service "$QUI"/systemd/pengwin-*.timer /etc/systemd/system/
systemctl daemon-reload
# Tutti i timer presenti in systemd/: rilanciare lo script dopo un `git pull`
# che ne aggiunge uno nuovo li attiva, quelli gia' attivi restano come sono.
for t in "$QUI"/systemd/pengwin-*.timer; do
    systemctl enable --now "$(basename "$t")"
done
systemctl list-timers 'pengwin-*' --no-pager
