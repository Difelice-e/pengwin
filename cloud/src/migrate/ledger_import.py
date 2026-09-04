"""
Migrazione una tantum di outputs/ledger.csv nelle tabelle `previsioni` e `giocate`.

Conserva i timestamp originali (`placed_at`, `settled_at`): senza quelli il
tracking non vale nulla (00-specifica-sistema.md par.3). `previsioni` e'
immutabile per trigger, quindi lo script e' protetto contro il doppio
inserimento: rileva le previsioni gia' presenti e salta quelle.

Uso:
  python -m src.migrate.ledger_import percorso/ledger.csv
  python -m src.migrate.ledger_import percorso/ledger.csv --scrivi
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.client import client  # noqa: E402

# I timestamp del ledger sono ora locale, senza fuso: vanno interpretati in
# Europe/Rome. Trattarli come UTC li sposta di 1-2 ore e falsa il CLV, che
# dipende dalla distanza fra il momento della giocata e la chiusura del mercato.
FUSO = ZoneInfo("Europe/Rome")

MODELLO = "dixon_coles_xg"
VERSIONE = "blend35-65_xi0.0018_w3y"

# 'sel' del ledger -> (mercato, selezione)
SEL = {
    "1": ("1X2", "1"), "X": ("1X2", "X"), "2": ("1X2", "2"),
    "Over 2.5": ("OU25", "over"), "Under 2.5": ("OU25", "under"),
    "GG": ("GG", "gg"), "NG": ("GG", "ng"),
}
ESITO = {"vinta": "vinta", "persa": "persa", "aperta": "aperta",
         "void": "void", "annullata": "void"}


def _f(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _ts(v):
    v = (v or "").strip()
    if not v:
        return None
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(v, f).replace(tzinfo=FUSO).isoformat()
        except ValueError:
            continue
    return None


def turno(placed_at: str) -> str | None:
    """Etichetta di turno ISO (anno-settimana), es. 2026-W36."""
    d = _ts(placed_at)
    if not d:
        return None
    a, s, _ = date.fromisoformat(d[:10]).isocalendar()
    return f"{a}-W{s:02d}"


def leggi(percorso: Path) -> list[dict]:
    with open(percorso, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def chiave(r: dict) -> tuple:
    return (r["MatchDate"], r["HomeTeam"], r["AwayTeam"], r["sel"], r["placed_at"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Importa ledger.csv in Supabase")
    ap.add_argument("csv_path", type=Path)
    ap.add_argument("--scrivi", action="store_true",
                    help="senza questo flag mostra soltanto cosa farebbe")
    a = ap.parse_args(argv)

    righe = leggi(a.csv_path)
    print(f"ledger: {len(righe)} righe da {a.csv_path}")

    db = client()
    ok, msg = db.ping()
    if not ok:
        print(f"[db] non pronto: {msg}", file=sys.stderr)
        return 2

    # gia' presenti (previsioni e' immutabile: mai reinserire)
    esistenti = {
        (p["data_partita"], p["casa"], p["trasferta"], p["mercato"], p["selezione"],
         (p["creata_il"] or "")[:16])
        for p in db.select("previsioni",
                           colonne="data_partita,casa,trasferta,mercato,selezione,creata_il")
    }
    print(f"previsioni gia' in tabella: {len(esistenti)}")

    prev, salti, ignote = [], 0, []
    for r in righe:
        if r["sel"] not in SEL:
            ignote.append(r["sel"])
            continue
        mercato, selezione = SEL[r["sel"]]
        creata = _ts(r["placed_at"])
        k = (r["MatchDate"], r["HomeTeam"], r["AwayTeam"], mercato, selezione,
             (creata or "")[:16])
        if k in esistenti:
            salti += 1
            continue
        p, q = _f(r["p"]), _f(r["odds"])
        prev.append({
            "creata_il": creata, "modello": MODELLO, "versione": VERSIONE,
            "lega": r["Div"], "data_partita": r["MatchDate"],
            "casa": r["HomeTeam"], "trasferta": r["AwayTeam"],
            "mercato": mercato, "selezione": selezione, "prob": p,
            "quota_offerta": q,
            "edge": round(p * q - 1, 6) if (p and q) else None,
            "note": f"import ledger.csv bet_id={r['bet_id']}",
        })

    if ignote:
        print(f"[!] selezioni non riconosciute, saltate: {sorted(set(ignote))}", file=sys.stderr)
    print(f"da inserire: {len(prev)} previsioni  (gia' presenti: {salti})")

    if not a.scrivi:
        print("\n(anteprima — rilanciare con --scrivi per applicare)")
        for p in prev[:3]:
            print("  ", p["creata_il"], p["lega"], p["casa"], "-", p["trasferta"],
                  p["mercato"], p["selezione"], p["prob"], "@", p["quota_offerta"])
        return 0

    creati = db.insert("previsioni", prev, ritorna=True) if prev else []
    print(f"previsioni inserite: {len(creati)}")

    # indice previsione -> id, per collegare le giocate
    idx = {(p["data_partita"], p["casa"], p["trasferta"], p["mercato"], p["selezione"]): p["id"]
           for p in db.select("previsioni",
                              colonne="id,data_partita,casa,trasferta,mercato,selezione")}

    giocate = []
    for r in righe:
        if r["sel"] not in SEL:
            continue
        mercato, selezione = SEL[r["sel"]]
        pid = idx.get((r["MatchDate"], r["HomeTeam"], r["AwayTeam"], mercato, selezione))
        p, q = _f(r["p"]), _f(r["odds"])
        giocate.append({
            "previsione_id": pid,
            "piazzata_il": _ts(r["placed_at"]),
            "turno": turno(r["placed_at"]),
            "lega": r["Div"], "data_partita": r["MatchDate"],
            "casa": r["HomeTeam"], "trasferta": r["AwayTeam"],
            "mercato": mercato, "selezione": selezione,
            "quota": q, "stake": _f(r["stake"]),
            "prob_modello": p,
            "edge": round(p * q - 1, 6) if (p and q) else None,
            "esito": ESITO.get(r["status"], "aperta"),
            "ritorno": _f(r.get("payout")),
            "chiusa_il": _ts(r.get("settled_at")),
            "note": (f"risultato {r['result']}" if r.get("result") else None),
        })

    # le giocate si possono reimportare: si azzera e si riscrive lo stato corrente
    db.delete("giocate", {"note": "not.is.null"})
    db.delete("giocate", {"note": "is.null"})
    n = db.insert("giocate", giocate)
    print(f"giocate inserite: {n}")
    db.log("migrazione_ledger", "ok", n, f"da {a.csv_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
