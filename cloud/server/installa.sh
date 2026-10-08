#!/usr/bin/env bash
# Installazione del server italiano di Pengwin. Da lanciare UNA volta, come
# root, su un Ubuntu 24.04 appena creato:
#
#     sudo bash installa.sh
#     sudo bash installa.sh --genera-certificato   # solo se quello del PC non c'e'
#
# Cosa fa, nell'ordine:
#   1. fuso orario Europe/Rome (i turni seguono l'ora legale italiana);
#   2. aggiornamenti di sicurezza automatici;
#   3. firewall: entra solo SSH, esce tutto;
#   4. utente dedicato `pengwin`, con la stessa chiave SSH di chi installa;
#   5. cartella dei segreti ~/.betfair (700) con il modello di betfair.env;
#      il certificato e' quello gia' in uso sul PC, copiato li'. Solo con
#      --genera-certificato ne crea uno nuovo (da ricaricare su betfair.it);
#   6. codice dal branch `server-betfair` (o `quote-betfair`) e ambiente
#      Python isolato.
#
# NON scrive credenziali: App Key, utente e password li inserisci tu in
# ~/.betfair/betfair.env (vedi cloud/server/README.md).
#
# Rilanciarlo non fa danni: ogni passo controlla se e' gia' stato fatto.

set -euo pipefail

UTENTE=pengwin
CASA=/home/$UTENTE
REPO=https://github.com/Difelice-e/pengwin.git
BRANCH=${BRANCH:-server-betfair}     # se non esiste ancora, ripiega su quote-betfair

if [[ $EUID -ne 0 ]]; then
    echo "Va lanciato come root: sudo bash $0" >&2
    exit 1
fi

passo() { printf '\n=== %s\n' "$*"; }

passo "1/6 fuso orario"
timedatectl set-timezone Europe/Rome
timedatectl | grep -i 'time zone'

passo "2/6 pacchetti e aggiornamenti automatici"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get -yq upgrade
apt-get -yq install python3-venv python3-pip git curl openssl ufw unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

passo "3/6 firewall (solo SSH in ingresso)"
ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null
ufw status | head -5

passo "4/6 utente $UTENTE"
if ! id "$UTENTE" &>/dev/null; then
    adduser --disabled-password --gecos "" "$UTENTE"
fi
install -d -m 700 -o "$UTENTE" -g "$UTENTE" "$CASA/.ssh"
CHIAVI=""
for f in /root/.ssh/authorized_keys "/home/${SUDO_USER:-nessuno}/.ssh/authorized_keys"; do
    [[ -s $f ]] && CHIAVI=$f && break
done
if [[ -n $CHIAVI ]]; then
    install -m 600 -o "$UTENTE" -g "$UTENTE" "$CHIAVI" "$CASA/.ssh/authorized_keys"
    echo "chiave SSH copiata da $CHIAVI: entri con  ssh $UTENTE@<ip-del-server>"
    # Con una chiave in funzione, la password via SSH non serve piu' ed e' la
    # porta d'ingresso piu' attaccata. Senza chiave invece la si lascia: chiudere
    # l'unico accesso possibile vorrebbe dire restare fuori dal server.
    cat > /etc/ssh/sshd_config.d/10-pengwin.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
CONF
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
    echo "accesso SSH con password disattivato"
else
    echo "[!] nessuna chiave SSH trovata: l'accesso con password resta attivo."
    echo "    Aggiungi la chiave a $CASA/.ssh/authorized_keys e rilancia lo script."
fi

passo "5/6 cartella dei segreti"
SEGRETI=$CASA/.betfair
install -d -m 700 -o "$UTENTE" -g "$UTENTE" "$SEGRETI"
if [[ ! -f $SEGRETI/betfair.env ]]; then
    install -m 600 -o "$UTENTE" -g "$UTENTE" /dev/null "$SEGRETI/betfair.env"
    cat > "$SEGRETI/betfair.env" <<CONF
# Credenziali betfair.it. Questo file resta SOLO su questo server (permessi 600).
# Le stesse variabili che usi sul PC.
BETFAIR_APP_KEY=
BETFAIR_USERNAME=
BETFAIR_PASSWORD=
BETFAIR_CERT=$SEGRETI/client.crt
BETFAIR_KEY=$SEGRETI/client.key
# Supabase: la SECRET key (sb_secret_...). La publishable non puo' scrivere le
# tabelle del braccio reale. Dashboard Supabase -> Settings -> API Keys.
SUPABASE_URL=https://cshlvcfahevvdcdjlola.supabase.co
SUPABASE_KEY=
CONF
    echo "modello creato: $SEGRETI/betfair.env (da compilare)"
