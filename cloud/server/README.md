# Server italiano — installazione, prove e braccio reale su betfair.it

Betfair accetta login e ordini solo da IP localizzati in Italia: dai runner di
GitHub (Stati Uniti) il login esce `BETTING_RESTRICTED_LOCATION`. Il server
serve a questo e solo a questo: prezzi betfair.it, giocate reali, chiusure.
Ingest di risultati e xG e il braccio su carta restano su GitHub Actions.

Le sezioni 1-5 sono la messa in opera (una volta). La 7 e la 8 sono il turno reale,
che gioca **solo Premier League e Serie A** (decisione dell'8/10/2026); le
fotografie delle quote restano sui cinque campionati.

## 0. Il server

- Provider con data center **in Italia**. Evitare data center in Germania,
  Francia, Olanda e Stati Uniti: Betfair li blocca.
  Scelta del 8/10/2026: Aruba Cloud VPS O1I2 (1 vCPU, 2 GB, 40 GB).
- **Ubuntu 24.04**, accesso con **chiave SSH**.
- IP **fisso**: se la prova passa, si tiene quel server con quell'IP.

## 1. Installazione (una volta)

```bash
ssh root@<ip>
curl -fsSLO https://raw.githubusercontent.com/Difelice-e/pengwin/server-betfair/cloud/server/installa.sh
sudo bash installa.sh
```

Lo script imposta fuso orario e firewall, crea l'utente `pengwin` e scarica il
codice. Non tocca credenziali.

## 2. Il certificato già in uso

Si usa quello già collegato a betfair.it dal 10/9, che sta sul PC di casa (percorsi
nelle variabili `BETFAIR_CERT` e `BETFAIR_KEY`). Da PowerShell sul PC:

```powershell
scp <percorso>\client.crt pengwin@<ip>:.betfair/client.crt
scp <percorso>\client.key pengwin@<ip>:.betfair/client.key
```

Solo se quel certificato non fosse raggiungibile:
`sudo bash installa.sh --genera-certificato` ne crea uno nuovo sul server. Va poi
caricato su betfair.it → I miei dati → accesso non interattivo, dove sostituisce
quello del PC.

## 3. Credenziali

```bash
ssh pengwin@<ip>
nano ~/.betfair/betfair.env      # App Key, utente, password
chmod 600 ~/.betfair/*
```

La **Delayed App Key** si rilegge da qualunque browser collegato a betfair.it,
con l'Accounts API Demo Tool (`getDeveloperAppKeys`). Il file ha permessi 600 e
non va mai copiato altrove.

## 4. Prova di connessione — sola lettura

```bash
~/venv/bin/python ~/pengwin/cloud/server/betfair_prova.py connessione
```

| Esito | Significato |
|---|---|
| `CONNESSIONE OK` | Betfair accetta il server: si tiene |
| uscita 3, `BETTING_RESTRICTED_LOCATION` | l'IP non passa: si distrugge il server e si cancella il disco |
| uscita 4 | credenziali, certificato o chiave: il messaggio dice quale |

## 5. Prova d'ordine

```bash
~/venv/bin/python ~/pengwin/cloud/server/betfair_prova.py ordine            # anteprima
~/venv/bin/python ~/pengwin/cloud/server/betfair_prova.py ordine --esegui   # vera
```

Punta 2,00 € a quota 1000 sul favorito di una partita dei cinque campionati
che inizia fra almeno 2 ore, e annulla subito. Nessuno banca un favorito a
1000, quindi l'ordine non viene abbinato. Rischio massimo: i 2 €, solo nel caso
assurdo in cui venisse abbinato. Servono almeno 2 € sul conto.

Lo script chiede di scrivere `PROVA` prima di inviare. Durante la sezione
critica ignora Ctrl-C, ritenta l'annullamento fino a tre volte, poi chiede a
Betfair lo stato dell'ordine e lo confronta con il saldo.

| Uscita | Significato |
|---|---|
| 0 `PROVA ORDINE OK` | il conto può piazzare e annullare da questo server |
| 2 | rifiutata dallo script, niente inviato |
| 5 | respinto da Betfair (es. `PERMISSION_DENIED`): niente esposto |
| **6** | **annullamento non confermato: controllare subito betfair.it → Le mie scommesse** |

Richieste e risposte (mai le credenziali) in `~/.betfair/prove/*.jsonl`.

## 6. Dopo

- **Confermato dal supporto Betfair (8/10/2026, richiesta 57870):** per i conti
  italiani l'attivazione della **Live App Key è gratuita**, e piazzare scommesse
  reali con la Delayed durante i test è consentito, anzi incoraggiato.
- Dopo la prova d'ordine: richiedere la Live su developer.betfair.com → Exchange API
  → For My Personal Betting (servono test completati e conto verificato KYC). La
  Live non si può usare in sola lettura: va attivata quando si comincia a giocare.
- La scadenza del certificato la stampa `installa.sh`: annotarla.

## 7. Braccio reale — una volta, prima del primo turno

1. **Supabase → SQL Editor → New query**: incollare `cloud/sql/braccio_reale.sql`
   e lanciarlo. Crea `giocate_reali`, `ordini_log`, `quote_snapshot` e scrive la
   **pre-registrazione** `braccio_reale`. Va riletta prima: dopo non si cambia.
2. **Secret key** di Supabase (Settings → API Keys, `sb_secret_...`) in
   `~/.betfair/betfair.env` alla riga `SUPABASE_KEY=`. La publishable non può
   scrivere le tabelle del braccio reale, che non sono leggibili da fuori.
3. Timer (fotografie ogni 2 ore, chiusure ogni 5 minuti — nessuno dei due gioca):
   `sudo bash ~pengwin/pengwin/cloud/server/attiva_timer.sh`

## 8. Il turno

**Sempre dentro `tmux`**: se la connessione SSH cade, il programma continua a girare
invece di essere chiuso a metà fra l'invio di un ordine e la sua registrazione.

```bash
tmux new -A -s pengwin         # rientrando dopo una disconnessione: stesso comando
```

Sul PC conviene un alias con keepalive, in `%USERPROFILE%\.ssh\config`:

```
Host pengwin
    HostName 217.61.57.19
    User pengwin
    ServerAliveInterval 30
    ServerAliveCountMax 4
```

Sul server, come `pengwin`:

```bash
cd ~/pengwin && git pull && cd cloud
~/venv/bin/python -m src.reale.turno --consenti-dati-vecchi            # anteprima
~/venv/bin/python -m src.reale.turno --invia --consenti-dati-vecchi    # gioca, chiede GIOCA
```

`--consenti-dati-vecchi` serve solo quando l'ultima giornata giocata è di oltre
10 giorni fa per una **sosta** (come il 9/10). In un weekend normale non si mette:
se il programma rifiuta, è un buco nei dati e il turno si salta.

L'anteprima non scrive niente: saldo, modello, partite trovate su Betfair e
valutate, giocate con quota, edge e puntata. `--invia` rifà tutto, chiede di
scrivere `GIOCA`, registra le decisioni e poi piazza (LIMIT alla quota letta,
LAPSE). I prezzi valgono 10 minuti: se si conferma dopo, rifiuta e si rilancia.

| Uscita | Significato |
|---|---|
| 0 | tutto inviato (o anteprima pulita) |
| 2 | rifiutato: turno già giocato, dati vecchi, pre-registrazione mancante, conferma non data, controlli fissi |
| 3 | posizione geografica rifiutata da Betfair |
| 4 | inviato con giocate **respinte o incerte**: guardare `giocate_reali` e betfair.it |

Se si interrompe a metà: `--invia --riprendi` invia solo le giocate rimaste
`da_piazzare` o `incerta`, dopo aver chiesto a Betfair quali ordini esistono già.
Non manda mai due volte lo stesso ordine.

Log di ogni ordine: tabella `ordini_log` e copia locale in `~/.betfair/ordini/`.

Test senza rete, dalla cartella `cloud/`: `python tests/betfair_prova.py` e `python tests/reale.py`.
