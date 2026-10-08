"""
Fotografie delle quote betfair.it e quota di chiusura delle giocate reali.

    python -m src.reale.quote --fotografa   # tutte le partite dei 5 campionati nei prossimi 7 giorni
    python -m src.reale.quote --chiusura    # giocate aperte che iniziano entro 12 minuti

Due usi diversi, stesso dato.

`--fotografa` (ogni 2 ore, timer systemd) scrive in `quote_snapshot` miglior
back e miglior lay di ogni selezione 1X2 e Over/Under 2.5. Non gioca e non
sceglie nulla: e' l'archivio per decidere *quando* conviene piazzare. Fra
qualche settimana dira' a che distanza dal via i prezzi delle nostre selezioni
battono la chiusura. Una regola sul momento si decide con questi dati e si
pre-registra; non si sceglie a occhio.

`--chiusura` (ogni 5 minuti, timer systemd) serve il CLV. Per ogni giocata
reale aperta il cui calcio d'inizio cade nei prossimi 12 minuti:
  - aggiorna importo e quota abbinati (un ordine puo' abbinarsi dopo l'invio);
  - registra il miglior back del momento come quota di chiusura. Ogni
    passaggio la sovrascrive, quindi resta l'ultima lettura prima del via:
    la definizione pre-registrata e' «ultima fotografia entro 10 minuti».
Se non ci sono giocate in quella finestra non fa nemmeno il login.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.client import client                      # noqa: E402
from src.ingest.betfair import LocazioneVietata        # noqa: E402
from src.reale import api                              # noqa: E402
from src.reale.selezione import _tipo                  # noqa: E402

FINESTRA_CHIUSURA = timedelta(minutes=12)
APERTE = ("piazzata", "parziale", "abbinata")


def righe_fotografia(cat: list[dict], book: dict[str, dict], motivo: str) -> list[dict]:
    """Una riga per selezione: miglior back e lay con importo, stato del mercato."""
    ora = datetime.now(timezone.utc).isoformat()
    out = []
    for c in cat:
        b = book.get(c["marketId"])
        tipo = _tipo(c.get("marketName"))
        if not b or tipo is None:
            continue
        per_id = {r["selectionId"]: r for r in b.get("runners", [])}
        for desc in c.get("runners", []):
            r = per_id.get(desc["selectionId"]) or {}
            back, back_size = api.migliore(r, "availableToBack")
            lay, lay_size = api.migliore(r, "availableToLay")
            out.append({
                "il": ora, "motivo": motivo, "market_id": c["marketId"], "mercato": tipo,
                "lega": api.COMPETIZIONI.get((c.get("competition") or {}).get("id")),
                "evento": (c.get("event") or {}).get("name"),
                "inizio": c.get("marketStartTime"),
                "selection_id": desc["selectionId"], "runner": desc.get("runnerName"),
                "back": back, "back_size": back_size, "lay": lay, "lay_size": lay_size,
                "stato": b.get("status"), "inplay": b.get("inplay"),
                "ritardato": b.get("isMarketDataDelayed"),
            })
    return out


def fotografa(s, db, giorni: int) -> int:
    cat = api.catalogo(s, timedelta(0), timedelta(days=giorni))
    book = api.libri(s, sorted({c["marketId"] for c in cat}))
    righe = righe_fotografia(cat, book, "periodica")
    n = db.insert("quote_snapshot", righe) if righe else 0
    print(f"fotografia: {len(cat)} mercati, {n} selezioni")
    db.log("reale_fotografia", "ok", n, f"{len(cat)} mercati")
    return 0


def chiusura(db, sessione_factory, adesso: datetime | None = None) -> int:
    adesso = adesso or datetime.now(timezone.utc)
    aperte = db.select("giocate_reali", colonne="*", filtri={
        "stato": f"in.({','.join(APERTE)})",
        "inizio": f"lte.{(adesso + FINESTRA_CHIUSURA).isoformat()}",
    })
    aperte = [r for r in aperte
              if datetime.fromisoformat(r["inizio"].replace("Z", "+00:00")) > adesso]
    if not aperte:
        return 0
    s = sessione_factory()

    # 1. stato degli ordini: importo e quota abbinati fino a questo momento
    bet_ids = [r["bet_id"] for r in aperte if r.get("bet_id")]
    ordini = {}
    if bet_ids:
        res = api.rpc(s, "listCurrentOrders", {"betIds": bet_ids})
        ordini = {o["betId"]: o for o in res.get("currentOrders") or []}

    # 2. miglior back adesso = candidata quota di chiusura
    market_ids = sorted({r["market_id"] for r in aperte})
    book = api.libri(s, market_ids)
    for r in aperte:
        valori: dict = {}
        o = ordini.get(r.get("bet_id"))
        if o:
            abbinato = float(o.get("sizeMatched") or 0)
            size = float(r["stake_richiesto"])
            valori["stake_abbinato"] = abbinato
            valori["quota_abbinata"] = o.get("averagePriceMatched") or None
            valori["stato"] = ("abbinata" if abbinato >= size - 0.005
                               else "parziale" if abbinato > 0 else "piazzata")
        b = book.get(r["market_id"]) or {}
        if b.get("status") == "OPEN" and not b.get("inplay"):
            runner = next((x for x in b.get("runners", [])
                           if x["selectionId"] == int(r["selection_id"])), {})
            back, _ = api.migliore(runner, "availableToBack")
            if back:
                valori["quota_chiusura_it"] = back
                valori["chiusura_il"] = adesso.isoformat()
                q = valori.get("quota_abbinata") or r.get("quota_abbinata")
                if q:
                    valori["clv"] = round(float(q) / back - 1, 6)
        if valori:
            db.update("giocate_reali", {"id": f"eq.{r['id']}"}, valori)

    # 3. anche nell'archivio delle fotografie, con motivo 'chiusura'
    cat = [c for c in api.catalogo(s, timedelta(minutes=-5), FINESTRA_CHIUSURA)
           if c["marketId"] in set(market_ids)]
    righe = righe_fotografia(cat, book, "chiusura")
    if righe:
        db.insert("quote_snapshot", righe)
    print(f"chiusura: {len(aperte)} giocate aggiornate su {len(market_ids)} mercati")
    db.log("reale_chiusura", "ok", len(aperte), "")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fotografie delle quote betfair.it")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fotografa", action="store_true")
    g.add_argument("--chiusura", action="store_true")
    ap.add_argument("--giorni", type=int, default=7)
    a = ap.parse_args(argv)

    api.carica_env()
    db = client()
    try:
        if a.fotografa:
            return fotografa(api.sessione(), db, a.giorni)
        return chiusura(db, api.sessione)
    except LocazioneVietata:
        print("[!] Betfair rifiuta la posizione di questo server")
        db.log("reale_quote", "errore", 0, "BETTING_RESTRICTED_LOCATION")
        return 3
    except Exception as e:                                   # noqa: BLE001
        print(f"[!] {type(e).__name__}: {e}")
        db.log("reale_quote", "errore", 0, f"{type(e).__name__}: {e}"[:500])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
