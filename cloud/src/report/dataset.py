"""
Costruisce da Supabase il frame di addestramento che sul PC era
data/processed/xg_final.csv, con le stesse colonne attese dal modello:
Div, MatchDate, HomeTeam, AwayTeam, FTHG, FTAG, xgh, xga.

Gli xG vengono da Understat (fonte unica per la stima della forza); quelli
pubblicati da football-data dal 2026/27 non sono intercambiabili — differenza
mediana 0,18 ma massima 2,02 sulle partite in comune — e restano di controllo.
Unica eccezione: il ripiego a tempo di src/report/ripiego_xg.py, che si accende
da solo solo se Understat e' fermo, e la colonna `fonte_xg` dice dove e' stato
usato ("us" o "fd").
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.client import client          # noqa: E402
from src.ingest.squadre import a_understat  # noqa: E402

# Scarto massimo fra le date della stessa partita nelle due fonti. Sui 5.708
# risultati 2023-2027 la stessa partita dista al piu' 11 giorni (Udinese-Roma
# 2024, sospesa e ripresa); la stessa coppia nella stagione prima dista almeno
# 161 giorni. Senza questo limite, una partita che Understat non ha ancora
# prendeva gli xG della stessa sfida di una stagione passata (successo dal 1/11
# al 5/11/2026: Atalanta-Parma con gli xG di gennaio, Frosinone-Torino con quelli
# del 2023), e il controllo strutturale la contava come presente.
SCARTO_MAX = pd.Timedelta(days=30)


def carica(db=None, adesso: datetime | None = None) -> pd.DataFrame:
    db = db or client()
    p = pd.DataFrame(db.select(
        "partite",
        colonne="lega,data,casa,trasferta,gol_casa,gol_trasferta,xg_casa_fd,xg_trasferta_fd",
        filtri={"gol_casa": "not.is.null"}))
    x = pd.DataFrame(db.select(
        "xg_partite",
        colonne="casa,trasferta,datetime,xg_casa,xg_trasferta",
        filtri={"disputata": "is.true"}))

    p["MatchDate"] = pd.to_datetime(p["data"])
    x["MatchDate"] = pd.to_datetime(x["datetime"]).dt.normalize()
    p["_casa_us"] = p["casa"].map(a_understat)
    p["_trasf_us"] = p["trasferta"].map(a_understat)

    # join sui nomi squadra; la data non entra nella chiave perche' le due fonti
    # datano diversamente le partite spostate, ma lo scarto e' limitato (SCARTO_MAX)
    d = p.merge(
        x[["casa", "trasferta", "xg_casa", "xg_trasferta", "MatchDate"]].rename(
            columns={"casa": "_casa_us", "trasferta": "_trasf_us",
                     "MatchDate": "_data_us"}),
        on=["_casa_us", "_trasf_us"], how="left")

    # se una coppia si ripete (andata/ritorno fra stagioni) tiene l'occorrenza
    # piu' vicina nel tempo; oltre SCARTO_MAX non e' la stessa partita
    d["_scarto"] = (d["MatchDate"] - d["_data_us"]).abs()
    lontana = d["_scarto"] > SCARTO_MAX
    d.loc[lontana, ["xg_casa", "xg_trasferta"]] = None
    d = (d.sort_values("_scarto")
           .drop_duplicates(subset=["lega", "MatchDate", "casa", "trasferta"], keep="first"))

    d = d.rename(columns={"lega": "Div", "casa": "HomeTeam", "trasferta": "AwayTeam",
                          "gol_casa": "FTHG", "gol_trasferta": "FTAG",
                          "xg_casa": "xgh", "xg_trasferta": "xga",
                          "xg_casa_fd": "xg_fd_casa", "xg_trasferta_fd": "xg_fd_trasf"})
    d = d[["Div", "MatchDate", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "xgh", "xga",
           "xg_fd_casa", "xg_fd_trasf"]]
    for c in ("FTHG", "FTAG", "xgh", "xga", "xg_fd_casa", "xg_fd_trasf"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["fonte_xg"] = "us"

    from src.report import ripiego_xg     # import qui: ripiego_xg importa freschezza
    st = ripiego_xg.stato(db, adesso)
    if st.attivo:
        d = ripiego_xg.applica(d, st.dal)

    d = d.drop(columns=["xg_fd_casa", "xg_fd_trasf"])
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
