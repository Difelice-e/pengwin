#!/usr/bin/env bash
# Attiva i timer del server: turno automatico (venerdi' 19:00, martedi' 14:00),
# fotografie delle quote (ogni 2 ore), chiusura (ogni 5 minuti), esiti delle
# giocate reali (ogni 30 minuti) e del Goal/No Goal su carta (ogni giorno).
# Imposta anche il riavvio automatico notturno dopo gli aggiornamenti. Da lanciare come root DOPO che la prova di connessione
# e' riuscita e ~pengwin/.betfair/betfair.env contiene anche SUPABASE_KEY.
#
#     ssh root@<ip>      (l'utente pengwin non ha password ne' sudo)
#     bash /home/pengwin/pengwin/cloud/server/attiva_timer.sh
#
# Solo pengwin-turno piazza ordini, e solo con l'interruttore acceso dalla
# pagina privata (config_reale.attivo). Gli altri leggono quote, saldo ed esiti. Lo stato si controlla con:  systemctl list-timers 'pengwin-*'
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

# Riavvio automatico dopo gli aggiornamenti di sicurezza, alle 05:00: nessuna
# partita, nessun turno, nessuna chiusura a quell'ora. Cosi' "System restart
# required" non resta in sospeso e non serve un comando a mano.
cat > /etc/apt/apt.conf.d/52pengwin-riavvio <<'CONF'
// Pengwin: riavvio automatico dopo gli aggiornamenti, di notte (attiva_timer.sh)
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-WithUsers "true";
Unattended-Upgrade::Automatic-Reboot-Time "05:00";
CONF
echo "riavvio automatico dopo gli aggiornamenti: alle 05:00"

systemctl list-timers 'pengwin-*' --no-pager
