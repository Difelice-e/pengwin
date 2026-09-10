"""
Previsioni per le partite in arrivo, selezione value bet e staking Kelly
frazionario — versione cloud. Legge da Supabase e vi scrive le previsioni.

Le costanti del modello sono identiche a quelle del PC e NON vanno toccate a
esperimento in corso (05-runbook-esperimento.md): W, XI, LOOKBACK, KELLY_FRAC,
EDGE_MIN/MAX, CAP, MAX_EXPOSURE, MAX_BETS restano quelle.

Cambia invece il **prezzo di selezione**, e con esso il criterio: dal 10/9/2026
si seleziona sul miglior back Betfair al netto della commissione, non su MaxH
di football-data. Le giocate registrate da qui in avanti non sono confrontabili
con le precedenti: quelle vecchie sono state scelte contro la quota massima fra
~20 bookmaker in apertura, che e' il massimo di un campione e sovrastima
l'edge. Il marcatore `SELEZIONE` in `previsioni.note` separa le due serie;
ROI e CLV vanno calcolati per serie, non mescolati.

Nello stesso passaggio entrano in gioco Over 2.5 e Under 2.5, che erano in
MERCATI ma non erano mai stati giocabili perche' le colonne di prezzo
corrispondenti non esistevano nello schema (vedi sql/quote_over_under.sql).

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
from src.ingest.betfair import (COMMISSIONE, arrotonda_stake,    # noqa: E402
                                quota_netta)
from src.model.dixon_coles_xg import fit, score_matrix, markets  # noqa: E402
from src.report.dataset import carica, fixtures       # noqa: E402

# --- parametri tarati in Fase 2 e regole dell'esperimento: non modificare ---
W, XI, LOOKBACK = 0.35, 0.0018, 1095
BANKROLL0, KELLY_FRAC, EDGE_MIN, CAP = 1000.0, 0.25, 0.02, 0.01
EDGE_MAX = 0.10
MAX_EXPOSURE, MAX_BETS, MIN_MATCHES, MINBET = 0.20, 25, 8, 2.0
MODELLO, VERSIONE = "dixon_coles_xg", "blend35-65_xi0.0018_w3y"
FUSO = ZoneInfo("Europe/Rome")

# Marcatore della fonte di prezzo, scritto in `previsioni.note`, che il trigger
# di immutabilita' protegge. NON in `giocate.note`: settle.py lo sovrascrive
# alla contabilizzazione. Le giocate si riconducono alla fase via previsione_id.
SELEZIONE = "prezzo-betfair-comm4.5"

# Prezzo di selezione: il miglior back sull'exchange, cioe' dove la giocata
# verra' eseguita. Prima erano MaxH/MaxO25 di football-data, la massima fra ~20
# bookmaker e in apertura: il massimo di un campione, quindi distorto all'insu'
# per costruzione, di un allibratore qualsiasi e non necessariamente
# disponibile. L'edge misurato contro quel prezzo sovrastimava il vantaggio.
MERCATI = [("1", "H", "q_bf_1"), ("X", "D", "q_bf_x"), ("2", "A", "q_bf_2"),
           ("Over 2.5", "O2.5", "q_bf_over25"),
           ("Under 2.5", "U2.5", "q_bf_under25")]
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


MAX_ETA = 10          # giorni oltre i quali i dati di un campionato sono "vecchi"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registra", action="store_true")
    ap.add_argument("--bankroll", type=float, default=BANKROLL0)
    ap.add_argument("--consenti-dati-vecchi", action="store_true",
                    dest="consenti_dati_vecchi",
                    help="registra anche con campionati fermi da oltre "
                         f"{MAX_ETA} giorni. Scelta deliberata di una persona: "
                         "annotare il turno come degradato nel runbook.")
    a = ap.parse_args(argv)

    db = client()
    d = carica(db)
    fx = fixtures(db)
    ora = datetime.now(FUSO)
    asof = pd.Timestamp(ora.date())

    models = build_models(d, asof)
    print("modelli stimati:")
    vecchi = []
    for k, v in models.items():
        eta = (asof - v[4]).days
        if eta > MAX_ETA:
            vecchi.append((k, v[4].date(), eta))
        avviso = "  <-- DATI VECCHI" if eta > MAX_ETA else ""
        print(f"  {k:<4} {v[2]:>4} partite, ultima {v[4].date()} ({eta} giorni fa){avviso}")

    # Il controllo sui dati vecchi e' un RIFIUTO del programma, non un avviso da
    # leggere. Prima stava nel prompt di un'attivita' pianificata: qualcuno
    # doveva guardare l'output e decidere di fermarsi. Un turno registrato non
    # si annulla (le previsioni sono immutabili), quindi la condizione pericolosa
    # deve bloccare la scrittura da sola, senza dipendere da chi legge.
    # E' l'errore che ha viziato il turno del 4 settembre 2026: Bundesliga ferma
    # al 16 maggio, previsioni calcolate lo stesso.
    if a.registra and vecchi and not a.consenti_dati_vecchi:
        print("\n[!] RIFIUTATO: dati fermi da oltre "
              f"{MAX_ETA} giorni in {len(vecchi)} campionat"
              f"{'o' if len(vecchi) == 1 else 'i'}:")
        for k, ultima, eta in vecchi:
            print(f"      {k}: ultima partita {ultima} ({eta} giorni fa)")
        print("    Il modello prevederebbe partite di un campionato di cui non ha")
        print("    visto le ultime giornate. Nessuna previsione e' stata scritta.")
        print("    Rilanciare l'ingest; se il buco e' sulla fonte, il turno si salta.")
        print("    Per registrare comunque, deliberatamente: --consenti-dati-vecchi")
        return 2

    if a.registra and vecchi and a.consenti_dati_vecchi:
        print(f"\n[!] --consenti-dati-vecchi: registro nonostante {len(vecchi)} "
              "campionato/i con dati vecchi. Annotare il turno come degradato.")

    if fx.empty:
        print("\nnessun fixture in tabella: lanciare prima `src.ingest.betfair --carica`")
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
            # Due quote distinte, e non sono interscambiabili: `o` e' quella
            # che si punta e che finisce nel ledger (il CLV la confronta con
            # una chiusura, anch'essa lorda); `o_netta` e' quella che rende, e
            # su un exchange la commissione si paga solo sul profitto. Usare
            # la lorda nell'edge lo sovrastima di (o-1)*commissione.
            o = float(o)
            o_netta = quota_netta(o)
            p = float(mk[pk])
            edge = p * o_netta - 1
            f = min(kelly(p, o_netta) * KELLY_FRAC, CAP)
            stake = arrotonda_stake(f * a.bankroll)
            ok = (EDGE_MIN <= edge <= EDGE_MAX) and stake >= MINBET
            righe.append({"Div": m.Div, "MatchDate": m.MatchDate, "Time": m.Time,
                          "HomeTeam": m.HomeTeam, "AwayTeam": m.AwayTeam,
                          "sel": sel, "p": p, "quota": o, "edge": edge,
                          "sospetta": edge > EDGE_MAX,
                          "stake": stake if ok else 0.0})

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
        sel["stake"] = (sel.stake * lim / tot).map(arrotonda_stake)
        sel = sel[sel.stake >= MINBET]
        print(f"esposizione riscalata da {tot:.0f} a {sel.stake.sum():.0f} EUR "
              f"(tetto {MAX_EXPOSURE:.0%})")

    print(f"\nrighe mercato valutate: {len(out)} | scartate {len(saltate)} partite")
    print(f"\n=== GIOCATE SELEZIONATE (edge {EDGE_MIN:.0%}-{EDGE_MAX:.0%}, "
          f"Kelly 1/4 su {a.bankroll:.0f} EUR, commissione {COMMISSIONE:.1%}) ===")
    if sel.empty:
        print("nessuna")
    else:
        for _, r in sel.sort_values(["MatchDate", "Time"]).iterrows():
            print(f"{r.MatchDate.date()} {r.Div:4} "
                  f"{r.HomeTeam[:16] + ' - ' + r.AwayTeam[:16]:36} {r.sel:10} "
                  f"p {r.p:.3f}  q {r.quota:5.2f}  edge {r.edge:+6.1%}  {r.stake:6.2f}")
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
             "prob": round(r.p, 6), "quota_offerta": r.quota,
             "edge": round(r.edge, 6), "note": f"turno {turno} · {SELEZIONE}"}
            for _, r in sel.iterrows()]
    creati = db.insert("previsioni", prev, ritorna=True)
    idx = {(p["data_partita"], p["casa"], p["trasferta"], p["mercato"], p["selezione"]): p["id"]
           for p in creati}
    giocate = [{"previsione_id": idx.get((str(r.MatchDate.date()), r.HomeTeam, r.AwayTeam,
                                          SEL_DB[r.sel][0], SEL_DB[r.sel][1])),
                "piazzata_il": creata, "turno": turno, "lega": r.Div,
                "data_partita": str(r.MatchDate.date()), "casa": r.HomeTeam,
                "trasferta": r.AwayTeam, "mercato": SEL_DB[r.sel][0],
                "selezione": SEL_DB[r.sel][1], "quota": r.quota,
                "stake": r.stake, "bankroll_al_momento": a.bankroll,
                "prob_modello": round(r.p, 6), "edge": round(r.edge, 6), "esito": "aperta"}
               for _, r in sel.iterrows()]
    n = db.insert("giocate", giocate)
    print(f"\nregistrate {len(creati)} previsioni e {n} giocate (turno {turno})")
    db.log("predict", "ok", n, f"turno {turno}, bankroll {a.bankroll}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
