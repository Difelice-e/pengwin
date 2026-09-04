"""
Previsioni per le partite in arrivo, selezione value bet e staking Kelly
frazionario — versione cloud. Legge da Supabase e vi scrive le previsioni.

Le costanti dell'esperimento sono identiche a quelle del PC e NON vanno
toccate a esperimento in corso (05-runbook-esperimento.md).

Uso:
  python -m src.report.predict                 # anteprima, non scrive
  python -m src.report.predict --registra      # scrive previsioni e giocate
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.client import client                      # noqa: E402
from src.model.dixon_coles_xg import fit, score_matrix, markets  # noqa: E402
from src.report.dataset import carica, fixtures       # noqa: E402

# --- parametri tarati in Fase 2 e regole dell'esperimento: non modificare ---
W, XI, LOOKBACK = 0.35, 0.0018, 1095
BANKROLL0, KELLY_FRAC, EDGE_MIN, CAP = 1000.0, 0.25, 0.02, 0.01
EDGE_MAX = 0.10
MAX_EXPOSURE, MAX_BETS, MIN_MATCHES, MINBET = 0.20, 25, 8, 2.0
MODELLO, VERSIONE = "dixon_coles_xg", "blend35-65_xi0.0018_w3y"
FUSO = ZoneInfo("Europe/Rome")

MERCATI = [("1", "H", "MaxH"), ("X", "D", "MaxD"), ("2", "A", "MaxA"),
           ("Over 2.5", "O2.5", "MaxO25"), ("Under 2.5", "U2.5", "MaxU25")]
SEL_DB = {"1": ("1X2", "1"), "X": ("1X2", "X"), "2": ("1X2", "2"),
          "Over 2.5": ("OU25", "over"), "Under 2.5": ("OU25", "under")}


def build_models(d: pd.DataFrame, asof: pd.Timestamp) -> dict:
    out = {}
    for div, dl in d.groupby("Div"):
        tr = dl[(dl.MatchDate < asof) & (dl.MatchDate >= asof - pd.Timedelta(days=LOOKBACK))]
        if len(tr) < 200:
            continue
        teams = sorted(set(tr.HomeTeam) | set(tr.AwayTeam))
        ix = {t: i for i, t in enumerate(teams)}
        cnt = pd.concat([tr.HomeTeam, tr.AwayTeam]).value_counts().to_dict()
        wt = np.exp(-XI * (asof - tr.MatchDate).dt.days.values)
        par = fit(tr.HomeTeam.map(ix).values, tr.AwayTeam.map(ix).values,
                  W * tr.FTHG.values + (1 - W) * tr.xgh.values,
                  W * tr.FTAG.values + (1 - W) * tr.xga.values,
                  tr.FTHG.values, tr.FTAG.values, wt, len(teams))
        out[div] = (par, ix, len(tr), cnt, tr.MatchDate.max())
    return out


def kelly(p: float, o: float) -> float:
    b = o - 1.0
    return max(0.0, (p * o - 1.0) / b) if b > 0 else 0.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registra", action="store_true")
    ap.add_argument("--bankroll", type=float, default=BANKROLL0)
    a = ap.parse_args(argv)

    db = client()
    d = carica(db)
    fx = fixtures(db)
    ora = datetime.now(FUSO)
    asof = pd.Timestamp(ora.date())

    models = build_models(d, asof)
    print("modelli stimati:")
    for k, v in models.items():
        eta = (asof - v[4]).days
        avviso = "  <-- DATI VECCHI" if eta > 10 else ""
        print(f"  {k:<4} {v[2]:>4} partite, ultima {v[4].date()} ({eta} giorni fa){avviso}")

    if fx.empty:
        print("\nnessun fixture in tabella: lanciare prima l'ingest football_data --fixtures")
        return 1

    # esclude le partite gia' iniziate o troppo imminenti (margine 15 minuti)
    kick = pd.to_datetime(fx["data"].astype(str) + " " + fx["Time"].fillna("00:00").astype(str),
                          errors="coerce")
    limite = pd.Timestamp(ora.replace(tzinfo=None)) + pd.Timedelta(minutes=15)
    scartate = int(((kick.notna()) & (kick <= limite)).sum())
    fx = fx[(kick > limite) | (kick.isna() & (fx.MatchDate > asof))]
    if scartate:
        print(f"escluse {scartate} partite gia' iniziate o troppo imminenti")

    righe, saltate = [], []
    for _, m in fx.iterrows():
        if m.Div not in models:
            saltate.append((m.Div, m.HomeTeam, m.AwayTeam, "lega senza modello"))
            continue
        par, ix, _, cnt, _ = models[m.Div]
        magre = [t for t in (m.HomeTeam, m.AwayTeam) if cnt.get(t, 0) < MIN_MATCHES]
        if magre or m.HomeTeam not in ix or m.AwayTeam not in ix:
            saltate.append((m.Div, m.HomeTeam, m.AwayTeam,
                            "storico insufficiente: " +
                            ", ".join(f"{t} ({cnt.get(t,0)})" for t in (magre or [m.HomeTeam]))))
            continue
        mk = markets(score_matrix(par, ix[m.HomeTeam], ix[m.AwayTeam]))
        for sel, pk, ocol in MERCATI:
            o = m.get(ocol)
            if o is None or pd.isna(o) or float(o) <= 1.01:
                continue
            o = float(o)
            p = float(mk[pk])
            edge = p * o - 1
            f = min(kelly(p, o) * KELLY_FRAC, CAP)
            stake = f * a.bankroll
            ok = (EDGE_MIN <= edge <= EDGE_MAX) and stake >= MINBET
            righe.append({"Div": m.Div, "MatchDate": m.MatchDate, "Time": m.Time,
                          "HomeTeam": m.HomeTeam, "AwayTeam": m.AwayTeam,
                          "sel": sel, "p": p, "odds_max": o, "edge": edge,
                          "sospetta": edge > EDGE_MAX,
                          "stake": round(stake, 2) if ok else 0.0})

    out = pd.DataFrame(righe)
    if out.empty:
        print("\nnessuna partita candidata: niente da valutare, nessuna giocata")
        for s in saltate[:8]:
            print("   ", s)
        return 0

    susp = out[out.sospetta]
    if len(susp):
        print(f"\nscartate {len(susp)} righe con edge > {EDGE_MAX:.0%} "
              f"(implausibili: errore di stima, non valore)")

    sel = out[out.stake > 0].sort_values(["MatchDate", "edge"]).head(MAX_BETS).copy()
    tot, lim = sel.stake.sum(), MAX_EXPOSURE * a.bankroll
    if tot > lim:
        sel["stake"] = (sel.stake * lim / tot).round(2)
        sel = sel[sel.stake >= MINBET]
        print(f"esposizione riscalata da {tot:.0f} a {sel.stake.sum():.0f} EUR "
              f"(tetto {MAX_EXPOSURE:.0%})")

    print(f"\nrighe mercato valutate: {len(out)} | scartate {len(saltate)} partite")
    print(f"\n=== GIOCATE SELEZIONATE (edge {EDGE_MIN:.0%}-{EDGE_MAX:.0%}, "
          f"Kelly 1/4 su {a.bankroll:.0f} EUR) ===")
    if sel.empty:
        print("nessuna")
    else:
        for _, r in sel.sort_values(["MatchDate", "Time"]).iterrows():
            print(f"{r.MatchDate.date()} {r.Div:4} "
                  f"{r.HomeTeam[:16] + ' - ' + r.AwayTeam[:16]:36} {r.sel:10} "
                  f"p {r.p:.3f}  q {r.odds_max:5.2f}  edge {r.edge:+6.1%}  {r.stake:6.2f}")
        print(f"\ntotale esposto: {sel.stake.sum():.2f} EUR su {len(sel)} giocate")

    if not a.registra:
        print("\n(anteprima — rilanciare con --registra per salvare su Supabase)")
        return 0

    turno = f"{asof.isocalendar()[0]}-W{asof.isocalendar()[1]:02d}"

    # Confine del turno gia' selezionato (stessa regola del ledger sul PC).
    # Senza questo, una seconda passata sceglierebbe partite che alla prima non
    # superavano la soglia — il modello nel frattempo si e' ristimato — gonfiando
    # l'esposizione e invalidando la previsione pre-registrata. Le previsioni sono
    # immutabili: un doppio inserimento non si annulla.
    gia = db.select("giocate", colonne="turno,data_partita",
                    ordina="data_partita.desc", limite=1)
    if gia:
        ultima = gia[0]["data_partita"]
        nuove = [r for _, r in sel.iterrows() if str(r.MatchDate.date()) > ultima]
        if len(nuove) < len(sel):
            print(f"\n[!] RIFIUTATO: {len(sel) - len(nuove)} delle {len(sel)} giocate "
                  f"riguardano partite non successive all'ultima gia' registrata ({ultima}).")
            print("    Il turno risulta gia' selezionato. Nessuna scrittura effettuata.")
            db.log("predict", "saltato", 0, f"turno gia' selezionato, ultima {ultima}")
            return 0
    if any(g["turno"] == turno for g in db.select("giocate", colonne="turno")):
        print(f"\n[!] RIFIUTATO: il turno {turno} ha gia' delle giocate registrate.")
        db.log("predict", "saltato", 0, f"turno {turno} gia' presente")
        return 0
    creata = ora.isoformat()
    prev = [{"creata_il": creata, "modello": MODELLO, "versione": VERSIONE,
             "lega": r.Div, "data_partita": str(r.MatchDate.date()),
             "casa": r.HomeTeam, "trasferta": r.AwayTeam,
             "mercato": SEL_DB[r.sel][0], "selezione": SEL_DB[r.sel][1],
             "prob": round(r.p, 6), "quota_offerta": r.odds_max,
             "edge": round(r.edge, 6), "note": f"turno {turno}"}
            for _, r in sel.iterrows()]
    creati = db.insert("previsioni", prev, ritorna=True)
    idx = {(p["data_partita"], p["casa"], p["trasferta"], p["mercato"], p["selezione"]): p["id"]
           for p in creati}
    giocate = [{"previsione_id": idx.get((str(r.MatchDate.date()), r.HomeTeam, r.AwayTeam,
                                          SEL_DB[r.sel][0], SEL_DB[r.sel][1])),
                "piazzata_il": creata, "turno": turno, "lega": r.Div,
                "data_partita": str(r.MatchDate.date()), "casa": r.HomeTeam,
                "trasferta": r.AwayTeam, "mercato": SEL_DB[r.sel][0],
                "selezione": SEL_DB[r.sel][1], "quota": r.odds_max,
                "stake": r.stake, "bankroll_al_momento": a.bankroll,
                "prob_modello": round(r.p, 6), "edge": round(r.edge, 6), "esito": "aperta"}
               for _, r in sel.iterrows()]
    n = db.insert("giocate", giocate)
    print(f"\nregistrate {len(creati)} previsioni e {n} giocate (turno {turno})")
    db.log("predict", "ok", n, f"turno {turno}, bankroll {a.bankroll}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
