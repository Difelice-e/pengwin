"""
Braccio SU CARTA Goal/No Goal (BTTS) ai prezzi di betfair.it. Nessun denaro.

    python -m src.reale.carta_btts                      # anteprima, non scrive
    python -m src.reale.carta_btts --registra           # registra le giocate su carta del turno
    python -m src.reale.carta_btts --contabilizza       # esiti delle partite concluse (timer)
    python -m src.reale.carta_btts --auto               # registra, dal turno automatico

Con --auto i dati vecchi si giudicano con il controllo strutturale
(src/report/freschezza.py) e la finestra e' quella del turno (fine_finestra),
come per il turno reale automatico.

Si lancia subito dopo il turno reale, con lo stesso --consenti-dati-vecchi
quando serve. Regole in `preregistrazioni.carta_btts`: le stesse del braccio
reale (selezione.py), su tutti e cinque i campionati, bankroll virtuale 400.

Perche' a parte. Il modello calcola P(entrambe segnano) ma non e' mai stato
verificato su questo mercato. Prima dei soldi serve una misura, e la misura
va presa con regole fissate prima, non ricostruita dopo dalle fotografie.

Separato da `giocate` (l'esperimento su carta 1X2/Over-Under) e da
`giocate_reali`: nessuna delle due serie si mescola con questa.

La quota di chiusura la scrive `quote.py --chiusura` (timer ogni 5 minuti),
che tratta anche queste righe. Il CLV e' quota / quota_chiusura - 1.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.client import client                                   # noqa: E402
from src.ingest.betfair import COMMISSIONE, LocazioneVietata        # noqa: E402
from src.ingest.squadre import da_betfair                           # noqa: E402
from src.reale import api                                           # noqa: E402
from src.reale.selezione import MARGINE_INIZIO, controlla, seleziona  # noqa: E402

FUSO = ZoneInfo("Europe/Rome")
LEGHE = ("E0", "I1", "SP1", "D1", "F1")
BANKROLL_VIRTUALE = 400.0
TABELLA = "carta_btts"
SELEZIONI = {"yes": "gg", "no": "ng"}


def candidati_btts(cat: list[dict], book: dict[str, dict], probabilita, adesso: datetime,
                   leghe=LEGHE) -> tuple[list[dict], list[str]]:
    """Una riga per Goal e una per No Goal di ogni partita utilizzabile.

    `probabilita(lega, casa, trasferta)` e' la stessa del turno reale: il
    dizionario di markets() del modello, o una stringa col motivo dello scarto.
    """
    righe, scartate = [], []
    for c in cat:
        lega = api.COMPETIZIONI.get((c.get("competition") or {}).get("id"))
        nome = (c.get("event") or {}).get("name") or ""
        if lega not in leghe or " v " not in nome:
            continue
        if not (c.get("marketName") or "").lower().startswith("both teams to score"):
            continue
        inizio = datetime.fromisoformat(c["marketStartTime"].replace("Z", "+00:00"))
        if inizio - adesso < MARGINE_INIZIO:
            continue
        libro = book.get(c["marketId"])
        if not libro or libro.get("status") != "OPEN" or libro.get("inplay"):
            continue

        casa_bf, trasf_bf = (x.strip() for x in nome.split(" v ", 1))
        casa, trasf = da_betfair(casa_bf), da_betfair(trasf_bf)
        mk = probabilita(lega, casa, trasf)
        if isinstance(mk, str):
            scartate.append(f"{lega} {casa} - {trasf}: {mk}")
            continue
        p_gg = float(mk["BTTS"])

        per_id = {r["selectionId"]: r for r in libro.get("runners", [])}
        for desc in c.get("runners", []):
            sel = SELEZIONI.get((desc.get("runnerName") or "").strip().lower())
            r = per_id.get(desc["selectionId"])
            if sel is None or r is None or r.get("status") != "ACTIVE":
                continue
            offerte = (r.get("ex") or {}).get("availableToBack") or []
            if not offerte or not offerte[0].get("price"):
                continue
            righe.append({
                "lega": lega, "casa": casa, "trasferta": trasf, "evento_bf": nome,
                "inizio": inizio, "mercato": "BTTS", "selezione": sel,
                "market_id": c["marketId"], "selection_id": desc["selectionId"],
                "p": p_gg if sel == "gg" else 1.0 - p_gg,
                "quota": float(offerte[0]["price"]),
                "size": float(offerte[0].get("size") or 0),
            })
    return righe, scartate


def esito(selezione: str, gol_casa: int, gol_trasf: int) -> str:
    entrambe = gol_casa > 0 and gol_trasf > 0
    return "vinta" if (selezione == "gg") == entrambe else "persa"


def profitto(esito_: str, stake: float, quota: float) -> float:
    """Netto della commissione, che su Betfair si paga solo sulle vincite."""
    if esito_ == "vinta":
        return round(stake * (quota - 1) * (1 - COMMISSIONE), 2)
    if esito_ == "persa":
        return -round(stake, 2)
    return 0.0


def risultato(partite: list[dict], data_partita: str) -> dict | None:
    """La partita giocata piu' vicina alla data prevista, entro 3 giorni (rinvii brevi)."""
    prevista = date.fromisoformat(data_partita)
    giocate = [p for p in partite if p.get("gol_casa") is not None
               and abs((date.fromisoformat(p["data"]) - prevista).days) <= 3]
    if not giocate:
        return None
    return min(giocate, key=lambda p: abs((date.fromisoformat(p["data"]) - prevista).days))


