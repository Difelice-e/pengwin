"""Dataset finale: xG + quote di CHIUSURA de-marginate (benchmark piu' severo)."""
import sys, os, pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_dataset import shin_probs
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
d = pd.read_csv(f'{ROOT}/data/processed/xg_odds.csv', low_memory=False)
d['MatchDate'] = pd.to_datetime(d['MatchDate'], errors='coerce')
d = d.dropna(subset=['MatchDate','FTHG','FTAG','xgh','xga']).sort_values('MatchDate').reset_index(drop=True)
d['Season'] = np.where(d.MatchDate.dt.month >= 7, d.MatchDate.dt.year, d.MatchDate.dt.year - 1)
d['HomeTeam'] = d['H']; d['AwayTeam'] = d['A']
d['res'] = d['FTR']; d['TotGoals'] = d.FTHG + d.FTAG

def demarg(cols, out):
    m = d[cols].notna().all(axis=1) & (d[cols] > 1.0).all(axis=1)
    if m.sum() == 0: return 0
    p, marg, _ = shin_probs(d.loc[m, cols].values)
    for i, c in enumerate(out): d.loc[m, c] = p[:, i]
    d.loc[m, out[0].split('_')[0] + '_margin'] = marg
    return m.sum()

n1 = demarg(['AvgCH','AvgCD','AvgCA'], ['avg_H','avg_D','avg_A'])       # media di mercato alla chiusura
n2 = demarg(['PSCH','PSCD','PSCA'],   ['ps_H','ps_D','ps_A'])           # Pinnacle chiusura
n3 = demarg(['BFECH','BFECD','BFECA'],['bfe_H','bfe_D','bfe_A'])        # Betfair Exchange chiusura
n4 = demarg(['AvgC>2.5','AvgC<2.5'],  ['avg_O25','avg_U25'])
print(f"de-marginate: media {n1}, Pinnacle {n2}, Betfair {n3}, O/U {n4}")
print(f"margine mediano  media {100*d.avg_margin.median():.2f}%  Pinnacle {100*d.ps_margin.median():.2f}%  Betfair {100*d.bfe_margin.median():.2f}%")
print("\nCALIBRAZIONE (prob media vs frequenza reale)")
for pre, lab in [('avg','media mercato'), ('ps','Pinnacle'), ('bfe','Betfair Ex')]:
    s = d.dropna(subset=[f'{pre}_H'])
    if len(s) < 100: continue
    print(f"  {lab:14s} n={len(s):6d}  H {s[pre+'_H'].mean():.4f}/{(s.res=='H').mean():.4f}"
          f"  D {s[pre+'_D'].mean():.4f}/{(s.res=='D').mean():.4f}  A {s[pre+'_A'].mean():.4f}/{(s.res=='A').mean():.4f}")
KEEP = ['Div','MatchDate','Season','HomeTeam','AwayTeam','FTHG','FTAG','res','TotGoals','xgh','xga',
        'HS','AS','HST','AST','HC','AC','avg_H','avg_D','avg_A','ps_H','ps_D','ps_A','bfe_H','bfe_D','bfe_A',
        'avg_O25','avg_U25','AvgCH','AvgCD','AvgCA','MaxCH','MaxCD','MaxCA','PSCH','PSCD','PSCA',
        'BFECH','BFECD','BFECA','AvgC>2.5','AvgC<2.5','MaxC>2.5','MaxC<2.5','avg_margin','ps_margin']
d[[c for c in KEEP if c in d.columns]].to_csv(f'{ROOT}/data/processed/xg_final.csv', index=False)
print(f"\nsalvate {len(d):,} partite -> data/processed/xg_final.csv")
