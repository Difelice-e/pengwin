# Pengwin — versione cloud

Migrazione del motore fuori dal PC di casa. Aggiornato al **4 settembre 2026**.

## Stato

| Componente | Stato |
|---|---|
| Rete verso football-data.co.uk | **OK** — testato, 5 leghe + fixtures |
| Rete verso Understat | **OK** — testato, endpoint nuovo (vedi sotto) |
| Rete verso Supabase | **OK** — risolto in questa sessione (prima 403 `host_not_allowed`) |
| `sql/schema.sql` eseguito sul progetto | **FATTO** — 4/9/2026, RLS attivo su 7 tabelle |
| `src/db/client.py` su API REST | **OK** — riscritto, psycopg2 abbandonato |
| Dati caricati | **OK** — 5.355 partite, 7.008 righe xG, 47 fixture |
| Modello Dixon-Coles + xG | **PORTATO E VERIFICATO** — vedi sotto |
| Ledger storico migrato | **FATTO** — 51 previsioni + 51 giocate |
| Ledger storico migrato | **DA FARE** — dipende dal push GitHub |

## Installazione

```bash
pip install -r requirements.txt
export SUPABASE_URL=https://cshlvcfahevvdcdjlola.supabase.co
export SUPABASE_KEY=sb_publishable_KPLnF6VmHPgBC7uEOA7GyA_AUUffl5D
```

## Passo 1 — creare lo schema (gia' eseguito il 4/9/2026)

Dashboard Supabase → **SQL Editor** → *New query* → incollare tutto
`sql/schema.sql` → **Run**. Idempotente: rieseguirlo non fa danni.
Supabase avvisa che lo script "crea tabelle senza RLS": e' un falso allarme,
l'RLS lo abilita la sezione 9 dello script stesso. Scegliere *Run without RLS*
e verificare dopo con la query in fondo a questo README.

Verifica:

```bash
python -m src.db.client
# OK  ok — schema presente
```

## Passo 2 — ingest

```bash
# storico (piu' stagioni) + partite in programma
python -m src.ingest.football_data --stagioni 2324 2425 2526 2627 --fixtures --carica

# xG per partita (2026 = stagione 2026/27)
python -m src.ingest.understat --stagioni 2024 2025 2026 --carica
```

Senza `--carica` scarica soltanto in `data/raw/` e `data/xg/` e stampa il riepilogo.
Entrambi gli script sono **idempotenti**: fanno upsert sulla chiave naturale, quindi
si possono rilanciare a ogni giornata senza duplicare nulla e recuperando le righe
che la fonte aveva pubblicato in ritardo.

## Quote: apertura e chiusura NON sono la stessa colonna

Nei CSV di football-data le colonne **senza** `C` sono le quote di **apertura**
(`AvgH`, `B365H`, `MaxH`); quelle di **chiusura** hanno la C interna
(`AvgCH`, `B365CH`, `MaxCH`, `PSCH`, `BFECH`). Il criterio di successo del
progetto e' battere le **chiusure**: confonderle non produce alcun errore
visibile, produce un backtest ottimista e un CLV privo di significato.

Nel database: `q_*` = chiusura, `q_ap_*` = apertura. Nessun fallback fra le due.

**Pinnacle non e' piu' disponibile.** Dal 2026/27 football-data non pubblica piu'
le colonne `PS*`, e gia' nel 2025/26 la copertura era del 51%. Il benchmark passa
a **Betfair Exchange** (`q_bfe_*`, colonne `BFEC*`), che la specifica indica come
altrettanto severo. Copertura misurata:

| Stagione | AvgC | MaxC | B365C | BFEC | PSC | xG football-data |
|---|---|---|---|---|---|---|
| 2023/24 | 100% | 100% | 100% | 0% | 100% | — |
| 2024/25 | 100% | 100% | 100% | 100% | 100% | — |
| 2025/26 | 100% | 100% | 100% | 93% | 51% | — |
| 2026/27 | 100% | 100% | 100% | 100% | 0% | 100% |

