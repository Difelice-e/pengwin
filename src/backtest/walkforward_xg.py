"""Walk-forward con obiettivo misto gol/xG, benchmark = quote di CHIUSURA Pinnacle."""
import sys, os, time, numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from model.dixon_coles_xg import fit, score_matrix, markets
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def run_league(d, div, w, xi, lookback, s0, s1, refit_days=14):
    dl = d[d.Div == div].sort_values('MatchDate').reset_index(drop=True)
    test = dl[dl.Season.between(s0, s1)]
    if test.empty: return pd.DataFrame()
    rows, prev, par, teams, ix, last = [], None, None, None, None, None
    for _, m in test.iterrows():
        if last is None or (m.MatchDate - last).days >= refit_days:
            cut = m.MatchDate
            tr = dl[(dl.MatchDate < cut) & (dl.MatchDate >= cut - pd.Timedelta(days=lookback))]
            if len(tr) < 200: continue
            teams = sorted(set(tr.HomeTeam) | set(tr.AwayTeam)); ix = {t:i for i,t in enumerate(teams)}
            wt = np.exp(-xi * (cut - tr.MatchDate).dt.days.values)
            x0 = None
            if prev is not None:
                a = np.array([prev['a'].get(t,0.0) for t in teams])
                b = np.array([prev['b'].get(t,0.0) for t in teams])
                x0 = np.concatenate([a, b, [prev['g']], [prev['r']]])
            th = w*tr.FTHG.values + (1-w)*tr.xgh.values
            ta = w*tr.FTAG.values + (1-w)*tr.xga.values
            par = fit(tr.HomeTeam.map(ix).values, tr.AwayTeam.map(ix).values,
                      th, ta, tr.FTHG.values, tr.FTAG.values, wt, len(teams), x0=x0)
            prev = {'a':dict(zip(teams,par['attack'])), 'b':dict(zip(teams,par['defense'])),
                    'g':par['home_adv'], 'r':par['rho']}
            last = cut
        if par is None or m.HomeTeam not in ix or m.AwayTeam not in ix: continue
        mk = markets(score_matrix(par, ix[m.HomeTeam], ix[m.AwayTeam]))
        rows.append({'Div':div, 'MatchDate':m.MatchDate, 'Season':m.Season,
                     'HomeTeam':m.HomeTeam, 'AwayTeam':m.AwayTeam, 'res':m.res, 'TotGoals':m.TotGoals,
                     'p_H':mk['H'], 'p_D':mk['D'], 'p_A':mk['A'], 'p_O25':mk['O2.5'], 'p_U25':mk['U2.5'],
                     'ps_H':m.ps_H, 'ps_D':m.ps_D, 'ps_A':m.ps_A,
                     'avg_H':m.avg_H, 'avg_D':m.avg_D, 'avg_A':m.avg_A,
                     'avg_O25':m.avg_O25, 'avg_U25':m.avg_U25,
                     'PSCH':m.PSCH, 'PSCD':m.PSCD, 'PSCA':m.PSCA,
                     'MaxCH':m.MaxCH, 'MaxCD':m.MaxCD, 'MaxCA':m.MaxCA})
    return pd.DataFrame(rows)

def ll3(df, cols):
    y = pd.get_dummies(df.res)[['H','D','A']].values.astype(float)
    p = np.clip(df[cols].values.astype(float), 1e-9, 1); p = p/p.sum(axis=1, keepdims=True)
    return -np.mean(np.sum(y*np.log(p), axis=1))

def evaluate(df, bench='ps'):
    d = df.dropna(subset=[f'{bench}_H', f'{bench}_D', f'{bench}_A'])
    if len(d) < 50: return None
    a, b = ll3(d, ['p_H','p_D','p_A']), ll3(d, [f'{bench}_H', f'{bench}_D', f'{bench}_A'])
    return {'n': len(d), 'll_model': a, 'll_market': b, 'delta': a-b}
