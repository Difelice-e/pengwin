"""
Ingest Understat -> tabella `xg_partite`.

Endpoint (verificato 2026-09-04):
  POST https://understat.com/getLeagueData/{lega}/{stagione}
  header X-Requested-With: XMLHttpRequest
Risposta JSON: {teams: {...}, players: [...], dates: [...]}
`dates` contiene una riga per partita, con xG e risultato, incluse le
partite non ancora giocate (isResult=false, xG assenti).

Uso:
  python -m src.ingest.understat --stagioni 2024 2025 2026
  python -m src.ingest.understat --stagioni 2026 --carica
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

BASE = "https://understat.com"
LEGHE = ["EPL", "Serie_A", "La_liga", "Bundesliga", "Ligue_1"]
# corrispondenza con i codici football-data
LEGA_FD = {"EPL": "E0", "Serie_A": "I1", "La_liga": "SP1",
           "Bundesliga": "D1", "Ligue_1": "F1"}
RAW = Path(__file__).resolve().parents[2] / "data" / "xg"
TIMEOUT = 60
HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (compatible; pengwin/1.0)",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": f"{BASE}/",
}


def _num(v):
    if v in (None, "", "-"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def scarica_lega(lega: str, stagione: int, salva=True) -> dict:
    url = f"{BASE}/getLeagueData/{lega}/{stagione}"
    r = requests.post(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    dati = r.json()
    if salva:
        RAW.mkdir(parents=True, exist_ok=True)
        (RAW / f"{lega}_{stagione}.json").write_text(
            json.dumps(dati, ensure_ascii=False), encoding="utf-8")
    return dati


def partite(lega: str, stagione: int, salva=True) -> list[dict]:
    dati = scarica_lega(lega, stagione, salva=salva)
    out = []
    for m in dati.get("dates", []):
        h, a = m.get("h") or {}, m.get("a") or {}
        casa, trasf = (h.get("title") or "").strip(), (a.get("title") or "").strip()
        if not (casa and trasf and m.get("id")):
            continue
        gol = m.get("goals") or {}
        xg = m.get("xG") or {}
        out.append({
            "understat_id": str(m["id"]),
            "lega_us": lega,
            "stagione": stagione,
            "datetime": m.get("datetime"),
            "casa": casa,
            "trasferta": trasf,
            "gol_casa": _int(gol.get("h")),
            "gol_trasferta": _int(gol.get("a")),
            "xg_casa": _num(xg.get("h")),
            "xg_trasferta": _num(xg.get("a")),
            "disputata": bool(m.get("isResult")),
        })
    return out


def squadre(stagione: int) -> list[dict]:
    """Elenco squadre per lega, utile a popolare l'anagrafica `squadre`."""
    out = []
    for lega in LEGHE:
        dati = scarica_lega(lega, stagione, salva=False)
        for t in (dati.get("teams") or {}).values():
            out.append({"nome_canonico": t["title"], "nome_understat": t["title"],
                        "lega": LEGA_FD[lega]})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ingest Understat (xG per partita)")
    ap.add_argument("--stagioni", nargs="+", type=int, required=True,
                    help="anno di inizio stagione, es. 2026 per 2026/27")
    ap.add_argument("--leghe", nargs="*", default=LEGHE)
    ap.add_argument("--carica", action="store_true")
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
    for st in a.stagioni:
        for lega in a.leghe:
            try:
                righe = partite(lega, st)
            except Exception as e:
                print(f"[!] {lega}/{st}: {e}", file=sys.stderr)
                continue
            disputate = sum(1 for r in righe if r["disputata"])
            print(f"{st}/{lega:<11} {len(righe):>4} partite ({disputate} disputate)")
            totale += len(righe)
            if db:
                n = db.upsert("xg_partite", righe, on_conflict="understat_id")
                print(f"                 -> {n} righe su Supabase")

    if db:
        db.log("ingest_understat", "ok", totale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
