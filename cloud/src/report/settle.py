"""
Contabilizza le giocate aperte confrontandole con i risultati in `partite`,
e calcola il CLV. Non tocca mai una giocata gia' contabilizzata.

CLV (Closing Line Value) = quota_presa / quota_di_chiusura - 1.
E' il segnale precoce e affidabile richiesto dal par.6 della specifica: dice se
la quota ottenuta era migliore di quella a cui il mercato ha chiuso, e non
dipende dal fatto che la singola giocata sia poi risultata vinta o persa.

Uso:
  python -m src.report.settle            # anteprima
  python -m src.report.settle --scrivi
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.client import client  # noqa: E402

FUSO = ZoneInfo("Europe/Rome")

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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scrivi", action="store_true")
    a = ap.parse_args(argv)

    db = client()
    aperte = db.select("giocate", colonne="*", filtri={"esito": "eq.aperta"})
    print(f"giocate aperte: {len(aperte)}")
    if not aperte:
        print("niente da contabilizzare")
        return 0

    date = sorted({g["data_partita"] for g in aperte})
    ris = db.select("partite",
                    colonne="lega,data,casa,trasferta,gol_casa,gol_trasferta,"
                            "q_max_1,q_max_x,q_max_2,q_max_over25,q_max_under25",
                    filtri={"data": f"gte.{date[0]}", "gol_casa": "not.is.null"})
    idx = {(r["lega"], r["data"], r["casa"], r["trasferta"]): r for r in ris}

    ora = datetime.now(FUSO).isoformat()
    fatte, in_attesa, senza_chiusura = [], 0, 0
    for g in aperte:
        k = (g["lega"], g["data_partita"], g["casa"], g["trasferta"])
        r = idx.get(k)
        if not r:
            in_attesa += 1
            continue
        gc, gt = int(r["gol_casa"]), int(r["gol_trasferta"])
        w = vinta(g["mercato"], g["selezione"], gc, gt)
        if w is None:
            continue
        quota, stake = float(g["quota"]), float(g["stake"])
        chius = r.get(CHIUSURA.get((g["mercato"], g["selezione"]), ""))
        if chius:
            clv = round(quota / float(chius) - 1, 6)
        else:
            clv = None
            senza_chiusura += 1
        fatte.append({
            "id": g["id"], "esito": "vinta" if w else "persa",
            "ritorno": round(stake * quota, 2) if w else 0.0,
            "quota_chiusura": float(chius) if chius else None,
            "clv": clv, "chiusa_il": ora,
            "note": f"risultato {gc}-{gt}",
        })

    print(f"contabilizzabili: {len(fatte)} | in attesa di risultato: {in_attesa}")
    if senza_chiusura:
        print(f"[!] {senza_chiusura} senza quota di chiusura: CLV non calcolabile")

    if not a.scrivi:
        for f in fatte[:8]:
            print(f"   #{f['id']:>3} {f['esito']:<6} ritorno {f['ritorno']:>7.2f} "
                  f"CLV {f['clv'] if f['clv'] is not None else '-'}  {f['note']}")
        print("\n(anteprima — rilanciare con --scrivi per applicare)")
        return 0

    for f in fatte:
        i = f.pop("id")
        db.update("giocate", {"id": f"eq.{i}"}, f)
    print(f"contabilizzate {len(fatte)} giocate")

    tutte = db.select("giocate", colonne="stake,ritorno,esito,clv")
    ch = [g for g in tutte if g["esito"] in ("vinta", "persa")]
    if ch:
        st = sum(float(g["stake"]) for g in ch)
        rt = sum(float(g["ritorno"] or 0) for g in ch)
        clvs = [float(g["clv"]) for g in ch if g["clv"] is not None]
        print(f"\nconcluse {len(ch)} | volume {st:.2f} | P&L {rt-st:+.2f} | ROI {(rt-st)/st*100:+.2f}%")
        print(f"vinte {sum(1 for g in ch if g['esito']=='vinta')}/{len(ch)}")
        if clvs:
            pos = sum(1 for c in clvs if c > 0)
            print(f"CLV medio {sum(clvs)/len(clvs)*100:+.2f}%  |  positivo su {pos}/{len(clvs)} "
                  f"({pos/len(clvs)*100:.0f}%)")
            print("Il CLV, non il ROI, e' il segnale che conta su questi numeri.")
    db.log("settle", "ok", len(fatte))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
