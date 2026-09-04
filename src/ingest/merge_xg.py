"""Unisce quote di chiusura (football-data) e xG (Understat).
I due archivi usano nomi di squadra diversi: la mappatura viene DEDOTTA dai dati,
appaiando le partite su lega+data+risultato e prendendo la corrispondenza piu' frequente.
"""
import pandas as pd, numpy as np, os
from collections import Counter, defaultdict
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LG = {'EPL':'E0', 'La liga':'SP1', 'Serie A':'I1', 'Bundesliga':'D1', 'Ligue 1':'F1'}

o = pd.read_csv(f'{ROOT}/data/raw/current/pengwin_odds_xgera.csv', low_memory=False)
o['Date'] = pd.to_datetime(o['Date'], dayfirst=True, errors='coerce')
o = o.dropna(subset=['Date','FTHG','FTAG'])
o['d'] = o.Date.dt.normalize()

x = pd.read_csv(f'{ROOT}/data/raw/current/pengwin_xg.csv')
x['datetime'] = pd.to_datetime(x['datetime'], errors='coerce')
x = x.dropna(subset=['datetime'])
x['Div'] = x.league.map(LG)
x['d'] = x.datetime.dt.normalize()

# --- deduzione della mappatura dei nomi ---
pairs = defaultdict(Counter)
for off in (0, -1, 1):
    xx = x.copy(); xx['d'] = xx['d'] + pd.Timedelta(days=off)
    m = xx.merge(o, on=['Div','d'], suffixes=('_x','_o'))
    m = m[(m.gh == m.FTHG) & (m.ga == m.FTAG)]
    for h_x, h_o, a_x, a_o in zip(m.home, m.HomeTeam, m.away, m.AwayTeam):
        pairs[h_x][h_o] += 1; pairs[a_x][a_o] += 1
mapping = {k: c.most_common(1)[0][0] for k, c in pairs.items() if c}
amb = {k: c.most_common(2) for k, c in pairs.items()
       if len(c) > 1 and c.most_common(2)[1][1] > 0.25*c.most_common(1)[0][1]}
print(f"squadre mappate: {len(mapping)} | ambigue: {len(amb)}")
if amb:
    for k, v in list(amb.items())[:8]: print("   AMBIGUA", k, v)

x['H'] = x.home.map(mapping); x['A'] = x.away.map(mapping)
nomap = x[x.H.isna() | x.A.isna()]
print(f"partite xG senza mappatura: {len(nomap)}")
if len(nomap): print("   nomi non mappati:", sorted(set(nomap.home.dropna()) | set(nomap.away.dropna()))[:10])

# --- unione finale su lega + data (+-1g) + squadre ---
best = None
for off in (0, -1, 1):
    xx = x.copy(); xx['d'] = xx['d'] + pd.Timedelta(days=off)
    m = xx.merge(o, left_on=['Div','d','H','A'], right_on=['Div','d','HomeTeam','AwayTeam'], how='inner')
    best = m if best is None else pd.concat([best, m], ignore_index=True)
best = best.drop_duplicates(subset=['Div','H','A','Season'])
print(f"\nUNITE {len(best):,} partite su {len(x):,} xG e {len(o):,} con quote  ({100*len(best)/len(x):.1f}%)")
best = best.rename(columns={'Date':'MatchDate'}).sort_values('MatchDate')
print(best.groupby('Div').agg(n=('gh','size'), dal=('MatchDate','min'), al=('MatchDate','max')).to_string())
best.to_csv(f'{ROOT}/data/processed/xg_odds.csv', index=False)
