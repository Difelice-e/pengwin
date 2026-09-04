"""Diagnostica: perche' il modello perde, e cosa succederebbe scommettendo davvero."""
import numpy as np, pandas as pd
from scipy.optimize import minimize

d = pd.read_csv('outputs/predictions_walkforward.csv', parse_dates=['MatchDate']).sort_values('MatchDate').reset_index(drop=True)
d = d.dropna(subset=['mkt_H','mkt_D','mkt_A','MaxHome','MaxDraw','MaxAway']).reset_index(drop=True)
y = pd.get_dummies(d.res)[['H','D','A']].values.astype(float)
pm = d[['p_H','p_D','p_A']].values; pk = d[['mkt_H','mkt_D','mkt_A']].values
cut = len(d)//2

def nll(par, i0, i1):
    a, b = par
    s = a*np.log(np.clip(pk[i0:i1],1e-12,1)) + b*np.log(np.clip(pm[i0:i1],1e-12,1))
    s = s - s.max(axis=1, keepdims=True); p = np.exp(s); p /= p.sum(axis=1, keepdims=True)
    return -np.mean(np.log(np.clip((p*y[i0:i1]).sum(axis=1),1e-12,1)))

r = minimize(nll, [1.0, 0.0], args=(0, cut), method='Nelder-Mead')
a, b = r.x
print("=== REGRESSIONE LIBERA sui log delle probabilita' (meta' di taratura) ===")
print(f"  coefficiente mercato a = {a:.4f}   coefficiente modello b = {b:.4f}")
print(f"  (b vicino a 0 = il modello non porta informazione indipendente)")
print(f"  log-loss fuori campione: combinazione {nll([a,b],cut,len(d)):.5f}  |  solo mercato {nll([1,0],cut,len(d)):.5f}")

print("\n=== CALIBRAZIONE DEL MODELLO (fuori campione) ===")
pmt, yt = pm[cut:], y[cut:]
bins = np.linspace(0,1,11)
lab = ['Casa','Pari','Trasf']
for k in range(3):
    print(f"  {lab[k]}: ", end="")
    idx = np.digitize(pmt[:,k], bins) - 1
    out = []
    for bnum in range(2, 9):
        m = idx == bnum
        if m.sum() > 100:
            out.append(f"[{bins[bnum]:.1f}-{bins[bnum+1]:.1f}] prev {pmt[m,k].mean():.2f} vs reale {yt[m,k].mean():.2f}")
    print("  ".join(out))

print("\n=== SIMULAZIONE VALUE BETTING (meta' fuori campione, quote massime di mercato) ===")
dt = d.iloc[cut:].reset_index(drop=True)
odds = dt[['MaxHome','MaxDraw','MaxAway']].values
p = dt[['p_H','p_D','p_A']].values
res = pd.get_dummies(dt.res)[['H','D','A']].values.astype(float)
mkt = dt[['mkt_H','mkt_D','mkt_A']].values
edge = p*odds - 1
print(f"{'soglia':>8s} {'giocate':>8s} {'ROI':>9s} {'CLV medio':>11s}")
for thr in [0.02, 0.05, 0.10, 0.20]:
    sel = edge > thr
    n = sel.sum()
    if n < 30: continue
    ret = np.where(res[sel] == 1, odds[sel]-1, -1.0)
    # CLV: confronto la quota presa con la quota equa del mercato (1/prob de-marginata)
    clv = odds[sel]*mkt[sel] - 1
    print(f"{thr:8.0%} {n:8d} {ret.mean():8.2%} {clv.mean():10.2%}")
print("\n(ROI atteso di riferimento: scommettendo a caso sulle quote massime ~ -1/-2%)")
