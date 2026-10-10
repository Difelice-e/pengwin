"""
Contabilizzazione delle giocate reali: esiti, profitto e commissione da Betfair.

    python -m src.reale.contabilizza            # scrive esiti e saldo
    python -m src.reale.contabilizza --prova    # stampa cosa scriverebbe, non scrive

Fa fede Betfair (`listClearedOrders`), non il risultato della partita: e' lo
stesso numero che si vede sul conto, compresi void e rimborsi.

Per ogni giocata con betId, senza esito e iniziata da almeno 1 ora e 45:
  - SETTLED  -> esito 'vinta' / 'persa', profitto lordo, commissione, netto;
                stato 'chiusa';
  - VOIDED   -> esito 'void', profitto 0, stato 'void' (partita annullata,
                mercato rimborsato);
  - LAPSED / CANCELLED senza nulla abbinato -> esito 'void', stato
                'non_abbinata'.
Una giocata che Betfair non ha ancora regolato resta com'e' e si riprova al
giro dopo (timer ogni 30 minuti).

La commissione Betfair si paga per mercato sulle vincite nette, quindi si legge
a livello di mercato (`groupBy: MARKET`). Se Betfair non la restituisce si
stima al 4,5% e lo si scrive in `note`.

Scrive anche il saldo del conto in `saldi_reali` (se la tabella c'e'), quando
cambia rispetto all'ultima lettura: e' quello che mostra la dashboard privata.
Il P&L invece si calcola dalle giocate chiuse, non dalla differenza di saldo,
cosi' i versamenti non lo sporcano.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.client import client                      # noqa: E402
from src.ingest.betfair import COMMISSIONE, LocazioneVietata  # noqa: E402
from src.reale import api                              # noqa: E402

# Una partita dura circa 1 ora e 50 con l'intervallo; Betfair regola di solito
# pochi minuti dopo il fischio finale. Prima di questo non vale la pena chiedere.
ATTESA_DOPO_INIZIO = timedelta(hours=1, minutes=45)
# Senza giocate da regolare, il saldo si rilegge al massimo ogni 3 ore.
SALDO_OGNI = timedelta(hours=3)
STATI_CON_ORDINE = ("piazzata", "parziale", "abbinata")


def da_regolare(db, adesso: datetime) -> list[dict]:
    righe = db.select("giocate_reali", colonne="*", filtri={
        "esito": "is.null", "bet_id": "not.is.null",
        "stato": f"in.({','.join(STATI_CON_ORDINE)})",
        "inizio": f"lt.{(adesso - ATTESA_DOPO_INIZIO).isoformat()}",
    })
    return righe


def ordini_regolati(s, bet_ids: list[str]) -> dict[str, dict]:
    """betId -> riepilogo Betfair, con il tipo di chiusura in `_tipo`."""
    out: dict[str, dict] = {}
    for stato in ("SETTLED", "VOIDED", "LAPSED", "CANCELLED"):
        da = 0
        while True:
            res = api.rpc(s, "listClearedOrders", {
                "betStatus": stato, "betIds": bet_ids,
                "includeItemDescription": False, "fromRecord": da, "recordCount": 1000})
            ordini = res.get("clearedOrders") or []
            for o in ordini:
                # Un ordine parzialmente abbinato puo' comparire due volte:
                # SETTLED per la parte abbinata e LAPSED per il resto. Vince SETTLED.
                if o["betId"] in out and out[o["betId"]]["_tipo"] == "SETTLED":
                    continue
                out[o["betId"]] = dict(o, _tipo=stato)
            if not res.get("moreAvailable"):
                break
            da += len(ordini)
    return out


def commissioni_per_mercato(s, market_ids: list[str]) -> dict[str, float]:
    if not market_ids:
        return {}
    res = api.rpc(s, "listClearedOrders", {
        "betStatus": "SETTLED", "marketIds": market_ids, "groupBy": "MARKET",
        "includeItemDescription": False})
    return {o["marketId"]: float(o["commission"])
            for o in res.get("clearedOrders") or [] if o.get("commission") is not None}


def valori_contabili(riga: dict, o: dict, commissione_mercato: float | None,
                     profitti_mercato: float, adesso: datetime) -> dict:
    """Le colonne da scrivere per una giocata, dato il riepilogo Betfair.

    `profitti_mercato`: somma dei profitti positivi delle nostre giocate su
    quel mercato, per ripartire la commissione se ce n'e' piu' d'una.
    """
    tipo = o["_tipo"]
    base = {"contabilizzata_il": adesso.isoformat()}
    if tipo == "VOIDED":
        return dict(base, esito="void", stato="void", profitto_lordo=0.0,
                    commissione=0.0, profitto_netto=0.0)
    if tipo in ("LAPSED", "CANCELLED"):
        return dict(base, esito="void", stato="non_abbinata", profitto_lordo=0.0,
                    commissione=0.0, profitto_netto=0.0,
                    note=f"Betfair: {tipo}, nulla abbinato")

    lordo = round(float(o.get("profit") or 0), 2)
    note = None
    if lordo > 0:
        if commissione_mercato is None:
            comm = round(lordo * COMMISSIONE, 2)
            note = f"commissione stimata al {COMMISSIONE:.1%}: Betfair non l'ha restituita"
        else:
            quota_parte = lordo / profitti_mercato if profitti_mercato > 0 else 1.0
            comm = round(commissione_mercato * quota_parte, 2)
    else:
        comm = 0.0
    esito = {"WON": "vinta", "LOST": "persa"}.get(o.get("betOutcome"))
    if esito is None:                       # PLACE o altro: decide il segno del profitto
        esito = "vinta" if lordo > 0 else "persa"
    out = dict(base, esito=esito, stato="chiusa", profitto_lordo=lordo, commissione=comm,
               profitto_netto=round(lordo - comm, 2))
    if o.get("sizeSettled") is not None:
        out["stake_abbinato"] = float(o["sizeSettled"])
    if o.get("priceMatched"):
        out["quota_abbinata"] = float(o["priceMatched"])
    if note:
        out["note"] = note
    return out


def contabilizza(s, db, righe: list[dict], adesso: datetime, scrivi: bool = True) -> tuple[int, int]:
    regolati = ordini_regolati(s, [r["bet_id"] for r in righe])
    chiusi = {r["market_id"] for r in righe
              if regolati.get(r["bet_id"], {}).get("_tipo") == "SETTLED"}
    commissioni = commissioni_per_mercato(s, sorted(chiusi))
    positivi: dict[str, float] = {}
    for r in righe:
        o = regolati.get(r["bet_id"])
        if o and o["_tipo"] == "SETTLED" and float(o.get("profit") or 0) > 0:
            positivi[r["market_id"]] = positivi.get(r["market_id"], 0) + float(o["profit"])

    fatte, attesa = 0, 0
    for r in righe:
        o = regolati.get(r["bet_id"])
        if o is None:
            attesa += 1
            continue
        v = valori_contabili(r, o, commissioni.get(r["market_id"]),
                             positivi.get(r["market_id"], 0.0), adesso)
        print(f"  {r['customer_order_ref'] or r['id']}: {r['casa']} - {r['trasferta']} "
              f"{r['mercato']} {r['selezione']} -> {v['esito']}  netto {v['profitto_netto']:+.2f}"
              + (f"  (commissione {v['commissione']:.2f})" if v.get("commissione") else ""))
        if scrivi:
            db.update("giocate_reali", {"id": f"eq.{r['id']}"}, v)
        fatte += 1
    return fatte, attesa


def ultimo_saldo(db) -> dict | None:
    try:
        r = db.select("saldi_reali", colonne="il,totale,disponibile,esposizione",
                      ordina="il.desc", limite=1)
    except Exception:                                        # noqa: BLE001
        return None        # tabella non ancora creata
    return r[0] if r else {}


def registra_saldo(s, db, ultimo: dict | None, adesso: datetime, scrivi: bool = True) -> dict:
    f = api.saldo(s)
    print(f"saldo betfair.it: {f['totale']:.2f} EUR "
          f"(disponibile {f['disponibile']:.2f}, esposizione {f['esposizione']:.2f})")
    if ultimo is None or not scrivi:
        return f
    uguale = (ultimo and abs(float(ultimo["disponibile"]) - f["disponibile"]) < 0.005
              and abs(float(ultimo["esposizione"]) - f["esposizione"]) < 0.005)
    if not uguale:
        db.insert("saldi_reali", [{"il": adesso.isoformat(), **f}])
    return f


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Contabilizzazione delle giocate reali (Betfair)")
    ap.add_argument("--prova", action="store_true", help="stampa senza scrivere")
    a = ap.parse_args(argv)

    api.carica_env()
    db = client()
    adesso = datetime.now(timezone.utc)
    righe = da_regolare(db, adesso)
    ultimo = ultimo_saldo(db)
    saldo_vecchio = ultimo is not None and (
        not ultimo or datetime.fromisoformat(ultimo["il"].replace("Z", "+00:00"))
        < adesso - SALDO_OGNI)
    if not righe and not saldo_vecchio and not a.prova:
        return 0           # niente da fare: nessun login

    try:
        s = api.sessione()
    except LocazioneVietata:
        print("[!] Betfair rifiuta la posizione di questo server")
        return 3

    fatte, attesa = (contabilizza(s, db, righe, adesso, scrivi=not a.prova)
                     if righe else (0, 0))
    print(f"contabilizzate {fatte}, non ancora regolate da Betfair {attesa}")
    registra_saldo(s, db, ultimo, adesso, scrivi=not a.prova)
    if righe and not a.prova:
        db.log("reale_contabilizza", "ok", fatte, f"{attesa} in attesa")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
