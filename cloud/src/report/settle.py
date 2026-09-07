"""
Contabilizza le giocate confrontandole con i risultati, e calcola il CLV.
Non tocca mai una giocata gia' contabilizzata *e con CLV calcolato*.

CLV (Closing Line Value) = quota_presa / quota_di_chiusura - 1.
E' il segnale precoce e affidabile richiesto dal par.6 della specifica: dice se
la quota ottenuta era migliore di quella a cui il mercato ha chiuso, e non
dipende dal fatto che la singola giocata sia poi risultata vinta o persa.

Contabilizzazione a due fasi (football-data pubblica i CSV stagionali con
ore/giorni di ritardo rispetto alla fine delle partite):

  FASE 1 — giocate ancora `aperta`
    - risultato presente in `partite` (football-data)  -> contabilizzo completo
      (esito + ritorno + quota_chiusura + CLV): giocata definitiva.
    - altrimenti risultato presente in `xg_partite` (Understat) -> contabilizzo
      PROVVISORIO (esito + ritorno). `clv` e `quota_chiusura` restano NULL e la
      nota porta il marcatore "Understat": la giocata e' chiusa nel P&L ma il
      CLV manca ancora.
    - nessuna delle due -> resta aperta.

  FASE 2 — giocate gia' chiuse ma con `clv` NULL
    - se nel frattempo football-data ha pubblicato la partita: riempio
      `quota_chiusura` e `clv` dai suoi dati e ri-verifico l'esito. Se il
      risultato di football-data contraddice quello provvisorio di Understat,
      football-data prevale e la rettifica viene annotata in `note` e nel log.

Uso:
  python -m src.report.settle            # anteprima
  python -m src.report.settle --scrivi
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.client import client                 # noqa: E402
from src.ingest.squadre import a_understat       # noqa: E402
from src.ingest.understat import LEGA_FD         # noqa: E402

FUSO = ZoneInfo("Europe/Rome")

# codice football-data -> codice lega Understat (per agganciare xg_partite)
FD_LEGA = {v: k for k, v in LEGA_FD.items()}     # I1 -> Serie_A
# tolleranza sul giorno quando si aggancia un risultato Understat a una giocata:
# le due fonti datano diversamente le partite spostate.
FINESTRA_GIORNI = 5
# marcatore in `note` per riconoscere una contabilizzazione provvisoria
TAG_PROVV = "Understat"

# selezione -> colonna della quota di CHIUSURA massima, per il CLV
CHIUSURA = {("1X2", "1"): "q_max_1", ("1X2", "X"): "q_max_x", ("1X2", "2"): "q_max_2",
            ("OU25", "over"): "q_max_over25", ("OU25", "under"): "q_max_under25"}


def vinta(mercato: str, selezione: str, gc: int, gt: int) -> bool | None:
    if mercato == "1X2":
        return {"1": gc > gt, "X": gc == gt, "2": gc < gt}.get(selezione)
    if mercato == "OU25":
        return {"over": gc + gt > 2.5, "under": gc + gt < 2.5}.get(selezione)
    if mercato == "GG":
        return {"gg": gc > 0 and gt > 0, "ng": gc == 0 or gt == 0}.get(selezione)
    return None


def _data(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _da_football_data(g: dict, r: dict, ora: str) -> dict | None:
    """Contabilizzazione definitiva a partire da una riga di `partite`."""
    if r.get("gol_casa") is None or r.get("gol_trasferta") is None:
        return None
    gc, gt = int(r["gol_casa"]), int(r["gol_trasferta"])
    w = vinta(g["mercato"], g["selezione"], gc, gt)
    if w is None:
        return None
    quota, stake = float(g["quota"]), float(g["stake"])
    chius = r.get(CHIUSURA.get((g["mercato"], g["selezione"]), ""))
    clv = round(quota / float(chius) - 1, 6) if chius else None
    return {
        "id": g["id"], "esito": "vinta" if w else "persa",
        "ritorno": round(stake * quota, 2) if w else 0.0,
        "quota_chiusura": float(chius) if chius else None,
        "clv": clv, "chiusa_il": ora,
        "note": f"risultato {gc}-{gt}",
    }


def _da_understat(g: dict, gc: int, gt: int, ora: str) -> dict | None:
    """Contabilizzazione provvisoria: esito e ritorno, ma niente CLV."""
    w = vinta(g["mercato"], g["selezione"], gc, gt)
    if w is None:
        return None
    quota, stake = float(g["quota"]), float(g["stake"])
    return {
        "id": g["id"], "esito": "vinta" if w else "persa",
        "ritorno": round(stake * quota, 2) if w else 0.0,
        "quota_chiusura": None, "clv": None, "chiusa_il": ora,
        "note": f"risultato {gc}-{gt} · {TAG_PROVV}, CLV in attesa di football-data",
    }


def _risultato_understat(g: dict, idx_us: dict) -> tuple[int, int] | None:
    """Cerca il risultato di una giocata in `xg_partite` (nomi Understat)."""
    lega_us = FD_LEGA.get(g["lega"])
    if not lega_us:
        return None
    cand = idx_us.get((lega_us, a_understat(g["casa"]), a_understat(g["trasferta"])))
    if not cand:
        return None
    d0 = _data(g["data_partita"])
    scelta, scarto_min = None, None
    for r in cand:
        dr = _data(r.get("datetime"))
        if d0 and dr:
            scarto = abs((dr - d0).days)
            if scarto > FINESTRA_GIORNI:
                continue
            if scarto_min is None or scarto < scarto_min:
                scelta, scarto_min = r, scarto
        elif scelta is None:
            scelta = r
    if not scelta or scelta.get("gol_casa") is None or scelta.get("gol_trasferta") is None:
        return None
    return int(scelta["gol_casa"]), int(scelta["gol_trasferta"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scrivi", action="store_true")
    a = ap.parse_args(argv)

    db = client()
    # una sola lettura: tutto cio' che non ha ancora un CLV.
    #  - esito 'aperta'          -> da contabilizzare (fase 1)
    #  - esito 'vinta'/'persa'   -> gia' chiusa ma CLV mancante (fase 2):
    #    contabilizzata provvisoriamente da Understat, oppure da football-data
    #    quando le colonne di chiusura mancavano. La fase 2 prova a completarle;
    #    solo quelle col marcatore Understat si contano "in attesa".
    senza_clv = db.select("giocate", colonne="*", filtri={"clv": "is.null"})
    aperte = [g for g in senza_clv if g["esito"] == "aperta"]
    da_completare = [g for g in senza_clv if g["esito"] in ("vinta", "persa")]
    print(f"giocate aperte: {len(aperte)} | chiuse senza CLV: {len(da_completare)}")
    if not aperte and not da_completare:
        print("niente da contabilizzare")
        return 0

    date_utili = sorted(g["data_partita"] for g in aperte + da_completare)
    da = date_utili[0]

    fd = db.select("partite",
                   colonne="lega,data,casa,trasferta,gol_casa,gol_trasferta,"
                           "q_max_1,q_max_x,q_max_2,q_max_over25,q_max_under25",
                   filtri={"data": f"gte.{da}", "gol_casa": "not.is.null"})
    idx_fd = {(r["lega"], r["data"], r["casa"], r["trasferta"]): r for r in fd}

    us = db.select("xg_partite",
                   colonne="lega_us,datetime,casa,trasferta,gol_casa,gol_trasferta",
                   filtri={"datetime": f"gte.{da}", "disputata": "is.true"})
    idx_us: dict = {}
    for r in us:
        idx_us.setdefault((r["lega_us"], r["casa"], r["trasferta"]), []).append(r)

    ora = datetime.now(FUSO).isoformat()
    agg: list[dict] = []

    # ---- FASE 1 : giocate aperte -----------------------------------------
    n_fd = n_us = in_attesa = senza_chiusura = 0
    for g in aperte:
        r = idx_fd.get((g["lega"], g["data_partita"], g["casa"], g["trasferta"]))
        if r:
            u = _da_football_data(g, r, ora)
            if u is None:
                continue
            if u["clv"] is None:
                senza_chiusura += 1
            agg.append(u)
            n_fd += 1
            continue
        ris = _risultato_understat(g, idx_us)
        if ris:
            u = _da_understat(g, ris[0], ris[1], ora)
            if u is not None:
                agg.append(u)
                n_us += 1
                continue
        in_attesa += 1

    # ---- FASE 2 : chiuse senza CLV -> football-data disponibile? ----------
    n_clv = n_rettifiche = provv_restanti = 0
    for g in da_completare:
        provvisoria = TAG_PROVV in (g.get("note") or "")
        r = idx_fd.get((g["lega"], g["data_partita"], g["casa"], g["trasferta"]))
        u = _da_football_data(g, r, ora) if r else None
        if u is None or u["clv"] is None:
            if provvisoria:
                provv_restanti += 1
            continue
        if u["esito"] != g["esito"]:
            n_rettifiche += 1
            u["note"] += f" [rettifica: {TAG_PROVV} dava {g['esito']}]"
        n_clv += 1
        agg.append(u)

    print(f"[fase 1] football-data: {n_fd}"
          f"{f' (di cui {senza_chiusura} senza chiusura)' if senza_chiusura else ''}"
          f" | Understat (provvisorio): {n_us} | in attesa di risultato: {in_attesa}")
    print(f"[fase 2] CLV completato da football-data: {n_clv}"
          f"{f' | RETTIFICHE esito: {n_rettifiche}' if n_rettifiche else ''}"
          f" | provvisorie ancora senza CLV: {provv_restanti}")

    if not a.scrivi:
        for u in agg[:10]:
            print(f"   #{u['id']:>3} {u['esito']:<6} ritorno {u['ritorno']:>7.2f} "
                  f"CLV {u['clv'] if u['clv'] is not None else '-':<9}  {u['note']}")
        print("\n(anteprima — rilanciare con --scrivi per applicare)")
        return 0

    for u in agg:
        i = u.pop("id")
        db.update("giocate", {"id": f"eq.{i}"}, u)
    print(f"scritte {len(agg)} contabilizzazioni")

    tutte = db.select("giocate", colonne="stake,ritorno,esito,clv,note")
    ch = [g for g in tutte if g["esito"] in ("vinta", "persa")]
    if ch:
        provv = sum(1 for g in ch if g["clv"] is None and TAG_PROVV in (g.get("note") or ""))
        st = sum(float(g["stake"]) for g in ch)
        rt = sum(float(g["ritorno"] or 0) for g in ch)
        clvs = [float(g["clv"]) for g in ch if g["clv"] is not None]
        print(f"\nconcluse {len(ch)}"
              f"{f' (di cui {provv} provvisorie, CLV in attesa)' if provv else ''}"
              f" | volume {st:.2f} | P&L {rt-st:+.2f} | ROI {(rt-st)/st*100:+.2f}%")
        print(f"vinte {sum(1 for g in ch if g['esito']=='vinta')}/{len(ch)}")
        if clvs:
            pos = sum(1 for c in clvs if c > 0)
            print(f"CLV medio {sum(clvs)/len(clvs)*100:+.2f}%  |  positivo su {pos}/{len(clvs)} "
                  f"({pos/len(clvs)*100:.0f}%)")
            print("Il CLV, non il ROI, e' il segnale che conta su questi numeri.")
    db.log("settle", "ok", len(agg),
           f"fd={n_fd} understat={n_us} clv_completati={n_clv} rettifiche={n_rettifiche}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
