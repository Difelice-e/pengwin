"""
Ripiego sugli xG di football-data quando Understat e' fermo. Deroga a tempo.

Perche' esiste: dal 1/11/2026 Understat non risponde (prima errori di rete, dal
4/11 una pagina di manutenzione). Il controllo strutturale rifiuta giustamente
i turni: il modello non ha gli xG dell'1-2/11. L'utente ha deciso il 5/11 di
non saltare il turno W45 (nota di progetto 25) e di usare, solo per le partite
mancanti, gli xG che football-data pubblica dal 2026/27.

Le regole, fissate prima del turno:
- vale solo fino a SCADENZA (dopo, Understat fermo = turno rifiutato come prima);
- si accende solo se l'ingest Understat non riesce da piu' di 30 ore e quello
  di football-data e' riuscito nelle ultime 30 ore: e' esattamente il caso in
  cui il controllo strutturale rifiuterebbe per colpa della sola Understat;
- riempie solo le partite giocate dal giorno dell'ultimo ingest Understat
  riuscito in poi, con risultato e xG football-data, e senza xG Understat;
- gli xG football-data non sono quelli di Understat: stanno sotto in media di
  0,13 per squadra a partita, con differenze diverse per campionato. Si somma la
  differenza media del campionato (CORREZIONE). Il rumore resta: correlazione
  0,93, scarto assoluto mediano 0,21;
- non scrive niente nel database: appena Understat torna, i suoi xG prendono il
  posto di questi da soli;
- un turno giocato col ripiego e' un turno DEGRADATO: la riga «dati: RIPIEGO …»
  finisce nel log del turno.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.report.freschezza import INGEST_MAX, ingest_fermi   # noqa: E402

FUSO = ZoneInfo("Europe/Rome")

# Copre il turno W45 (ven 6/11 alle 19:00, carta compresa) e niente altro: il
# turno successivo con partite e' venerdi' 20/11, dopo la sosta.
SCADENZA = datetime(2026, 11, 9, 0, 0, tzinfo=FUSO)

# Understat meno football-data, media per squadra a partita, 2026/27 fino al
# 31/10: 430 partite (860 valori) appaiate con le stesse regole di
# dataset.carica. Calcolata il 5/11/2026, poi fissata.
#   D1 140 valori, E0 176, F1 150, I1 186, SP1 208.
CORREZIONE = {"D1": 0.13, "E0": 0.16, "F1": 0.03, "I1": 0.18, "SP1": 0.14}


@dataclass
class Stato:
    attivo: bool
    motivo: str
    dal: date | None = None          # giorno dell'ultimo ingest Understat riuscito


def ultimo_ok(db, job: str) -> datetime | None:
    r = db.select("log_esecuzioni", colonne="eseguito_il", filtri={
        "job": f"eq.{job}", "esito": "eq.ok"}, ordina="eseguito_il.desc", limite=1)
    if not r:
        return None
    return datetime.fromisoformat(r[0]["eseguito_il"].replace("Z", "+00:00"))


def stato(db, adesso: datetime | None = None) -> Stato:
    adesso = adesso or datetime.now(timezone.utc)
    if adesso >= SCADENZA:
        return Stato(False, f"scaduto il {SCADENZA:%d/%m/%Y}")
    fermi = ingest_fermi(db, adesso)
    if "ingest_understat" not in fermi:
        return Stato(False, "Understat aggiornato: non serve")
    if "ingest_football_data" in fermi:
        return Stato(False, "anche football-data e' fermo")
    ultimo = ultimo_ok(db, "ingest_understat")
    if ultimo is None:
        return Stato(False, "nessun ingest Understat riuscito in archivio")
    ore = (adesso - ultimo).total_seconds() / 3600
    return Stato(True, f"ingest Understat fermo da {ore:.0f} ore "
                       f"(oltre {INGEST_MAX.total_seconds() / 3600:.0f})",
                 ultimo.astimezone(FUSO).date())


def applica(d: pd.DataFrame, dal: date) -> pd.DataFrame:
    """Riempie xgh/xga dove mancano, per le partite dal giorno `dal` in poi.

    `d` ha le colonne di dataset.carica prima del dropna, piu' xg_fd_casa e
    xg_fd_trasf. Restituisce una copia con `fonte_xg` = "fd" sulle righe riempite.
    """
    d = d.copy()
    corr = d["Div"].map(CORREZIONE)
    da_riempire = (d["xgh"].isna() & d["xga"].isna()
                   & d["FTHG"].notna() & d["FTAG"].notna()
                   & d["xg_fd_casa"].notna() & d["xg_fd_trasf"].notna()
                   & corr.notna()
                   & (d["MatchDate"] >= pd.Timestamp(dal)))
    d.loc[da_riempire, "xgh"] = (d.loc[da_riempire, "xg_fd_casa"] + corr[da_riempire]).clip(lower=0)
    d.loc[da_riempire, "xga"] = (d.loc[da_riempire, "xg_fd_trasf"] + corr[da_riempire]).clip(lower=0)
    d.loc[da_riempire, "fonte_xg"] = "fd"
    return d
