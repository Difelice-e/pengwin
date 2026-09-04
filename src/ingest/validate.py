import pandas as pd, numpy as np
P="/".join(__file__.split("/")[:-3])
df = pd.read_csv(f"{P}/data/raw/Matches.csv", low_memory=False)
print("righe totali:", f"{len(df):,}", "| colonne:", len(df.columns))
print("\ncolonne:", list(df.columns))
TOP5={'E0':'Premier League','D1':'Bundesliga','I1':'Serie A','SP1':'La Liga','F1':'Ligue 1'}
df['MatchDate']=pd.to_datetime(df['MatchDate'],errors='coerce')
t=df[df.Division.isin(TOP5)].copy()
print(f"\n=== TOP 5 === {len(t):,} partite | {t.MatchDate.min().date()} -> {t.MatchDate.max().date()}")
for d,n in TOP5.items():
    s=t[t.Division==d]
    print(f"{n:16s} {len(s):6,d} partite  {s.MatchDate.min().date()} -> {s.MatchDate.max().date()}")
print("\n=== COMPLETEZZA COLONNE CHIAVE (Top5) ===")
for c in ['FTHome','FTAway','OddHome','OddDraw','OddAway','MaxHome','Over25','Under25','MaxOver25','HomeElo','AwayElo','HomeShots','HomeTarget']:
    if c in t.columns:
        print(f"  {c:14s} {100*t[c].notna().mean():5.1f}% presente")
print("\n=== COPERTURA QUOTE PER STAGIONE (Serie A) ===")
i1=t[t.Division=='I1'].copy()
i1['stag']=np.where(i1.MatchDate.dt.month>=7, i1.MatchDate.dt.year, i1.MatchDate.dt.year-1)
g=i1.groupby('stag').agg(partite=('FTHome','size'), odd1x2=('OddHome',lambda s:100*s.notna().mean()), ou25=('Over25',lambda s:100*s.notna().mean()))
print(g.tail(20).to_string(float_format=lambda x:f"{x:.0f}"))
