"""
Contabilizza il Goal/No Goal su carta (`carta_btts`) nello stesso giro di GitHub
che aggiorna risultati e xG, subito dopo `settle`.

Perche' qui e non solo sul server. Il timer del server (`carta_btts
--contabilizza`, ogni giorno alle 16:15) legge solo `partite`, cioe'
football-data, che pubblica il weekend il lunedi' notte. Su Supabase quei dati
arrivano poi col giro di GitHub, che partiva con ore di ritardo (fra le 12 e le
16:30). Risultato: le giocate del sabato restavano aperte fino al martedi' o al
mercoledi', mentre quelle dell'esperimento principale si chiudevano la
domenica grazie a Understat. Qui il Goal/No Goal fa come l'esperimento
principale:

  1. giocate aperte: risultato in `partite` (football-data), altrimenti in
     `xg_partite` (Understat);
  2. giocate chiuse coi gol: se football-data contraddice i gol usati, prevale
     football-data e l'esito si rettifica (succede di rado: i gol sono gol);
  3. giocate chiuse da Betfair senza gol (esito dal mercato, vedi
     `src/reale/carta_btts.py` sul server): si aggiungono i gol per la pagina.
     Fa fede Betfair: se i gol dicono altro, non si scrive niente e lo si
     segnala nel log.

Partite sospese o rinviate: la giocata resta aperta finche' la partita non si
gioca, anche settimane dopo (decisione dell'utente, 13/10/2026, per
Vallecano-Bilbao sospesa sullo 0-0). Per questo il risultato si cerca da 3
giorni prima della data prevista in poi, non solo entro 3 giorni: nello stesso
campionato e nella stessa stagione la coppia casa-trasferta si incontra una
volta sola.

Esito e profitto sono quelli di `src/reale/carta_btts.py` (server-betfair):
commissione 4,5% solo sulle vincite. Il CLV non si tocca: lo scrive il server
alla chiusura di Betfair. Se manca, il ripiego pre-registrato e' l'ultima
fotografia periodica prima del via, come sul server.

Rilanciabile: lavora solo su righe aperte, o su chiuse da completare o da
rettificare. Il timer del server resta come secondo percorso: le strade
scrivono la stessa cosa.

Uso:
  python -m src.report.settle_btts            # anteprima
  python -m src.report.settle_btts --scrivi
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.ingest.squadre import a_understat       # noqa: E402
from src.ingest.understat import LEGA_FD         # noqa: E402

TABELLA = "carta_btts"
COMMISSIONE = 0.045          # come src.ingest.betfair.COMMISSIONE sul server
PRIMA = 3                    # giorni di anticipo accettati (partite anticipate)
DOPO = 120                   # giorni di ritardo accettati (recuperi): resta nella stagione
RETTIFICHE_GIORNI = 21       # per quanto tempo si ricontrollano le gia' chiuse
ATTESA_DOPO_VIA = timedelta(hours=2, minutes=30)
FD_LEGA = {v: k for k, v in LEGA_FD.items()}     # SP1 -> La_liga


def esito(selezione: str, gc: int, gt: int) -> str:
    entrambe = gc > 0 and gt > 0
    return "vinta" if (selezione == "gg") == entrambe else "persa"


def profitto(esito_: str, stake: float, quota: float) -> float:
    if esito_ == "vinta":
        return round(stake * (quota - 1) * (1 - COMMISSIONE), 2)
    if esito_ == "persa":
        return -round(stake, 2)
    return 0.0


def _data(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _piu_vicina(righe: list[dict], prevista: date | None, campo: str) -> dict | None:
    """La partita giocata piu' vicina alla data prevista, da PRIMA giorni prima a DOPO giorni dopo."""
    if prevista is None:
        return None
    migliore, scarto_min = None, None
    for r in righe:
        if r.get("gol_casa") is None or r.get("gol_trasferta") is None:
            continue
        d = _data(r.get(campo))
        if d is None or not (-PRIMA <= (d - prevista).days <= DOPO):
            continue
        scarto = abs((d - prevista).days)
        if scarto_min is None or scarto < scarto_min:
            migliore, scarto_min = r, scarto
    return migliore


def risultato_fd(b: dict, idx_fd: dict) -> tuple[int, int] | None:
    r = _piu_vicina(idx_fd.get((b["lega"], b["casa"], b["trasferta"]), []),
                    _data(b["data_partita"]), "data")
    return (int(r["gol_casa"]), int(r["gol_trasferta"])) if r else None


def risultato_us(b: dict, idx_us: dict) -> tuple[int, int] | None:
    lega_us = FD_LEGA.get(b["lega"])
    if not lega_us:
        return None
    r = _piu_vicina(idx_us.get((lega_us, a_understat(b["casa"]), a_understat(b["trasferta"])), []),
                    _data(b["data_partita"]), "datetime")
    return (int(r["gol_casa"]), int(r["gol_trasferta"])) if r else None


def chiusura_da_fotografia(db, b: dict) -> dict:
    """Ripiego pre-registrato: l'ultima fotografia periodica prima del via."""
    try:
        f = db.select("quote_snapshot", colonne="il,back", filtri={
            "market_id": f"eq.{b['market_id']}", "selection_id": f"eq.{b['selection_id']}",
            "il": f"lt.{b['inizio']}", "back": "not.is.null"}, ordina="il.desc", limite=1)
    except Exception:                                        # noqa: BLE001
        return {}
    if not f:
        return {}
    back = float(f[0]["back"])
    return {"quota_chiusura": back, "chiusura_il": f[0]["il"], "chiusura_fonte": "fotografia",
            "clv": round(float(b["quota"]) / back - 1, 6)}