else
    echo "$SEGRETI/betfair.env esiste gia': non lo tocco"
    grep -q '^SUPABASE_KEY=' "$SEGRETI/betfair.env" || {
        printf '\n# Supabase: SECRET key (sb_secret_...)\nSUPABASE_URL=https://cshlvcfahevvdcdjlola.supabase.co\nSUPABASE_KEY=\n' >> "$SEGRETI/betfair.env"
        echo "aggiunte le righe SUPABASE_* a betfair.env (da compilare)"
    }
fi

passo "5b/6 certificato per il login non interattivo"
# Di norma si usa il certificato gia' collegato a betfair.it, copiato dal PC in
# ~/.betfair/client.crt e client.key. Generarne uno nuovo serve solo se quello
# vecchio non e' raggiungibile: va chiesto esplicitamente (--genera-certificato)
# e poi caricato su betfair.it, dove sostituisce il precedente.
if [[ -f $SEGRETI/client.crt && -f $SEGRETI/client.key ]]; then
    chown "$UTENTE:$UTENTE" "$SEGRETI"/client.*
    chmod 600 "$SEGRETI"/client.*
    echo "certificato presente (scade: $(openssl x509 -enddate -noout -in "$SEGRETI/client.crt" | cut -d= -f2))"
elif [[ ${1:-} == --genera-certificato ]]; then
    # RSA 2048 ed extendedKeyUsage = clientAuth, come da guida Betfair al login
    # non interattivo. La chiave privata nasce qui e non lascia il server.
    sudo -u "$UTENTE" bash -c "
        set -e
        umask 077
        cd '$SEGRETI'
        cat > openssl-client.cnf <<'CNF'
[ req ]
distinguished_name = dn
prompt = no
[ dn ]
CN = pengwin
[ ssl_client ]
basicConstraints = CA:FALSE
nsCertType = client
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
CNF
        openssl req -new -newkey rsa:2048 -nodes -keyout client.key -out client.csr \
            -config openssl-client.cnf
        openssl x509 -req -days 730 -in client.csr -signkey client.key -out client.crt \
            -extfile openssl-client.cnf -extensions ssl_client 2>/dev/null
        rm -f client.csr
    "
    echo "certificato NUOVO generato (scade: $(openssl x509 -enddate -noout -in "$SEGRETI/client.crt" | cut -d= -f2))"
    echo "Va caricato su betfair.it, dove sostituisce quello del PC. Contenuto (pubblico):"
    cat "$SEGRETI/client.crt"
else
    echo "[!] certificato non ancora presente in $SEGRETI."
    echo "    Copia dal PC client.crt e client.key (vedi la fine di questo script),"
    echo "    poi rilancia: sudo bash installa.sh"
fi

passo "6/6 codice e ambiente Python"
if [[ ! -d $CASA/pengwin/.git ]]; then
    sudo -u "$UTENTE" git clone -q -b "$BRANCH" "$REPO" "$CASA/pengwin" 2>/dev/null \
        || sudo -u "$UTENTE" git clone -q -b quote-betfair "$REPO" "$CASA/pengwin"
else
    sudo -u "$UTENTE" git -C "$CASA/pengwin" pull -q --ff-only || true
fi
sudo -u "$UTENTE" git -C "$CASA/pengwin" log -1 --format='branch %D — %h %s'
if [[ ! -x $CASA/venv/bin/python ]]; then
    sudo -u "$UTENTE" python3 -m venv "$CASA/venv"
fi
sudo -u "$UTENTE" "$CASA/venv/bin/pip" install -q --upgrade pip
sudo -u "$UTENTE" "$CASA/venv/bin/pip" install -q -r "$CASA/pengwin/cloud/requirements.txt"
sudo -u "$UTENTE" "$CASA/venv/bin/python" -c "import requests, pandas, scipy; print('python ok')"

IP=$(curl -s -m 10 https://api.ipify.org || echo "?")
PROVA=$CASA/pengwin/cloud/server/betfair_prova.py
[[ -f $PROVA ]] || PROVA="$CASA/betfair_prova.py   (copialo tu: non e' ancora nel repo)"
cat <<FINE

Installazione completata. IP pubblico del server: $IP

Prossimi passi:
  1. dal PC di casa, in PowerShell, copia il certificato gia' in uso
     (i percorsi sono quelli delle variabili BETFAIR_CERT e BETFAIR_KEY):
       scp <percorso>\\client.crt $UTENTE@$IP:.betfair/client.crt
       scp <percorso>\\client.key $UTENTE@$IP:.betfair/client.key
  2. entra come $UTENTE ( ssh $UTENTE@$IP ) e compila  ~/.betfair/betfair.env
     con App Key, utente e password;
  3. chmod 600 ~/.betfair/*  e prova di connessione, sola lettura:
       ~/venv/bin/python $PROVA connessione
FINE