Per un backtest che copra tutte e quattro le stagioni con un'unica serie
coerente, l'unico candidato e' `q_avg_*` (media di mercato, da spogliare del
margine). `q_bfe_*` e' piu' severo ma parte dal 2024/25.

**Due fonti xG che non coincidono.** Dal 2026/27 football-data pubblica xG propri
(`xg_casa_fd`). Confrontati con Understat sulle 98 partite in comune: differenza
mediana 0,18 xG ma massima 2,02, e solo il 67% entro 0,3. Sono modelli diversi.
Usarne **una sola** per la stima della forza — Understat, che ha storico dal 2014 —
e tenere l'altra come controllo di sanita', mai mescolarle nella stessa serie.

## Quote Betfair: perche' e cosa cambia

`src/report/predict.py` seleziona su `MaxH`, cioe' la quota **massima fra ~20
bookmaker** e in **apertura**. E' il massimo di un campione, quindi distorto
all'insu' per costruzione; e' di un allibratore qualsiasi, non necessariamente
uno dove si puo' giocare; e non e' il prezzo dove la giocata verra' eseguita.
Il CLV che ne deriva confronta «meglio di 20 bookmaker in apertura» con «la
chiusura di un exchange»: una parte del segnale positivo e' garantita dal
confronto stesso, non dal fatto che il modello anticipi il mercato.

Dal **10 settembre 2026** la selezione avviene sul miglior back Betfair al netto
della commissione. E' prevedibile che si selezionino meno giocate e con edge
minore: non e' un peggioramento, e' la fine di una sovrastima.

**Le due serie non sono confrontabili.** Le giocate precedenti sono state
scelte contro la quota massima fra ~20 bookmaker in apertura; quelle nuove
contro il prezzo dell'exchange. ROI e CLV vanno calcolati per serie, non
mescolati. Il marcatore e' `prezzo-betfair-comm4.5` in **`previsioni.note`**,
che il trigger di immutabilita' protegge — non in `giocate.note`, che
`settle.py` sovrascrive alla contabilizzazione. Le giocate si riconducono alla
serie tramite `previsione_id`.

**Il calendario arriva da Betfair.** `q_ap_max_*` di football-data resta
comunque scritto sulla stessa riga — l'upsert usa la stessa chiave — quindi il
confronto fra i due prezzi si puo' fare a posteriori sulle partite che
football-data pubblica.

**Quota lorda e quota netta non sono interscambiabili.** Nel ledger `quota` e'
la **lorda**, cioe' quella che si punta: il CLV la confronta con una chiusura,
anch'essa lorda. `edge` invece e' calcolato sulla **netta**, perche' e' quella
che rende. Chi ricalcola `p × quota − 1` dal ledger ottiene un numero piu' alto
dell'`edge` salvato: la differenza e' la commissione, ed e' voluta.

**Sola lettura.** `listCompetitions`, `listMarketCatalogue`, `listMarketBook`.
Nessun `placeOrders`: la fase di giocata automatica e' successiva.

**La Delayed App Key basta.** E' gratuita, opera sull'exchange reale e
permetterebbe anche di scrivere ordini; i prezzi arrivano in snapshot ritardati
fra 1 e 180 secondi, irrilevante su mercati pre-match letti il venerdi' per il
weekend. La Live App Key (tempo reale) costa **499 GBP una tantum** e servirebbe
solo scendendo a orizzonti brevi. Con la Delayed manca il volume scambiato: la
size in `bf_raw` e' quella *disponibile*, non lo scambiato.

