"""Backtest walk-forward: previsioni settimana per settimana usando SOLO il passato.

Per ogni settimana di calendario il modello viene ristimato sulle partite precedenti
(finestra mobile con decadimento temporale) e usato per prevedere le partite di
quella settimana. Nessuna informazione successiva all'evento entra mai nella stima.
"""
import sys, os, time, numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from model.dixon_coles import fit, score_matrix, markets

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def run_league(d, division, xi, lookback_days, start_season, end_season, verbose=False):
    dl = d[d.Division == division].sort_values('MatchDate').reset_index(drop=True)
    test = dl[dl.Season.between(start_season, end_season)]
    if test.empty:
        return pd.DataFrame()
    weeks = test.MatchDate.dt.to_period('W')
    rows, prev = [], None
    for wk, blk in test.groupby(weeks):
        cutoff = blk.MatchDate.min()
        tr = dl[(dl.MatchDate < cutoff) & (dl.MatchDate >= cutoff - pd.Timedelta(days=lookback_days))]
        if len(tr) < 200:
            continue
        teams = sorted(set(tr.HomeTeam) | set(tr.AwayTeam)); ix = {t: i for i, t in enumerate(teams)}
        w = np.exp(-xi * (cutoff - tr.MatchDate).dt.days.values)
        x0 = None
        if prev is not None:                       # warm start: riusa le stime precedenti
            a = np.array([prev['a'].get(t, 0.0) for t in teams])
            b = np.array([prev['b'].get(t, 0.0) for t in teams])
            x0 = np.concatenate([a, b, [prev['g']], [prev['r']]])
        par = fit(tr.HomeTeam.map(ix).values, tr.AwayTeam.map(ix).values,
                  tr.FTHome.values, tr.FTAway.values, w, len(teams), x0=x0)
        prev = {'a': dict(zip(teams, par['attack'])), 'b': dict(zip(teams, par['defense'])),
                'g': par['home_adv'], 'r': par['rho']}
        for _, m in blk.iterrows():
            if m.HomeTeam not in ix or m.AwayTeam not in ix:
                continue                            # neopromossa senza storico nella finestra
            mk = markets(score_matrix(par, ix[m.HomeTeam], ix[m.AwayTeam]))
            rows.append({'Division': division, 'MatchDate': m.MatchDate, 'Season': m.Season,
                         'HomeTeam': m.HomeTeam, 'AwayTeam': m.AwayTeam,
                         'res': m.res, 'TotGoals': m.TotGoals,
                         'p_H': mk['H'], 'p_D': mk['D'], 'p_A': mk['A'],
                         'p_O25': mk['O2.5'], 'p_U25': mk['U2.5'], 'p_BTTS': mk['BTTS'],
                         'mkt_H': m.mkt_H, 'mkt_D': m.mkt_D, 'mkt_A': m.mkt_A,
                         'mkt_O25': m.mkt_O25, 'mkt_U25': m.mkt_U25,
                         'OddHome': m.OddHome, 'OddDraw': m.OddDraw, 'OddAway': m.OddAway,
                         'MaxHome': m.MaxHome, 'MaxDraw': m.MaxDraw, 'MaxAway': m.MaxAway,
                         'Over25': m.Over25, 'Under25': m.Under25,
                         'MaxOver25': m.MaxOver25, 'MaxUnder25': m.MaxUnder25})
        if verbose:
            print(f"  {division} {wk} n={len(blk)}", flush=True)
    return pd.DataFrame(rows)

def logloss_1x2(df, cols):
    y = pd.get_dummies(df.res)[['H','D','A']].values.astype(float)
    p = np.clip(df[cols].values.astype(float), 1e-9, 1)
    p = p / p.sum(axis=1, keepdims=True)
    return -np.mean(np.sum(y * np.log(p), axis=1))

def logloss_ou(df, col):
    y = (df.TotGoals > 2.5).values.astype(float)
    p = np.clip(df[col].values.astype(float), 1e-9, 1 - 1e-9)
    return -np.mean(y*np.log(p) + (1-y)*np.log(1-p))

def evaluate(df, label=""):
    d = df.dropna(subset=['mkt_H','mkt_D','mkt_A'])
    r = {'n': len(d),
         'll_model': logloss_1x2(d, ['p_H','p_D','p_A']),
         'll_market': logloss_1x2(d, ['mkt_H','mkt_D','mkt_A'])}
    r['delta'] = r['ll_model'] - r['ll_market']
    do = df.dropna(subset=['mkt_O25'])
    if len(do):
        r['n_ou'] = len(do)
        r['ll_model_ou'] = logloss_ou(do, 'p_O25')
        r['ll_market_ou'] = logloss_ou(do, 'mkt_O25')
        r['delta_ou'] = r['ll_model_ou'] - r['ll_market_ou']
    if label:
        r['label'] = label
    return r

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--xi', type=float, default=0.0025)
    ap.add_argument('--lookback', type=int, default=1095)
    ap.add_argument('--start', type=int, default=2010)
    ap.add_argument('--end', type=int, default=2024)
    ap.add_argument('--divisions', default='E0,D1,I1,SP1,F1')
    ap.add_argument('--out', default='')
    a = ap.parse_args()
    d = pd.read_csv(f'{ROOT}/data/processed/top5.csv', parse_dates=['MatchDate'], low_memory=False)
    allp = []
    for div in a.divisions.split(','):
        t0 = time.time()
        p = run_league(d, div, a.xi, a.lookback, a.start, a.end)
        allp.append(p)
        print(f"{div}: {len(p)} previsioni in {time.time()-t0:.0f}s", flush=True)
    res = pd.concat(allp, ignore_index=True)
    if a.out:
        res.to_csv(a.out, index=False)
    e = evaluate(res)
    print(f"\nxi={a.xi} lookback={a.lookback}g  n={e['n']}")
    print(f"  1X2  modello {e['ll_model']:.5f}  mercato {e['ll_market']:.5f}  delta {e['delta']:+.5f}")
    if 'delta_ou' in e:
        print(f"  O/U  modello {e['ll_model_ou']:.5f}  mercato {e['ll_market_ou']:.5f}  delta {e['delta_ou']:+.5f}")
