"""
Ingest football-data.co.uk -> tabelle `partite` e `fixtures`.

Uso:
  python -m src.ingest.football_data --stagioni 2425 2526 2627
  python -m src.ingest.football_data --fixtures
  python -m src.ingest.football_data --stagioni 2627 --fixtures --carica
Senza --carica scrive solo i CSV in data/raw/ e stampa un riepilogo.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

BASE = "https://www.football-data.co.uk"
LEGHE = {"E0": "Premier League", "I1": "Serie A", "SP1": "La Liga",
         "D1": "Bundesliga", "F1": "Ligue 1"}
RAW = Path(__file__).resolve().parents[2] / "data" / "raw"
TIMEOUT = 60

# colonna CSV -> colonna tabella
MAP_RISULTATI = {
    "FTHG": "gol_casa", "FTAG": "gol_trasferta", "FTR": "esito",
    "HTHG": "gol_casa_ht", "HTAG": "gol_trasferta_ht",
    "HS": "tiri_casa", "AS": "tiri_trasferta",
    "HST": "tiri_porta_casa", "AST": "tiri_porta_trasf",
    "HC": "corner_casa", "AC": "corner_trasferta",
}
# ATTENZIONE: le colonne SENZA "C" sono le quote di APERTURA; quelle di
# CHIUSURA hanno la C interna (AvgCH, B365CH, BFECH, PSCH). Il criterio di
# successo del progetto e' battere le CHIUSURE: non vanno mai confuse, e non
# si fa fallback dall'una all'altra (meglio un NULL onesto di un dato sbagliato).
MAP_CHIUSURA = {
    "AvgCH": "q_avg_1",  "AvgCD": "q_avg_x",  "AvgCA": "q_avg_2",    # media mercato
    "MaxCH": "q_max_1",  "MaxCD": "q_max_x",  "MaxCA": "q_max_2",    # massima disponibile
    "B365CH": "q_b365_1", "B365CD": "q_b365_x", "B365CA": "q_b365_2",
    "PSCH": "q_ps_1",    "PSCD": "q_ps_x",    "PSCA": "q_ps_2",      # Pinnacle (fino a 2526)
    "BFECH": "q_bfe_1",  "BFECD": "q_bfe_x",  "BFECA": "q_bfe_2",    # Betfair Exchange
    "AvgC>2.5": "q_avg_over25", "AvgC<2.5": "q_avg_under25",
    "MaxC>2.5": "q_max_over25", "MaxC<2.5": "q_max_under25",
    "BFEC>2.5": "q_bfe_over25", "BFEC<2.5": "q_bfe_under25",
}
MAP_APERTURA = {
    "AvgH": "q_ap_avg_1", "AvgD": "q_ap_avg_x", "AvgA": "q_ap_avg_2",
    "MaxH": "q_ap_max_1", "MaxD": "q_ap_max_x", "MaxA": "q_ap_max_2",
    "Max>2.5": "q_ap_max_over25", "Max<2.5": "q_ap_max_under25",
    "Avg>2.5": "q_ap_avg_over25", "Avg<2.5": "q_ap_avg_under25",
}
# dal 2026/27 football-data pubblica xG nativi: utile come cross-check di Understat
MAP_XG = {"HxG": "xg_casa_fd", "AxG": "xg_trasferta_fd"}

INTERI = set(MAP_RISULTATI.values()) - {"esito"}


def _num(v, intero=False):
    if v is None:
        return None
    v = str(v).strip()
    if not v or v in {"-", "NA"}:
        return None
    try:
        return int(float(v)) if intero else float(v)
    except ValueError:
        return None


def _data(v: str):
    v = (v or "").strip()
    for f in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(v, f).date().isoformat()
        except ValueError:
            continue
    return None


def _ora(v: str):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return datetime.strptime(v, "%H:%M").time().isoformat()
    except ValueError:
        return None


def scarica(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT,
                     headers={"User-Agent": "Mozilla/5.0 (compatible; pengwin/1.0)"})
    r.raise_for_status()
    return r.content.decode("utf-8-sig", errors="replace")


def _righe(testo: str):
    return list(csv.DictReader(io.StringIO(testo)))


def _quote(r: dict) -> dict:
    out = {}
    for mappa in (MAP_CHIUSURA, MAP_APERTURA, MAP_XG):
        for src, dst in mappa.items():
            v = _num(r.get(src))
            if v is not None:
                out[dst] = v
    return out


def stagione(lega: str, cod: str, salva=True) -> list[dict]:
    """Scarica una lega/stagione e la normalizza per la tabella `partite`."""
    url = f"{BASE}/mmz4281/{cod}/{lega}.csv"
    testo = scarica(url)
    if salva:
        RAW.mkdir(parents=True, exist_ok=True)
        (RAW / f"{cod}_{lega}.csv").write_text(testo, encoding="utf-8")

    out = []
    for r in _righe(testo):
        casa, trasf = (r.get("HomeTeam") or "").strip(), (r.get("AwayTeam") or "").strip()
        d = _data(r.get("Date", ""))
        if not (casa and trasf and d):
            continue
        riga = {"lega": lega, "stagione": cod, "data": d, "ora": _ora(r.get("Time", "")),
                "casa": casa, "trasferta": trasf}
        for src, dst in MAP_RISULTATI.items():
            riga[dst] = _num(r.get(src), intero=dst in INTERI) if dst != "esito" \
                else ((r.get(src) or "").strip() or None)
        riga.update(_quote(r))
        out.append(riga)
    return out


def fixtures(salva=True) -> list[dict]:
    """Partite in programma con quote correnti."""
    testo = scarica(f"{BASE}/fixtures.csv")
    if salva:
        RAW.mkdir(parents=True, exist_ok=True)
        (RAW / "fixtures.csv").write_text(testo, encoding="utf-8")
    out = []
    for r in _righe(testo):
        lega = (r.get("Div") or "").strip()
        if lega not in LEGHE:
            continue
        casa, trasf = (r.get("HomeTeam") or "").strip(), (r.get("AwayTeam") or "").strip()
        d = _data(r.get("Date", ""))
        if not (casa and trasf and d):
            continue
        riga = {"lega": lega, "data": d, "ora": _ora(r.get("Time", "")),
                "casa": casa, "trasferta": trasf}
        riga.update({k: v for k, v in _quote(r).items() if not k.startswith("xg_")})
        out.append(riga)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ingest football-data.co.uk")
    ap.add_argument("--stagioni", nargs="*", default=[],
                    help="codici stagione, es. 2425 2526 2627")
    ap.add_argument("--leghe", nargs="*", default=list(LEGHE))
    ap.add_argument("--fixtures", action="store_true")
    ap.add_argument("--carica", action="store_true", help="scrive su Supabase")
    a = ap.parse_args(argv)

    db = None
    if a.carica:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from src.db.client import client
        db = client()
        ok, msg = db.ping()
        if not ok:
            print(f"[db] non pronto: {msg}", file=sys.stderr)
            return 2

    totale = 0
    for cod in a.stagioni:
        for lega in a.leghe:
            try:
                righe = stagione(lega, cod)
            except Exception as e:
                print(f"[!] {cod}/{lega}: {e}", file=sys.stderr)
                continue
            disputate = sum(1 for r in righe if r.get("gol_casa") is not None)
            print(f"{cod}/{lega:<3} {len(righe):>4} partite ({disputate} disputate)")
            totale += len(righe)
            if db:
                n = db.upsert("partite", righe,
                              on_conflict="lega,stagione,data,casa,trasferta")
                print(f"          -> {n} righe su Supabase")

    if a.fixtures:
        fx = fixtures()
        print(f"fixtures  {len(fx):>4} partite in programma (Top5)")
        totale += len(fx)
        if db:
            n = db.upsert("fixtures", fx, on_conflict="lega,data,casa,trasferta")
            print(f"          -> {n} righe su Supabase")

    if db:
        db.log("ingest_football_data", "ok", totale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