def chiusura_da_fotografia(db, r: dict) -> dict:
    """Ripiego pre-registrato: l'ultima fotografia periodica prima del via.

    Serve se il timer delle chiusure non ha girato a ridosso della partita
    (server spento, rete). Annotato in `chiusura_fonte`.
    """
    try:
        f = db.select("quote_snapshot", colonne="il,back", filtri={
            "market_id": f"eq.{r['market_id']}",
            "selection_id": f"eq.{r['selection_id']}",
            "il": f"lt.{r['inizio']}", "back": "not.is.null"},
            ordina="il.desc", limite=1)
    except Exception:                                        # noqa: BLE001
        return {}
    if not f:
        return {}
    back = float(f[0]["back"])
    return {"quota_chiusura": back, "chiusura_il": f[0]["il"], "chiusura_fonte": "fotografia",
            "clv": round(float(r["quota"]) / back - 1, 6)}


def contabilizza(db, adesso: datetime | None = None) -> int:
    adesso = adesso or datetime.now(timezone.utc)
    aperte = db.select(TABELLA, colonne="*", filtri={"esito": "is.null"})
    aperte = [r for r in aperte
              if datetime.fromisoformat(r["inizio"].replace("Z", "+00:00"))
              < adesso - timedelta(hours=2, minutes=30)]
    fatte, in_attesa = 0, 0
    for r in aperte:
        partite = db.select("partite", colonne="data,gol_casa,gol_trasferta", filtri={
            "lega": f"eq.{r['lega']}", "casa": f"eq.{r['casa']}",
            "trasferta": f"eq.{r['trasferta']}"})
        p = risultato(partite, r["data_partita"])
        if p is None:
            in_attesa += 1
            continue
        e = esito(r["selezione"], int(p["gol_casa"]), int(p["gol_trasferta"]))
        valori = {
            "gol_casa": int(p["gol_casa"]), "gol_trasferta": int(p["gol_trasferta"]),
            "esito": e, "profitto_netto": profitto(e, float(r["stake"]), float(r["quota"])),
            "contabilizzata_il": adesso.isoformat()}
        if r.get("quota_chiusura") is None:
            valori.update(chiusura_da_fotografia(db, r))
        db.update(TABELLA, {"id": f"eq.{r['id']}"}, valori)
        fatte += 1
    print(f"Goal/No Goal su carta: contabilizzate {fatte}, in attesa del risultato {in_attesa}")
    db.log("carta_btts_contabilizza", "ok", fatte, f"{in_attesa} in attesa")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Braccio su carta Goal/No Goal (betfair.it)")
    ap.add_argument("--registra", action="store_true", help="scrive le giocate su carta")
    ap.add_argument("--contabilizza", action="store_true", help="esiti delle partite concluse")
    ap.add_argument("--consenti-dati-vecchi", action="store_true", dest="consenti_dati_vecchi")
    ap.add_argument("--giorni", type=float, default=4)
    ap.add_argument("--auto", action="store_true",
                    help="registra con controllo strutturale e finestra del turno")
    a = ap.parse_args(argv)
    if a.auto:
        a.registra = True

    api.carica_env()
    db = client()
    if a.contabilizza:
        return contabilizza(db)

    # import qui: il modello serve solo per la selezione, non per contabilizzare
    from src.reale.turno import (dati_vecchi, fine_finestra, probabilita_dal_modello,
                                 turno_corrente)
    from src.report import freschezza
    from src.report.dataset import carica
    from src.report.predict import MAX_ETA, VERSIONE, build_models

    try:
        s = api.sessione()
    except LocazioneVietata:
        print("[!] Betfair rifiuta la posizione di questo server")
        return 3

    ora = datetime.now(FUSO)
    turno = turno_corrente(ora)
    if a.registra and db.select(TABELLA, colonne="id", filtri={"turno": f"eq.{turno}"}):
        print(f"[!] RIFIUTATO: il turno {turno} del Goal/No Goal e' gia' registrato")
        return 2

    oggi = pd.Timestamp(ora.date())
    frame = carica(db)
    models = {k: v for k, v in build_models(frame, oggi).items() if k in LEGHE}
    vecchi = dati_vecchi(models, oggi)
    for k, ultima, eta in vecchi:
        print(f"  modello {k}: ultima partita {ultima} ({eta} giorni fa)  <-- DATI VECCHI")
    if a.auto:
        motivi = freschezza.controlla(db, frame, LEGHE)
        if motivi:
            print("[!] RIFIUTATO: dati non aggiornati:")
            for m in motivi:
                print(f"      {m}")
            return 2
        vecchi = []
    if a.registra and vecchi and not a.consenti_dati_vecchi:
        print(f"[!] RIFIUTATO: dati fermi da oltre {MAX_ETA} giorni. "
              "Se e' una sosta: --consenti-dati-vecchi")
        return 2

    adesso = datetime.now(timezone.utc)
    fino = (fine_finestra(ora) - ora) if a.auto else timedelta(days=a.giorni)
    cat = api.catalogo(s, MARGINE_INIZIO, fino,
                       tipi=("BOTH_TEAMS_TO_SCORE",), leghe=LEGHE)
    book = api.libri(s, sorted({c["marketId"] for c in cat}))
    cand, scartate = candidati_btts(cat, book, probabilita_dal_modello(models), adesso)
    for x in scartate:
        print(f"  saltata: {x}")
    scelte = seleziona(cand, BANKROLL_VIRTUALE)
    problemi = controlla(scelte, BANKROLL_VIRTUALE)

    print(f"\n=== GOAL/NO GOAL SU CARTA ({len(scelte)}) — {len(cat)} partite su Betfair, "
          f"bankroll virtuale {BANKROLL_VIRTUALE:.0f} EUR ===")
    for x in sorted(scelte, key=lambda x: x["inizio"]):
        q = x["inizio"].astimezone(FUSO)
        print(f"{q:%d/%m %H:%M} {x['lega']:4} {x['casa'][:15] + ' - ' + x['trasferta'][:15]:33} "
              f"{'Goal' if x['selezione'] == 'gg' else 'NoGoal':6} q {x['quota']:5.2f}  "
              f"p {x['p']:.3f}  edge {x['edge']:+5.1%}  {x['stake']:5.2f}")
    for p in problemi:
        print(f"[!!] {p}")

    if not a.registra:
        print("\nANTEPRIMA (carta, nessun denaro): nulla scritto. Per registrare: --registra")
        return 0
    if problemi:
        print("[!] RIFIUTATO: controlli fissi non superati")
        return 2
    if not scelte:
        print("nessuna giocata da registrare")
        return 0

    righe = [{
        "chiave": f"{turno}|{x['market_id']}|{x['selection_id']}", "turno": turno,
        "versione_modello": VERSIONE, "lega": x["lega"],
        "data_partita": str(x["inizio"].astimezone(FUSO).date()),
        "inizio": x["inizio"].isoformat(), "casa": x["casa"], "trasferta": x["trasferta"],
        "evento_bf": x["evento_bf"], "market_id": x["market_id"],
        "selection_id": x["selection_id"], "selezione": x["selezione"],
        "prob_modello": round(x["p"], 6), "quota": x["quota"],
        "size_disponibile": x["size"], "quota_netta": round(x["quota_netta"], 6),
        "edge_netto": round(x["edge"], 6), "bankroll_virtuale": BANKROLL_VIRTUALE,
        "stake": x["stake"],
    } for x in scelte]
    n = db.insert(TABELLA, righe)
    print(f"\nregistrate {n} giocate su carta Goal/No Goal, turno {turno}")
    db.log("carta_btts", "ok", n, turno)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
