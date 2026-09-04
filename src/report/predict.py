"""Previsioni per le partite in arrivo + selezione value bet + staking Kelly frazionario.
Gira in locale in pochi secondi: un fit per campionato sulle ultime 3 stagioni."""
import sys, os, json, numpy as np, pandas as pd
from datetime import datetime
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, f'{ROOT}/src')
from model.dixon_coles_xg import fit, score_matrix, markets

W, XI, LOOKBACK = 0.35, 0.0018, 1095      # parametri tarati in Fase 2
BANKROLL0, KELLY_FRAC, EDGE_MIN, CAP = 1000.0, 0.25, 0.02, 0.01
EDGE_MAX = 0.10   # banda superiore: sopra, il backtest mostra che l'edge apparente e' errore di stima
MAX_EXPOSURE = 0.20      # esposizione totale massima per turno
MAX_BETS = 25
MIN_MATCHES = 8          # partite minime per squadra nella finestra: sotto, la stima non e' affidabile
EDGE_SANITY = EDGE_MAX   # oltre la banda: errore del modello, non valore
MINBET = 2.0

def build_models(d, asof):
    out = {}
    for div, dl in d.groupby('Div'):
        tr = dl[(dl.MatchDate < asof) & (dl.MatchDate >= asof - pd.Timedelta(days=LOOKBACK))]
        if len(tr) < 200: continue
        teams = sorted(set(tr.HomeTeam) | set(tr.AwayTeam)); ix = {t:i for i,t in enumerate(teams)}
        cnt = pd.concat([tr.HomeTeam, tr.AwayTeam]).value_counts().to_dict()
        wt = np.exp(-XI * (asof - tr.MatchDate).dt.days.values)
        th = W*tr.FTHG.values + (1-W)*tr.xgh.values
        ta = W*tr.FTAG.values + (1-W)*tr.xga.values
        par = fit(tr.HomeTeam.map(ix).values, tr.AwayTeam.map(ix).values,
                  th, ta, tr.FTHG.values, tr.FTAG.values, wt, len(teams))
        out[div] = (par, ix, len(tr), cnt)
    return out

def kelly(p, o):
    b = o - 1.0
    return max(0.0, (p*o - 1.0) / b) if b > 0 else 0.0

