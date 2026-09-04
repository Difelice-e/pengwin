"""Costruisce il dataset pulito delle Top 5 con probabilita' di mercato de-marginate."""
import pandas as pd, numpy as np, os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOP5 = {'E0':'Premier League','D1':'Bundesliga','I1':'Serie A','SP1':'La Liga','F1':'Ligue 1'}

def shin_probs(odds):
    """De-margina un insieme di quote col metodo di Shin.
    Risolve per z (quota di scommettitori informati) tale che sum(p)=1, dove
        p_i = (sqrt(z^2 + 4(1-z)*pi_i^2/B) - z) / (2(1-z))
    con pi_i = 1/quota_i e B = sum(pi). A z=0 la somma vale sqrt(B) > 1 e
    decresce monotonicamente in z: la bisezione va verso z crescenti se s > 1."""
    o = np.asarray(odds, dtype=float)
    pi = 1.0 / o
    B = pi.sum(axis=1, keepdims=True)
    c = pi**2 / B
    lo = np.zeros((len(o), 1)); hi = np.full((len(o), 1), 0.99)
    for _ in range(80):
        z = (lo + hi) / 2
        p = (np.sqrt(z**2 + 4*(1-z)*c) - z) / (2*(1-z))
        s = p.sum(axis=1, keepdims=True)
        lo = np.where(s > 1, z, lo)          # somma troppo alta -> serve z maggiore
        hi = np.where(s > 1, hi, z)
    z = (lo + hi) / 2
    p = (np.sqrt(z**2 + 4*(1-z)*c) - z) / (2*(1-z))
    p = p / p.sum(axis=1, keepdims=True)
    return p, B.ravel() - 1.0, z.ravel()

def prop_probs(odds):
    """De-marginazione proporzionale (normalizzazione semplice), per confronto."""
    pi = 1.0 / np.asarray(odds, dtype=float)
    return pi / pi.sum(axis=1, keepdims=True)

def season_of(d):
    return np.where(d.dt.month >= 7, d.dt.year, d.dt.year - 1)

def main():
    df = pd.read_csv(f"{ROOT}/data/raw/Matches.csv", low_memory=False)
    df['MatchDate'] = pd.to_datetime(df['MatchDate'], errors='coerce')
    d = df[df.Division.isin(TOP5)].dropna(subset=['MatchDate','FTHome','FTAway']).copy()
    d['Season'] = season_of(d['MatchDate'])
    d = d.sort_values('MatchDate').reset_index(drop=True)

    # --- mercato 1X2 ---
    m = d[['OddHome','OddDraw','OddAway']].notna().all(axis=1) & (d[['OddHome','OddDraw','OddAway']] > 1.0).all(axis=1)
    p, marg, z = shin_probs(d.loc[m, ['OddHome','OddDraw','OddAway']].values)
    for i, c in enumerate(['mkt_H','mkt_D','mkt_A']):
        d.loc[m, c] = p[:, i]
    d.loc[m, 'margin_1x2'] = marg
    d.loc[m, 'shin_z'] = z
    pp = prop_probs(d.loc[m, ['OddHome','OddDraw','OddAway']].values)
    for i, c in enumerate(['prop_H','prop_D','prop_A']):
        d.loc[m, c] = pp[:, i]

    # --- mercato Over/Under 2.5 ---
    mo = d[['Over25','Under25']].notna().all(axis=1) & (d[['Over25','Under25']] > 1.0).all(axis=1)
    po, margo, _ = shin_probs(d.loc[mo, ['Over25','Under25']].values)
    d.loc[mo, 'mkt_O25'] = po[:, 0]; d.loc[mo, 'mkt_U25'] = po[:, 1]
    d.loc[mo, 'margin_ou'] = margo

    d['TotGoals'] = d.FTHome + d.FTAway
    d['res'] = d.FTResult
    out = f"{ROOT}/data/processed/top5.csv"
    d.to_csv(out, index=False)

    print(f"salvate {len(d):,} partite -> data/processed/top5.csv")
    print(f"con mercato 1X2: {m.sum():,} | con mercato O/U 2.5: {mo.sum():,}")
    print(f"\nmargine medio bookmaker 1X2 per decennio:")
    d['dec'] = (d.Season // 5) * 5
    print(d.groupby('dec')[['margin_1x2','margin_ou']].mean().mul(100).round(2).to_string())
    print(f"\nquota di insider (z) media: {d.shin_z.mean():.4f}")
    # sanity check: le prob de-marginate devono essere calibrate sul risultato reale
    dd = d[m]
    print("\n=== CALIBRAZIONE: probabilita' media stimata vs frequenza reale ===")
    print(f"{'esito':6s} {'Shin':>10s} {'Proporz.':>10s} {'reale':>10s}")
    for c, lab in [('H','H'), ('D','D'), ('A','A')]:
        print(f"{lab:6s} {dd['mkt_'+c].mean():10.4f} {dd['prop_'+c].mean():10.4f} {(dd.res==lab).mean():10.4f}")
    ddo = d[mo]
    print(f"{'Over':6s} {ddo.mkt_O25.mean():10.4f} {'-':>10s} {(ddo.TotGoals>2.5).mean():10.4f}")

    print("\n=== LOG-LOSS sul risultato reale (piu' basso = meglio) ===")
    y = pd.get_dummies(dd.res)[['H','D','A']].values.astype(float)
    for name, cols in [('Shin', ['mkt_H','mkt_D','mkt_A']), ('Proporzionale', ['prop_H','prop_D','prop_A'])]:
        pr = np.clip(dd[cols].values, 1e-9, 1)
        print(f"  {name:16s} {-np.mean(np.sum(y*np.log(pr), axis=1)):.5f}")

if __name__ == '__main__':
    main()
