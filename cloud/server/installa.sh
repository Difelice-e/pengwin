#!/usr/bin/env bash
# Installazione del server italiano di Pengwin. Da lanciare UNA volta, come
# root, su un Ubuntu 24.04 appena creato:
#
#     sudo bash installa.sh
#
# Cosa fa, nell'ordine:
#   1. fuso orario Europe/Rome (i turni seguono l'ora legale italiana);
#   2. aggiornamenti di sicurezza automatici;
#   3. firewall: entra solo SSH, esce tutto;
#   4. utente dedicato `pengwin`, con la stessa chiave SSH di chi installa;
#   5. cartella dei segreti ~/.betfair (700) con il modello di betfair.env,
#      e un certificato nuovo per il login non interattivo, generato qui;
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
CONF
    echo "modello creato: $SEGRETI/betfair.env (da compilare)"
else
    echo "$SEGRETI/betfair.env esiste gia': non lo tocco"
fi

passo "5b/6 certificato per il login non interattivo"
# Il certificato nasce qui e la chiave privata non lascia mai il server. Su
# betfair.it si carica solo client.crt, che e' pubblico. Formato e estensioni
# sono quelli della guida Betfair al login non interattivo (RSA 2048,
# extendedKeyUsage = clientAuth). Se ci sono gia' entrambi, non si tocca nulla.
if [[ -f $SEGRETI/client.crt && -f $SEGRETI/client.key ]]; then
    echo "certificato gia' presente: non lo rigenero"
else
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
    echo "certificato generato in $SEGRETI (scade: $(openssl x509 -enddate -noout -in "$SEGRETI/client.crt" | cut -d= -f2))"
    echo "Da caricare su betfair.it: SOLO client.crt. Il contenuto e' qui sotto, e' pubblico:"
    cat "$SEGRETI/client.crt"
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
  1. su betfair.it carica il certificato $SEGRETI/client.crt
     (stampato sopra: e' pubblico, la chiave privata resta qui);
  2. entra come $UTENTE ( ssh $UTENTE@$IP ) e compila  ~/.betfair/betfair.env
     con App Key, utente e password;
  3. prova di connessione, sola lettura:
       ~/venv/bin/python $PROVA connessione
FINE