def calcola(btts: list[dict], fd: list[dict], us: list[dict], adesso: datetime) -> dict:
    """Decide cosa scrivere. Nessuna lettura ne' scrittura: tutta la logica e' qui."""
    idx_fd: dict = {}
    for r in fd:
        idx_fd.setdefault((r["lega"], r["casa"], r["trasferta"]), []).append(r)
    idx_us: dict = {}
    for r in us:
        idx_us.setdefault((r["lega_us"], r["casa"], r["trasferta"]), []).append(r)

    ora, oggi = adesso.isoformat(), adesso.date()
    scritture, discordanze = [], []
    conta = {"fd": 0, "understat": 0, "gol_aggiunti": 0, "rettifiche": 0, "in_attesa": 0}

    def con_esito(b, ris, fonte):
        e = esito(b["selezione"], *ris)
        scritture.append({"id": b["id"], "fonte": fonte, "valori": {
            "gol_casa": ris[0], "gol_trasferta": ris[1], "esito": e,
            "profitto_netto": profitto(e, float(b["stake"]), float(b["quota"])),
            "contabilizzata_il": ora}})
        conta[fonte] += 1

    for b in btts:
        inizio = datetime.fromisoformat(str(b["inizio"]).replace("Z", "+00:00"))
        if b.get("esito") is None:
            if inizio > adesso - ATTESA_DOPO_VIA:
                continue                                       # non ancora finita
            ris = risultato_fd(b, idx_fd)
            if ris is not None:
                con_esito(b, ris, "fd")
                continue
            ris = risultato_us(b, idx_us)
            if ris is not None:
                con_esito(b, ris, "understat")
            else:
                conta["in_attesa"] += 1
            continue

        if b["esito"] not in ("vinta", "persa"):
            continue
        prevista = _data(b["data_partita"])
        if prevista is None or (oggi - prevista).days > RETTIFICHE_GIORNI:
            continue
        if b.get("gol_casa") is None:
            # chiusa da Betfair: fa fede Betfair, qui si aggiungono solo i gol
            ris = risultato_fd(b, idx_fd) or risultato_us(b, idx_us)
            if ris is None:
                continue
            if esito(b["selezione"], *ris) == b["esito"]:
                scritture.append({"id": b["id"], "fonte": "gol_aggiunti",
                                  "valori": {"gol_casa": ris[0], "gol_trasferta": ris[1]}})
                conta["gol_aggiunti"] += 1
            else:
                discordanze.append(f"#{b['id']} {b['casa']}-{b['trasferta']} {b['selezione']}: "
                                   f"Betfair {b['esito']}, risultato {ris[0]}-{ris[1]}")
            continue
        ris = risultato_fd(b, idx_fd)
        if ris is not None and ris != (int(b["gol_casa"]), int(b["gol_trasferta"])):
            con_esito(b, ris, "rettifiche")
    return {"scritture": scritture, "conta": conta, "discordanze": discordanze}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scrivi", action="store_true")
    a = ap.parse_args(argv)

    from src.db.client import client             # qui: il resto si prova senza rete
    db = client()
    adesso = datetime.now(timezone.utc)
    try:
        btts = db.select(TABELLA, colonne="*")
    except Exception as e:                       # noqa: BLE001  tabella assente: niente da fare
        print(f"carta_btts non leggibile ({e}): salto")
        return 0
    da_fare = [b for b in btts if b.get("esito") in (None, "vinta", "persa")]
    if not da_fare:
        print("Goal/No Goal: niente da contabilizzare")
        return 0
    da = min(date.fromisoformat(str(b["data_partita"])[:10]) for b in da_fare)
    da = (da - timedelta(days=PRIMA)).isoformat()
    fd = db.select("partite", colonne="lega,data,casa,trasferta,gol_casa,gol_trasferta",
                   filtri={"data": f"gte.{da}"})
    us = db.select("xg_partite", colonne="lega_us,datetime,casa,trasferta,gol_casa,gol_trasferta",
                   filtri={"datetime": f"gte.{da}", "disputata": "is.true"})

    out = calcola(da_fare, fd, us, adesso)
    c = out["conta"]
    per_id = {b["id"]: b for b in btts}
    for s in out["scritture"]:
        b, v = per_id[s["id"]], s["valori"]
        print(f"   #{s['id']:>3} {b['lega']:<4} {b['casa']} - {b['trasferta']} {b['selezione']}: "
              f"{v.get('esito', b['esito'])} ({v['gol_casa']}-{v['gol_trasferta']}) [{s['fonte']}]")
    for d in out["discordanze"]:
        print(f"   [!] discordanza, lasciata com'e': {d}")
    riassunto = (f"GitHub: fd={c['fd']} understat={c['understat']} rettifiche={c['rettifiche']} "
                 f"gol_aggiunti={c['gol_aggiunti']} | {c['in_attesa']} in attesa")
    if out["discordanze"]:
        riassunto += " | DISCORDANZE Betfair/risultato: " + "; ".join(out["discordanze"])
    print(riassunto)
    if not a.scrivi:
        print("(anteprima — rilanciare con --scrivi per applicare)")
        return 0

    for s in out["scritture"]:
        valori = dict(s["valori"])
        b = per_id[s["id"]]
        if "esito" in valori and b.get("quota_chiusura") is None:
            valori.update(chiusura_da_fotografia(db, b))
        db.update(TABELLA, {"id": f"eq.{s['id']}"}, valori)
    # una discordanza va guardata da una persona: "errore" la fa comparire nel riepilogo
    db.log("carta_btts_contabilizza", "errore" if out["discordanze"] else "ok",
           len(out["scritture"]), riassunto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
