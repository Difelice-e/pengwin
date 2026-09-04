# 05 — Runbook esperimento a bankroll simulato

> **Attenzione: questo documento è ricostruito, non è l'originale.**
> Il runbook originale non è mai stato trovato: assente da `C:\Users\difel\pengwin`
> (nessun file `.md`, nessuna cartella `claude/`), da Downloads, da Notion e da Gmail;
> i tarball in `transfer/` contengono solo `src/` e `outputs/`. Ricostruito il 03/09/2026
> leggendo `src/report/{predict,settle,ledger}.py` e il contenuto di `outputs/`.
>
> **Le regole di selezione qui sotto sono documentazione, non la fonte di verità.**
> La fonte di verità sono le costanti in `src/report/predict.py`. Se le due cose
> divergono, ha ragione il codice e va corretto questo file — mai il contrario.

---

## 1. Cos'è l'esperimento

Il modello (Dixon-Coles su blend gol/xG) viene messo alla prova con denaro finto contro
una previsione **pre-registrata prima della prima giocata**. Bankroll iniziale 1000 €.
Il punto non è guadagnare: è vedere se il margine dichiarato sopravvive al confronto con
un risultato che non si conosce in anticipo.

Stato al 03/09/2026: 26 giocate registrate, 24 concluse, P&L +32,50 € su 177,61 € di
volume (ROI +18,3%), bankroll 1032,50 €.

**Invariante fondamentale:** le bande `band` e `premier` in `outputs/dashboard_data.json`
sono pre-registrate. Non vanno **mai** ricalcolate. Ricalcolarle a esperimento in corso
annullerebbe il senso della previsione. `settle.rebuild_dashboard_data()` le preserva
già di suo: riscrive solo `bets` e `meta`.

**Secondo invariante:** una giocata già contabilizzata non si tocca. `settle.py` salta
tutto ciò che non è in stato `aperta`.

---

## 2. Calendario della fonte

`football-data.co.uk` pubblica:

| Cosa | Quando |
|---|---|
| Risultati (`/mmz4281/2627/<div>.csv`) | domenica notte e mercoledì notte |
| Partite in arrivo (`/fixtures.csv`) | venerdì e martedì |

Se non c'è niente di nuovo è normale: dirlo in una riga e fermarsi. Non forzare.

Campionati seguiti: **E0** (Premier), **D1** (Bundesliga), **I1** (Serie A),
**SP1** (Liga), **F1** (Ligue 1). Stagione **2627**.

---

## 3. Recupero dati — NON usare i download

Il vincolo è: farsi restituire il CSV come **testo** e scriverlo con un heredoc.
Niente file scaricati.

La navigazione diretta a un URL `.csv` viene rifiutata dal browser interno (il file
viene trattato come download). La via che funziona: navigare su una pagina qualsiasi
del dominio, poi fare `fetch` dal contesto della pagina.

```js
// mcp__Claude_Browser__navigate  ->  https://www.football-data.co.uk/englandm.php
// poi javascript_tool:
const urls={E0:'/mmz4281/2627/E0.csv',D1:'/mmz4281/2627/D1.csv',I1:'/mmz4281/2627/I1.csv',
            SP1:'/mmz4281/2627/SP1.csv',F1:'/mmz4281/2627/F1.csv',FX:'/fixtures.csv'};
window.__pw={};
for(const [k,u] of Object.entries(urls)){
  const r=await fetch(u,{cache:'no-store'});
  window.__pw[k]=await r.text();
}
```

In alternativa `mcp__workspace__web_fetch` sugli stessi URL (usato nel giro del 30/08).

### Colonne da tenere

I due file in `data/raw/current/` sono **sottoinsiemi** dei CSV originali, non copie.

`pengwin_results.csv` — i cinque campionati concatenati, colonne:

```
Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR
```

`pengwin_fixtures.csv` — solo le righe dei cinque campionati, colonne:

```
Div,Date,Time,HomeTeam,AwayTeam,B365H,B365D,B365A,MaxH,MaxD,MaxA,AvgH,AvgD,AvgA,
B365>2.5,B365<2.5,Max>2.5,Max<2.5,Avg>2.5,Avg<2.5,BFE>2.5,BFE<2.5,AHh
```

Attenzione: `fixtures.csv` contiene molti campionati (E1, E2, E3, EC, B1, SC0...).
Va filtrato, altrimenti `predict.py` scarta tutto per "lega" senza modello.