**Exchange italiano.** Login su `identitysso-cert.betfair.it` con certificato
self-signed, poi le richieste vanno agli endpoint `.com`, che restituiscono i
mercati visibili a un conto italiano. La liquidita' e' separata da quella
internazionale. `betfair.com` non e' un'alternativa: non accetta residenti
fiscali in Italia ed e' inibito da ADM. Vincoli della piazza, gia' nel codice:
commissione **4,5%** sulle vincite nette (`quota_netta()`), stake minimo
**2,00 EUR a multipli di 50 centesimi** (`arrotonda_stake()`, che arrotonda per
difetto perche' il cap di Kelly e' un limite superiore), vincita potenziale
massima 10.000 EUR. Nessuna delle due funzioni e' ancora richiamata da
`predict.py`.

**Id competizione e nomi squadra si leggono, non si indovinano.** Un
competitionId sbagliato non da' errore: restituisce le partite di un altro
campionato, e le seconde divisioni hanno nomi quasi identici (Bundesliga 2 =
61 contro 59, Ligue 2 = 57 contro 55). Verifica incrociata fatta il 10/9/2026:
per ognuna delle cinque leghe l'insieme delle squadre football-data e quello
dei nomi Betfair hanno la stessa cardinalita' (18/18, 20/20, 18/18, 20/20,
20/20) e si accoppiano tutti.

```bash
python -m src.ingest.betfair --competizioni   # id delle leghe, per COMPETIZIONI
python -m src.ingest.betfair --nomi           # nomi da mappare, per ALIAS_BETFAIR
```

`--nomi` legge le squadre da `partite` e non dai fixtures, e confronta **lega
per lega**: dai fixtures si vedrebbero solo le venti squadre del turno in
arrivo, e `fixtures.csv` e' vuoto durante le pause per le nazionali; un
confronto globale proporrebbe come candidato il nome di un altro campionato.
Va rilanciato a ogni nuova stagione, come `verifica()` per Understat.

Il caso pericoloso si e' presentato: **`Paris SG` -> `Paris St-G`, mentre
`Paris FC` su Betfair si chiama identico** e si aggancia da solo. Un fuzzy
matching li scambierebbe senza dare errore, ed e' la ragione per cui
`ALIAS_BETFAIR` e' manuale e `aggancia()` rifiuta i nomi non mappati invece di
avvicinarsi al piu' simile: una partita non agganciata viene stampata, una
agganciata male no.

**q_bf_* non e' q_bfe_*.** `q_bf_*` e' il miglior back al momento della
lettura (`bf_letto_il`); `q_bfe_*` e' la chiusura Betfair pubblicata da
football-data. Stessa borsa, momenti diversi, nessun fallback fra le due.

**Liquidita'.** Misurata il 10/9/2026 sulle 55 partite dei dieci giorni
successivi: al miglior prezzo, mediana 38 EUR per partita, peggiore 12 EUR.
Sopra il massimo stake attuale (cap 1% su bankroll 1000 = 10 EUR), ma non di
molto: alzando il bankroll gli ordini mangeranno piu' livelli del book e la
quota effettiva sara' peggiore di quella letta.

## Note tecniche

**Perche' REST e non psycopg2.** Il container instrada solo HTTP/HTTPS attraverso un
proxy: la porta 5432 del protocollo Postgres nativo non passa. `src/db/client.py` usa
PostgREST (`/rest/v1/...`, header `apikey` + `Authorization: Bearer`) e copre
select paginata, upsert a blocchi da 500, update, delete e conteggi.

