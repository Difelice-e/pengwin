import sys, os, itertools, time, pandas as pd, numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest.walkforward_xg import run_league, evaluate
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
D = None
def init():
    global D
    D = pd.read_csv(f'{ROOT}/data/processed/xg_final.csv', parse_dates=['MatchDate'], low_memory=False)
def job(a):
    w, div = a
    return (w, div, run_league(D, div, w, 0.0018, 1095, 2017, 2019))
if __name__ == '__main__':
    combos = list(itertools.product([1.0, 0.6, 0.3, 0.0], ['E0','I1']))
    t0 = time.time()
    with Pool(2, initializer=init) as pool: res = pool.map(job, combos)
    agg = {}
    for w, div, p in res: agg.setdefault(w, []).append(p)
    print(f"completato in {time.time()-t0:.0f}s\n")
    print(f"{'w (peso gol)':>13s} {'n':>6s} {'modello':>9s} {'Pinnacle':>9s} {'delta':>9s}")
    for w, ps in sorted(agg.items()):
        e = evaluate(pd.concat(ps, ignore_index=True))
        if e: print(f"{w:13.2f} {e['n']:6d} {e['ll_model']:9.5f} {e['ll_market']:9.5f} {e['delta']:+9.5f}")
