"""Il modello aggiunge informazione rispetto al mercato?

Test: combinazione log-lineare delle due distribuzioni
    p_blend  proporzionale a  p_modello^w * p_mercato^(1-w)
Il peso w viene tarato sul periodo di validazione e applicato al periodo di test.
Se w* = 0 il modello non aggiunge nulla. Se w* > 0 e il log-loss del blend scende
sotto quello del mercato in modo statisticamente significativo, c'e' informazione
incrementale — la condizione necessaria (non sufficiente) per avere un edge.
"""
import numpy as np, pandas as pd

def blend(pm, pk, w):
    """pm = prob modello, pk = prob mercato (array n x k)."""
    with np.errstate(divide='ignore'):
        lg = w*np.log(np.clip(pm, 1e-12, 1)) + (1-w)*np.log(np.clip(pk, 1e-12, 1))
    e = np.exp(lg - lg.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)

def ll_rows(p, y):
    """log-loss per singola riga (serve per il test di significativita' appaiato)."""
    return -np.log(np.clip((p*y).sum(axis=1), 1e-12, 1))

def analyse(df, mcols, kcols, ycol_fn, label, wgrid=np.linspace(0, 1, 51)):
    d = df.dropna(subset=list(mcols)+list(kcols)).copy()
    y = ycol_fn(d)
    pm = d[list(mcols)].values.astype(float); pm /= pm.sum(axis=1, keepdims=True)
    pk = d[list(kcols)].values.astype(float); pk /= pk.sum(axis=1, keepdims=True)
    n = len(d); cut = n // 2
    lls = [ll_rows(blend(pm[:cut], pk[:cut], w), y[:cut]).mean() for w in wgrid]
    w_star = wgrid[int(np.argmin(lls))]
    # applica il peso tarato sulla prima meta' alla seconda meta' (mai vista)
    lb = ll_rows(blend(pm[cut:], pk[cut:], w_star), y[cut:])
    lk = ll_rows(pk[cut:], y[cut:])
    diff = lk - lb                      # positivo = il blend batte il mercato
    se = diff.std(ddof=1) / np.sqrt(len(diff))
    t = diff.mean() / se if se > 0 else 0.0
    print(f"\n=== {label} ===")
    print(f"  n validazione {cut}  |  n test {len(diff)}")
    print(f"  peso ottimo sul modello  w* = {w_star:.2f}")
    print(f"  log-loss mercato   {lk.mean():.5f}")
    print(f"  log-loss blend     {lb.mean():.5f}")
    print(f"  miglioramento      {diff.mean():+.5f}  (t = {t:+.2f})")
    print(f"  {'INFORMAZIONE INCREMENTALE (t>2)' if t > 2 else 'nessun vantaggio statisticamente solido'}")
    return {'label': label, 'w': w_star, 'll_mkt': lk.mean(), 'll_blend': lb.mean(),
            'gain': diff.mean(), 't': t}

if __name__ == '__main__':
    import sys
    f = sys.argv[1]
    d = pd.read_csv(f, parse_dates=['MatchDate']).sort_values('MatchDate').reset_index(drop=True)
    r = []
    r.append(analyse(d, ('p_H','p_D','p_A'), ('mkt_H','mkt_D','mkt_A'),
                     lambda x: pd.get_dummies(x.res)[['H','D','A']].values.astype(float), '1X2'))
    d2 = d.dropna(subset=['mkt_O25']).reset_index(drop=True)
    r.append(analyse(d2, ('p_O25','p_U25'), ('mkt_O25','mkt_U25'),
                     lambda x: np.c_[(x.TotGoals > 2.5).astype(float), (x.TotGoals <= 2.5).astype(float)],
                     'Over/Under 2.5'))
    pd.DataFrame(r).to_csv('outputs/blend_results.csv', index=False)