**Endpoint Understat.** Il vecchio metodo (scraping di `var datesData = JSON.parse(...)`
dall'HTML) non funziona piu': la pagina lega carica i dati via XHR. Endpoint attuale,
verificato il 2026-09-04:

```
POST https://understat.com/getLeagueData/{lega}/{stagione}
     header  X-Requested-With: XMLHttpRequest
```

Risponde `{teams, players, dates}`; `dates` ha una riga per partita con xG, risultato e
anche i **fixture futuri** (`isResult: false`). Leghe: `EPL`, `Serie_A`, `La_liga`,
`Bundesliga`, `Ligue_1`. Stagione = anno d'inizio (2026 = 2026/27).

**Nomi squadra.** `src/ingest/squadre.py` traduce football-data → Understat.
Serve davvero: 25 nomi su 96 differiscono. Il caso pericoloso e' **`Paris SG`**, che
un fuzzy matching aggancia a **`Paris FC`** — un'altra squadra della stessa Ligue 1.
Per questo la tabella e' manuale. `verifica()` elenca i nomi non mappati: va chiamata
a ogni nuova stagione, quando promosse e retrocesse cambiano l'elenco.

**Immutabilita' delle previsioni.** La tabella `previsioni` ha un trigger che blocca
UPDATE e DELETE, in applicazione del principio in `00-specifica-sistema.md` par. 3.
Una previsione sbagliata si corregge inserendone una nuova, non riscrivendo la vecchia.

**RLS.** Lo schema abilita RLS e concede accesso pieno al ruolo `anon`: la publishable
key basta per leggere e scrivere. Per irrigidire: eliminare le policy `pengwin_anon_all`
e usare una **secret key** (`sb_secret_...`) in `SUPABASE_KEY`, senza altre modifiche
al codice.

## Il modello: portato e verificato numericamente

`src/model/dixon_coles_xg.py` e' identico all'originale del PC: e' stato tolto solo
l'inserimento di `pengwin/pylibs` in `sys.path`, che li' serviva perche' la home
della VM era effimera e qui non serve (pip funziona).

**Verifica.** Sulle 25 giocate del turno del 4 settembre, ricalcolando le stesse
probabilita':

| Dataset usato dal cloud | differenza mediana | massima | entro 0,01 |
|---|---|---|---|
| completo (fino al 3/9) | 0,00340 | 0,0265 | 20/25 |
| troncato al 25/8, come il PC | **0,00001** | 0,0090 | 25/25 |

Con gli stessi dati in ingresso le due implementazioni coincidono: il porting e'
corretto. La differenza sul dataset completo non e' un errore di codice, e' il PC
che stava lavorando su dati vecchi (vedi sotto).

## Il dataset del PC era fermo

Al momento del turno del 4 settembre, l'ultima partita presente in
`data/processed/xg_final.csv` era:

| Lega | ultima partita sul PC | ultima nel cloud |
|---|---|---|
| E0 | 2026-08-24 | 2026-08-31 |
| I1 | 2026-08-24 | 2026-08-31 |
| SP1 | 2026-08-27 | 2026-09-03 |
| F1 | 2026-08-23 | 2026-09-03 |
| D1 | **2026-05-16** | 2026-08-30 |

Per la Bundesliga mancava l'intera stagione 2026/27. Le 25 giocate aperte di quel
turno sono state selezionate su questa base. E' il genere di problema che la
versione cloud elimina, perche' l'ingest gira insieme alla previsione.

`src/report/predict.py` segnala ora esplicitamente con `<-- DATI VECCHI` ogni lega
la cui ultima partita risalga a piu' di 10 giorni prima.

## Struttura

```
pengwin_cloud/
├── README.md
├── requirements.txt
├── sql/schema.sql              -- squadre, partite, xg_partite, fixtures,
│                                  previsioni, giocate, log_esecuzioni + viste
├── sql/betfair_quote.sql       -- colonne q_bf_* sui fixtures (additivo)
├── tests/betfair.py            -- verifiche senza credenziali ne' rete
└── src/
    ├── db/client.py            -- Supabase via PostgREST
    ├── ingest/
    │   ├── football_data.py    -- risultati + quote (apertura e chiusura)
    │   ├── understat.py        -- xG per partita
    │   ├── betfair.py          -- quote dall'exchange (sola lettura)
    │   └── squadre.py          -- ponte fra i nomi delle fonti
    ├── migrate/
    │   └── ledger_import.py    -- import una tantum di ledger.csv
    ├── model/
    │   └── dixon_coles_xg.py   -- il modello, identico a quello del PC
    └── report/
        ├── dataset.py          -- ricostruisce xg_final da Supabase
        └── predict.py          -- previsioni, value bet, Kelly 1/4
```

## Il ciclo completo

```bash
python -m src.ingest.football_data --stagioni 2627 --fixtures --carica
python -m src.ingest.understat --stagioni 2026 --carica
python -m src.report.settle --scrivi        # contabilizza le giocate concluse
python -m src.ingest.betfair --carica       # calendario + quote Betfair
python -m src.report.predict                # anteprima del turno
python -m src.report.predict --registra     # registra previsioni e giocate
python -m src.report.dashboard out.html     # rigenera la pagina
```

L'ordine conta: prima l'ingest, poi la contabilizzazione (che ha bisogno dei
risultati appena scaricati), poi il passo Betfair — che porta il calendario e i
prezzi su cui si seleziona — e solo alla fine le previsioni. `predict.py` senza
un passo Betfair riuscito non trova quote e non seleziona nulla.

`src.ingest.betfair --carica` richiede una **secret key** in `SUPABASE_KEY`:
dopo `sql/rls_sola_lettura.sql` il ruolo `anon` e' in sola lettura, quindi con
la publishable key la scrittura viene rifiutata con `42501`. E' il
comportamento voluto.

`settle.py` non tocca mai una giocata gia' contabilizzata *con CLV calcolato*,
quindi si puo' rilanciare liberamente. Calcola anche il **CLV** — quota presa
diviso quota di chiusura, meno uno — che il par.6 della specifica indica come il
segnale affidabile, molto prima del ROI.

**Contabilizzazione a due fasi.** football-data pubblica i CSV stagionali con
ore o giorni di ritardo rispetto alla fine delle partite. Per non lasciare il
ledger fermo:

- se il risultato e' in `partite` (football-data) → contabilizzazione completa,
  con `quota_chiusura` e CLV: giocata definitiva;
- se invece e' solo in `xg_partite` (Understat) → contabilizzazione
  **provvisoria**: esito e P&L subito, `clv`/`quota_chiusura` restano NULL e la
  nota porta il marcatore `· Understat`. Sulla pagina live queste giocate hanno
  il tag `provv.`;
- ogni rilancio successivo, la **fase 2** riprende le giocate chiuse senza CLV e,
  appena football-data pubblica, riempie `quota_chiusura` e CLV. Se il risultato
  di football-data contraddice quello provvisorio di Understat, football-data
  prevale e la rettifica finisce in `note` e nel log.

Il segnale "provvisoria" e' `esito != aperta AND clv IS NULL AND note ~ 'Understat'`:
nessuna colonna nuova, nessuna migrazione dello schema.

Senza `--registra` `predict.py` non scrive nulla: e' la modalita' giusta per controllare prima
di impegnare il turno. **Non lanciare `--registra` due volte sullo stesso turno**:
le previsioni sono immutabili e non si possono cancellare.

## Cosa manca ancora

1. **Push del repo dal PC** (`C:\Users\difel\pengwin` → GitHub) — il repo e' ancora vuoto.
   Serve per portare `dixon_coles_xg.py` e il ledger storico. Attenzione a includere lo
   stato piu' recente del ledger, non una versione precedente.
2. **Script di migrazione del ledger** — da scrivere quando si conosce il formato reale
   di `ledger.csv` / `tracking.xlsx`, per inserirlo in `previsioni`/`giocate` con i
   timestamp originali intatti.
3. **Porting del modello** nel container e verifica numerica contro l'output del PC
   sulle stesse partite: due implementazioni che divergono sono peggio di una sola.
4. **Attivita' pianificata** cloud-native — solo dopo i punti sopra.


## Verifica dello stato (query di controllo)

```sql
select tablename, rowsecurity,
       (select count(*) from pg_policies p where p.tablename = t.tablename) as policy
from pg_tables t where schemaname = 'public' order by tablename;
```

Atteso: 7 tabelle, `rowsecurity = true`, 1 policy ciascuna.


## Dashboard

La pagina live e' `docs/index.html`, pubblicata via GitHub Pages. Non e' generata:
legge Supabase **dal browser** a ogni apertura (publishable key incorporata nella
pagina, RLS in sola lettura la rende sicura da esporre) e ricostruisce tutto —
metriche, grafici, tabelle — con JavaScript lato client. Non c'e' una copia
statica da rigenerare, quindi non c'e' una copia che possa restare indietro: se
la lettura fallisce, la pagina lo dice invece di mostrare numeri vecchi.

`src/report/dashboard.py` genera l'equivalente come HTML statico (dati incorporati
al momento dell'esecuzione anziche' letti dal browser) e viene tenuto sincronizzato
con `docs/index.html` a ogni modifica, ma non alimenta piu' nulla in produzione:
serve solo come riferimento offline o per un eventuale ritorno al modello statico.

