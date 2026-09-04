"""Taratura di xi (decadimento) e lookback su periodo di validazione separato."""
import sys, os, itertools, time, pandas as pd, numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest.walkforward import run_league, evaluate
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
D = None
def init():
    global D
    D = pd.read_csv(f'{ROOT}/data/processed/top5.csv', parse_dates=['MatchDate'], low_memory=False)
def job(a):
    xi, lb, div = a
    p = run_league(D, div, xi, lb, 2012, 2015)
    return (xi, lb, div, p)
if __name__ == '__main__':
    XI = [0.0010, 0.0018, 0.0025, 0.0035, 0.0050]
    LB = [730, 1095, 1825]
    DIV = ['I1', 'E0']
    combos = list(itertools.product(XI, LB, DIV))
    t0 = time.time()
    with Pool(processes=min(len(combos), os.cpu_count()), initializer=init) as pool:
        res = pool.map(job, combos)
    agg = {}
    for xi, lb, div, p in res:
        agg.setdefault((xi, lb), []).append(p)
    rows = []
    for (xi, lb), ps in agg.items():
        e = evaluate(pd.concat(ps, ignore_index=True))
        rows.append({'xi': xi, 'lookback': lb, 'n': e['n'],
                     'll_mod': e['ll_model'], 'll_mkt': e['ll_market'], 'delta': e['delta'],
                     'delta_ou': e.get('delta_ou', np.nan)})
    df = pd.DataFrame(rows).sort_values('delta')
    print(f"\ncompletato in {time.time()-t0:.0f}s\n")
    print(df.to_string(index=False, float_format=lambda x: f"{x:.5f}"))
    df.to_csv(f'{ROOT}/outputs/grid_validation.csv', index=False)
