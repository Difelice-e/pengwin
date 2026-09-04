"""Come build_dataset.py ma su tutti i campionati con quote utilizzabili."""
import sys, os, pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_dataset import shin_probs, season_of
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEEP = ['Division','MatchDate','Season','HomeTeam','AwayTeam','FTHome','FTAway','FTResult',
        'HomeCorners','AwayCorners','HomeYellow','AwayYellow','HomeElo','AwayElo',
        'OddHome','OddDraw','OddAway','MaxHome','MaxDraw','MaxAway',
        'Over25','Under25','MaxOver25','MaxUnder25',
        'mkt_H','mkt_D','mkt_A','mkt_O25','mkt_U25','margin_1x2','TotGoals','res']
df = pd.read_csv(f'{ROOT}/data/raw/Matches.csv', low_memory=False)
df['MatchDate'] = pd.to_datetime(df['MatchDate'], errors='coerce')
df = df.dropna(subset=['MatchDate','FTHome','FTAway']).copy()
cnt = df.groupby('Division').agg(n=('FTHome','size'), q=('OddHome', lambda s: s.notna().mean()))
divs = cnt[(cnt.n > 1500) & (cnt.q > 0.80)].index.tolist()
d = df[df.Division.isin(divs)].copy()
d['Season'] = season_of(d['MatchDate'])
d = d.sort_values('MatchDate').reset_index(drop=True)
m = d[['OddHome','OddDraw','OddAway']].notna().all(axis=1) & (d[['OddHome','OddDraw','OddAway']] > 1.0).all(axis=1)
p, marg, _ = shin_probs(d.loc[m, ['OddHome','OddDraw','OddAway']].values)
for i, c in enumerate(['mkt_H','mkt_D','mkt_A']):
    d.loc[m, c] = p[:, i]
d.loc[m, 'margin_1x2'] = marg
mo = d[['Over25','Under25']].notna().all(axis=1) & (d[['Over25','Under25']] > 1.0).all(axis=1)
po, _, _ = shin_probs(d.loc[mo, ['Over25','Under25']].values)
d.loc[mo, 'mkt_O25'] = po[:, 0]; d.loc[mo, 'mkt_U25'] = po[:, 1]
d['TotGoals'] = d.FTHome + d.FTAway; d['res'] = d.FTResult
d[[c for c in KEEP if c in d.columns]].to_csv(f'{ROOT}/data/processed/all_leagues.csv', index=False)
print(f"{len(divs)} campionati, {len(d):,} partite -> data/processed/all_leagues.csv")
print("campionati:", ", ".join(sorted(divs)))
