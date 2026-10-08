# Server italiano — installazione e prove con Betfair

Betfair accetta login e ordini solo da IP localizzati in Italia: dai runner di
GitHub (Stati Uniti) il login esce `BETTING_RESTRICTED_LOCATION`. Il server
serve a questo e solo a questo. Ingest, contabilizzazione e dashboard restano
su GitHub Actions.

Queste prove **non giocano il modello** e **non scrivono su Supabase**. Dicono
due cose: se Betfair accetta il server, e se il conto può piazzare ordini con
la chiave attuale.

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

Test senza rete: `python tests/betfair_prova.py` dalla cartella `cloud/`.