def main(bankroll=BANKROLL0):
    d = pd.read_csv(f'{ROOT}/data/processed/xg_final.csv', parse_dates=['MatchDate'], low_memory=False)
    fx = pd.read_csv(f'{ROOT}/data/raw/current/pengwin_fixtures.csv')
    fx['MatchDate'] = pd.to_datetime(fx['Date'], dayfirst=True, errors='coerce')
    asof = pd.Timestamp(datetime.now().date())
    # Filtro sul CALCIO D'INIZIO, non sulla data: se la procedura gira a meta' giornata
    # le partite gia' iniziate devono restare fuori. Margine di 15 minuti per sicurezza.
    kick = pd.to_datetime(fx['Date'].astype(str) + ' ' + fx['Time'].astype(str),
                          dayfirst=True, errors='coerce')
    limite = pd.Timestamp(datetime.now()) + pd.Timedelta(minutes=15)
    scartate_tempo = int(((kick.notna()) & (kick <= limite)).sum())
    fx = fx[(kick > limite) | (kick.isna() & (fx.MatchDate > asof))]
    if scartate_tempo:
        print(f"escluse {scartate_tempo} partite gia' iniziate o troppo imminenti")
    models = build_models(d, asof)
    print(f"modelli stimati: " + ", ".join(f"{k}({v[2]} partite)" for k,v in models.items()))

    rows, skipped = [], []
    for _, m in fx.iterrows():
        if m.Div not in models: skipped.append((m.Div, m.HomeTeam, m.AwayTeam, 'lega')); continue
        par, ix, _, cnt = models[m.Div]
        thin = [t for t in (m.HomeTeam, m.AwayTeam) if cnt.get(t, 0) < MIN_MATCHES]
        if thin:
            skipped.append((m.Div, m.HomeTeam, m.AwayTeam,
                            'storico insufficiente: ' + ', '.join(f"{t} ({cnt.get(t,0)} partite)" for t in thin)))
            continue
        mk = markets(score_matrix(par, ix[m.HomeTeam], ix[m.AwayTeam]))
        base = {'Div':m.Div, 'MatchDate':m.MatchDate, 'Time':m.Time,
                'HomeTeam':m.HomeTeam, 'AwayTeam':m.AwayTeam,
                'p_H':mk['H'], 'p_D':mk['D'], 'p_A':mk['A'], 'p_O25':mk['O2.5'], 'p_U25':mk['U2.5']}
        for sel, pcol, ocol, oavg in [('1','p_H','MaxH','AvgH'), ('X','p_D','MaxD','AvgD'), ('2','p_A','MaxA','AvgA'),
                                      ('Over 2.5','p_O25','Max>2.5','Avg>2.5'), ('Under 2.5','p_U25','Max<2.5','Avg<2.5')]:
            o = m.get(ocol); oa = m.get(oavg)
            if pd.isna(o) or o <= 1.01: continue
            p = base[pcol]; edge = p*o - 1
            r = dict(base); r.update({'sel':sel, 'p':p, 'odds_max':o, 'odds_avg':oa, 'edge':edge})
            f = kelly(p, o) * KELLY_FRAC
            stake = min(f, CAP) * bankroll
            r['sospetta'] = edge > EDGE_SANITY
            ok = (EDGE_MIN <= edge <= EDGE_MAX) and stake >= MINBET
            r['stake'] = round(stake, 2) if ok else 0.0
            rows.append(r)
    out = pd.DataFrame(rows)
    if len(out) == 0:
        # nessuna partita candidata (listino non ancora pubblicato, o tutte gia' iniziate):
        # uscita pulita. Nessuna regola di selezione e' coinvolta.
        print("\nnessuna partita candidata: niente da valutare, nessuna giocata")
        for s in skipped[:8]: print("   ", s)
        return out, out
    susp = out[out.get('sospetta', False) == True]
    if len(susp):
        print(f"\nSCARTATE {len(susp)} righe con edge > {EDGE_SANITY:.0%} (implausibili: errore di stima, non valore):")
        for _, r in susp.sort_values('edge', ascending=False).head(6).iterrows():
            print(f"   {r.Div} {r.HomeTeam}-{r.AwayTeam} {r.sel}: modello {r.p:.3f} vs quota {r.odds_max} -> edge {r.edge:+.0%}")
    sel = out[out.stake > 0].sort_values(['MatchDate','edge']).head(MAX_BETS).copy()
    # vincolo di portafoglio: riscala se l'esposizione totale supera il tetto
    tot = sel.stake.sum(); lim = MAX_EXPOSURE * bankroll
    if tot > lim:
        sel['stake'] = (sel.stake * lim / tot).round(2)
        sel = sel[sel.stake >= MINBET]
        print(f"esposizione riscalata da {tot:.0f} a {sel.stake.sum():.0f} EUR (tetto {MAX_EXPOSURE:.0%})")
    os.makedirs(f'{ROOT}/outputs/predictions', exist_ok=True)
    stamp = asof.strftime('%Y-%m-%d')
    out.to_csv(f'{ROOT}/outputs/predictions/all_{stamp}.csv', index=False)
    sel.to_csv(f'{ROOT}/outputs/predictions/bets_{stamp}.csv', index=False)
    print(f"\npartite valutate: {out.MatchDate.notna().sum()//5 if len(out) else 0} | righe mercato: {len(out)}")
    print(f"scartate: {len(skipped)}")
    for s in skipped[:8]: print("   ", s)
    print(f"\n=== GIOCATE SELEZIONATE (edge >= {EDGE_MIN:.0%}, Kelly 1/4 su {bankroll:.0f} EUR) ===")
    if len(sel) == 0:
        print("nessuna")
    else:
        print(f"{'data':11s} {'lega':4s} {'partita':38s} {'sel':10s} {'p':>6s} {'quota':>6s} {'edge':>7s} {'punta':>7s}")
        for _, r in sel.sort_values(['MatchDate','Time']).iterrows():
            print(f"{r.MatchDate.strftime('%d/%m/%Y')} {r.Div:4s} {r.HomeTeam[:17]+' - '+r.AwayTeam[:17]:38s} "
                  f"{r.sel:10s} {r.p:6.3f} {r.odds_max:6.2f} {r.edge:+7.1%} {r.stake:7.2f}")
        print(f"\ntotale esposto: {sel.stake.sum():.2f} EUR su {len(sel)} giocate ({100*sel.stake.sum()/bankroll:.1f}% del bankroll)")
    return out, sel

if __name__ == '__main__':
    main()