Scrivere entrambi con heredoc in `C:\Users\difel\pengwin\data\raw\current\`.
Prima di sovrascrivere conviene copiare i file in `/tmp` e poi fare `diff`: è il modo
più rapido per capire se la fonte ha davvero pubblicato qualcosa di nuovo.

---

## 4. Esecuzione

`scipy` non è installato di sistema: serve `PYTHONPATH` sulle librerie locali.

```bash
cd /sessions/<sess>/mnt/pengwin
PYTHONPATH=$PWD/pylibs python3 src/report/settle.py     # contabilizza + rigenera dashboard_data.json
PYTHONPATH=$PWD/pylibs python3 src/report/predict.py    # nuove previsioni + selezione
```

`predict.py` scrive `outputs/predictions/all_<data>.csv` e `bets_<data>.csv` ma
**non registra niente a ledger**. La registrazione è un passo separato:

```python
import sys, pandas as pd
ROOT='/sessions/<sess>/mnt/pengwin'
sys.path.insert(0, f'{ROOT}/src')
from report import ledger, settle
sel = pd.read_csv(f'{ROOT}/outputs/predictions/bets_<data>.csv', parse_dates=['MatchDate'])
n, led = ledger.add(sel)
settle.rebuild_dashboard_data()
```

Ordine obbligatorio: **settle prima, predict poi**. Contabilizzare dopo aver aggiunto
le giocate nuove non romperebbe nulla, ma il `rebuild_dashboard_data` finale deve essere
l'ultima cosa, altrimenti la dashboard esce senza le giocate appena registrate.

---

## 5. Regole di selezione (documentate, autorevoli solo in `predict.py`)

| Parametro | Valore | Perché |
|---|---|---|
| `EDGE_MIN` | 2% | sotto, il vantaggio non copre il rumore |
| `EDGE_MAX` / `EDGE_SANITY` | 10% | **sopra, è errore di stima, non valore**: scartata |
| `KELLY_FRAC` | 0.25 | Kelly frazionario |
| `CAP` | 1% del bankroll | tetto per singola giocata (10 €) |
| `MAX_EXPOSURE` | 20% per turno | oltre, si riscala proporzionalmente |
| `MAX_BETS` | 25 | |
| `MIN_MATCHES` | 8 per squadra | sotto, la stima non è affidabile |
| `MINBET` | 2 € | |
| `W`, `XI`, `LOOKBACK` | 0.35, 0.0018, 1095 | tarati in Fase 2 |

Bankroll di riferimento per lo staking: **1000 € fissi**, non composto. Coerente con
tutte le giocate a registro finora (le puntate al tetto valgono esattamente 10,00 €).

Due filtri di protezione in `ledger.add()`, entrambi da non aggirare:

1. non duplica una giocata identica (stessa partita, stesso mercato);
2. accetta solo partite **successive** all'ultima già a registro. Senza questo, una
   seconda passata sullo stesso turno pescherebbe partite che alla prima non passavano
   la soglia — il modello nel frattempo si è ristimato — gonfiando l'esposizione e
   invalidando la previsione pre-registrata.

`predict.py` esclude inoltre le partite già iniziate o che iniziano entro 15 minuti,
confrontando il **calcio d'inizio**, non la data.

---

## 6. Dashboard

```python
tpl  = open('dash/tpl.html', encoding='utf-8').read()
data = open('outputs/dashboard_data.json', encoding='utf-8').read()
open('dash/dashboard.html','w',encoding='utf-8').write(tpl.replace('__DATA__', data))
```

`tpl.html` contiene un solo segnaposto, `__DATA__`, alla riga `const DATA = __DATA__;`.

### Ripubblicazione — mantenere lo stesso indirizzo

L'id dell'artifact è in **`dash/artifact.json`**: `pengwin-banco-di-prova`.

Usare `mcp__cowork__update_artifact` su **quell'id**. Non usare `create_artifact`:
creerebbe un indirizzo nuovo e lascerebbe indietro quello vecchio, congelato.

Prima di pubblicare, due ritocchi all'HTML (senza toccare `dash/dashboard.html`,
che resta la copia locale canonica):

1. togliere il `<link>` ai font di Google — la vista sandboxed blocca la rete e il CSS
   ha già i fallback di sistema;
2. avvolgere il frammento in `<!DOCTYPE html><html lang="it" data-theme="light"> …
   </html>`, perché `tpl.html` è un frammento senza `<head>`/`<body>` e il tema chiaro
   va forzato.

Dopo la pubblicazione, `verify_artifact` per controllare che non ci siano errori in console.

---

## 7. Report finale

Due o tre righe: giocate contabilizzate, P&L complessivo, se la traiettoria è dentro
la banda prevista. Per l'ultimo punto si confronta il P&L cumulato col turno
corrispondente in `dashboard_data.json → band` (`p2_5`, `p25`, `p50`, `p75`, `p97_5`).

---

## 8. Modi noti di rompersi

- ~~`predict.py` va in `KeyError: False` se nessuna partita è candidata.~~
  **Corretto il 03/09/2026.** Con `fixtures.csv` vuoto o tutte le partite già iniziate,
  il DataFrame non aveva la colonna `sospetta` e `out[out.get('sospetta', False) == True]`
  esplodeva — succedeva regolarmente il martedì mattina. Ora c'è una guardia
  `if len(out) == 0:` che stampa "nessuna partita candidata" ed esce con codice 0.
  Nessuna costante di selezione è stata toccata: le regole sono identiche a prima.
- **`fixtures.csv` non filtrato per campionato** → tutte le partite scartate per "lega".
- **Doppia passata sullo stesso turno** → bloccata da `ledger.add`, ma se qualcuno
  allentasse quel filtro l'esperimento sarebbe invalidato.
- **Lista artifact vuota.** Se `list_artifacts` non trova `pengwin-banco-di-prova`,
  *non* pubblicarne uno nuovo d'istinto: fermarsi e chiedere. È già successo tre volte
  (30/08, 01/09, 03/09) e ha lasciato la dashboard ferma per giorni.

---

## 9. Cronologia

| Data | Cosa |
|---|---|
| 29/08 | 24 giocate registrate (turni 28–31/08), primo turno pre-registrato |
| 30/08 | fonte ferma, nessuna operazione |
| 01/09 | contabilizzate 7 giocate del 31/08 — tutte e 24 chiuse, P&L +32,50 € |
| 03/09 | 2 giocate nuove (Toulouse–Lille U2.5, Sociedad–Celta O2.5); dashboard pubblicata come artifact `pengwin-banco-di-prova` |
