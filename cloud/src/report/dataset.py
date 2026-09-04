"""
Costruisce da Supabase il frame di addestramento che sul PC era
data/processed/xg_final.csv, con le stesse colonne attese dal modello:
Div, MatchDate, HomeTeam, AwayTeam, FTHG, FTAG, xgh, xga.

Gli xG vengono da Understat (fonte unica per la stima della forza); quelli
pubblicati da football-data dal 2026/27 non sono intercambiabili — differenza
mediana 0,18 ma massima 2,02 sulle partite in comune — e restano di controllo.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.client import client          # noqa: E402
from src.ingest.squadre import a_understat  # noqa: E402


def carica(db=None) -> pd.DataFrame:
    db = db or client()
    p = pd.DataFrame(db.select(
        "partite",
        colonne="lega,data,casa,trasferta,gol_casa,gol_trasferta",
        filtri={"gol_casa": "not.is.null"}))
    x = pd.DataFrame(db.select(
        "xg_partite",
        colonne="casa,trasferta,datetime,xg_casa,xg_trasferta",
        filtri={"disputata": "is.true"}))

    p["MatchDate"] = pd.to_datetime(p["data"])
    x["MatchDate"] = pd.to_datetime(x["datetime"]).dt.normalize()
    p["_casa_us"] = p["casa"].map(a_understat)
    p["_trasf_us"] = p["trasferta"].map(a_understat)

    # join sui soli nomi squadra: le due fonti datano diversamente le partite
    # spostate, quindi la data non entra nella chiave.
    d = p.merge(
        x[["casa", "trasferta", "xg_casa", "xg_trasferta", "MatchDate"]].rename(
            columns={"casa": "_casa_us", "trasferta": "_trasf_us",
                     "MatchDate": "_data_us"}),
        on=["_casa_us", "_trasf_us"], how="left")

    # se una coppia si ripete (andata/ritorno fra stagioni) tiene l'occorrenza
    # piu' vicina nel tempo
    d["_scarto"] = (d["MatchDate"] - d["_data_us"]).abs()
    d = (d.sort_values("_scarto")
           .drop_duplicates(subset=["lega", "MatchDate", "casa", "trasferta"], keep="first"))

    d = d.rename(columns={"lega": "Div", "casa": "HomeTeam", "trasferta": "AwayTeam",
                          "gol_casa": "FTHG", "gol_trasferta": "FTAG",
                          "xg_casa": "xgh", "xg_trasferta": "xga"})
    d = d[["Div", "MatchDate", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "xgh", "xga"]]
    for c in ("FTHG", "FTAG", "xgh", "xga"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.dropna(subset=["FTHG", "FTAG", "xgh", "xga"]).sort_values("MatchDate")


def fixtures(db=None) -> pd.DataFrame:
    """Partite in programma con le quote MASSIME correnti (non di chiusura:
    si punta alla quota disponibile adesso)."""
    db = db or client()
    f = pd.DataFrame(db.select("fixtures", colonne="*"))
    if f.empty:
        return f
    f["MatchDate"] = pd.to_datetime(f["data"])
    return f.rename(columns={"lega": "Div", "casa": "HomeTeam", "trasferta": "AwayTeam",
                             "ora": "Time",
                             "q_ap_max_1": "MaxH", "q_ap_max_x": "MaxD", "q_ap_max_2": "MaxA",
                             "q_ap_avg_1": "AvgH", "q_ap_avg_x": "AvgD", "q_ap_avg_2": "AvgA",
                             "q_ap_max_over25": "MaxO25",
                             "q_ap_max_under25": "MaxU25"})


if __name__ == "__main__":
    d = carica()
    print(f"partite con gol e xG: {len(d)}")
    print(d.groupby("Div").agg(n=("FTHG", "size"), dal=("MatchDate", "min"),
                               al=("MatchDate", "max")))
