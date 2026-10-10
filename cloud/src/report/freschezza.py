"""
Dati vecchi: il controllo strutturale, per i turni automatici.

Il controllo di sempre (`MAX_ETA`) conta i giorni dall'ultima partita in
archivio. Dopo ogni sosta per le nazionali scatta a vuoto: i dati sono fermi
da due settimane perche' non si e' giocato, non perche' manca qualcosa. A mano
lo si scavalca con --consenti-dati-vecchi; un turno automatico non puo'
chiedere a nessuno (nota 17).

Questo controllo guarda invece il calendario. Una partita che secondo
Understat si doveva gia' giocare (calcio d'inizio fra 7 giorni fa e 8 ore fa)
e che nel frame del modello non c'e', con il risultato, e' un buco nei dati.
- Al piu' UNA partita mancante per campionato e' tollerata: e' il caso del
  rinvio, che altrimenti bloccherebbe il campionato per settimane.
- Gli ingest di football-data e Understat devono essere riusciti nelle ultime
  30 ore: se la pipeline quotidiana e' ferma, il turno non si gioca.

Funziona con qualunque frame prodotto da `src.report.dataset.carica`
(colonne Div, MatchDate, HomeTeam, AwayTeam con i nomi football-data).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.ingest.squadre import a_understat          # noqa: E402

LEGA_US = {"E0": "EPL", "I1": "Serie_A", "SP1": "La_liga",
           "D1": "Bundesliga", "F1": "Ligue_1"}
FINESTRA = timedelta(days=7)        # quanto indietro si guarda il calendario
MARGINE = timedelta(hours=8)        # una partita finita da meno di 8 ore non e' ancora attesa
TOLLERATE = 1                       # partite mancanti ammesse per campionato (rinvio)
TOLLERANZA_DATA = 3                 # giorni: le due fonti datano diversamente i recuperi
INGEST = ("ingest_football_data", "ingest_understat")
INGEST_MAX = timedelta(hours=30)


def mancanti(calendario: list[dict], d: pd.DataFrame, leghe) -> dict[str, list[str]]:
    """Per campionato, le partite del calendario senza risultato nel frame del modello.

    `calendario`: righe di xg_partite (lega_us, datetime UTC, casa, trasferta,
    nomi Understat), gia' ristrette alla finestra.
    """
    out: dict[str, list[str]] = {}
    for lega in leghe:
        us = LEGA_US[lega]
        dl = d[d["Div"] == lega]
        coppie: dict[tuple[str, str], list[pd.Timestamp]] = {}
        for h, a, dt in zip(dl["HomeTeam"], dl["AwayTeam"], dl["MatchDate"]):
            coppie.setdefault((a_understat(h), a_understat(a)), []).append(pd.Timestamp(dt))
        buchi = []
        for p in calendario:
            if p.get("lega_us") != us:
                continue
            quando = pd.Timestamp(str(p["datetime"])[:10])
            date = coppie.get((p["casa"], p["trasferta"]), [])
            if not any(abs((x - quando).days) <= TOLLERANZA_DATA for x in date):
                buchi.append(f"{p['casa']} - {p['trasferta']} ({str(p['datetime'])[:10]})")
        out[lega] = buchi
    return out


def leggi_calendario(db, adesso: datetime, leghe) -> list[dict]:
    da = (adesso - FINESTRA).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    a = (adesso - MARGINE).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return db.select("xg_partite", colonne="lega_us,datetime,casa,trasferta", filtri={
        "lega_us": f"in.({','.join(LEGA_US[x] for x in leghe)})",
        "and": f"(datetime.gte.{da},datetime.lt.{a})"})


def ingest_fermi(db, adesso: datetime) -> list[str]:
    """Gli ingest senza un'esecuzione riuscita nelle ultime 30 ore."""
    fermi = []
    for job in INGEST:
        r = db.select("log_esecuzioni", colonne="eseguito_il", filtri={
            "job": f"eq.{job}", "esito": "eq.ok"}, ordina="eseguito_il.desc", limite=1)
        if not r or datetime.fromisoformat(r[0]["eseguito_il"].replace("Z", "+00:00")) \
                < adesso - INGEST_MAX:
            fermi.append(job)
    return fermi


def controlla(db, d: pd.DataFrame, leghe, adesso: datetime | None = None) -> list[str]:
    """Motivi per cui i dati NON sono utilizzabili. Lista vuota = si puo' giocare."""
    adesso = adesso or datetime.now(timezone.utc)
    motivi = [f"{job}: nessuna esecuzione riuscita nelle ultime "
              f"{INGEST_MAX.total_seconds() / 3600:.0f} ore" for job in ingest_fermi(db, adesso)]
    for lega, buchi in mancanti(leggi_calendario(db, adesso, leghe), d, leghe).items():
        if len(buchi) > TOLLERATE:
            motivi.append(f"{lega}: {len(buchi)} partite gia' giocate senza risultato nei dati "
                          f"({'; '.join(buchi[:4])}{' …' if len(buchi) > 4 else ''})")
        elif buchi:
            print(f"  {lega}: 1 partita senza risultato, tollerata (rinvio?): {buchi[0]}")
    return motivi