La pagina e' ordinata per **peso probatorio**, non per estetica: il CLV viene
prima del ROI, perche' e' il criterio dichiarato nella specifica e il ROI su
poche decine di giocate e' rumore. Una dashboard che apre con il ROI positivo
racconterebbe una storia che i dati non sostengono. I filtri campionato e tipo
di giocata sono multiselect (si possono combinare piu' leghe o piu' mercati
insieme, es. Premier + Serie A), lo stato resta a scelta singola perche'
aperte/chiuse/tutte coprono gia' tutto lo spazio delle opzioni. I filtri
ricalcolano anche i riquadri in alto, non solo la tabella, e un pannello
dedicato mostra l'intervallo di confidenza sul ROI della selezione corrente
insieme a quante giocate servirebbero per distinguere un vantaggio reale dal
rumore.

Due tabelle mostrano anche la scomposizione **per campionato** e **per
mercato** su tutte le giocate concluse (non risentono dei filtri sopra, sono
gia' la vista completa) — ordinate per numero di giocate, non per ROI: la
fetta con piu' dati dietro viene prima, non quella che ha reso meglio finora.
Sotto le 8 giocate la riga e' marcata "poco dati" invece che lasciata in cima
solo perche' e' nata bene: e' la stessa logica che ha smascherato il falso
allarme sulla Premier League nel backtest, applicata sistematicamente invece
che raccontata una volta sola in un paragrafo.

Mostra anche lo **stato di freschezza dei dati** per campionato, con
evidenziazione oltre i dieci giorni: e' la protezione contro il problema che ha
colpito il PC, dove il modello prevedeva la Bundesliga senza aver visto nessuna
partita della stagione.

## Esecuzione automatica

Il ciclo gira come workflow GitHub Actions (`.github/workflows/pengwin.yml`,
`pengwin-turno.yml`), non come attivita' pianificata di una sessione Claude:

1. `pengwin.yml` (ogni giorno, anche a mano da Actions): ingest risultati e xG,
   carica su Supabase, contabilizza le giocate concluse, legge le quote Betfair;
2. `pengwin-turno.yml` (il venerdi'): in piu' registra il turno successivo.
   Qui le quote Betfair si leggono **prima** di `predict`, non dopo: il
   confronto che serve e' fra `MaxH` e prezzo Betfair *nel momento in cui il
   turno e' stato scelto*, altrimenti si misurerebbe anche la deriva del
   mercato nel frattempo;
3. `test.yml` (a ogni push): le verifiche di `tests/`, senza credenziali.

Il passo Betfair e' in `continue-on-error` in entrambi i workflow: il confronto
fra le due fonti e' un dato in piu', mentre contabilizzazione e registrazione
del turno sono il lavoro essenziale. Se i secret Betfair non sono configurati
il passo si salta con un avviso, senza far fallire il job.

Non c'e' un passo di "rigenera e ripubblica la dashboard": `docs/index.html` legge
Supabase da sola a ogni apertura, quindi aggiornare il database e' sufficiente.
Le chiavi Supabase stanno nei secret del repository (`Settings -> Secrets and
variables -> Actions`), non nel codice.
