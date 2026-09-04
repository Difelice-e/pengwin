import sys, os, time, pandas as pd
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest.walkforward_xg import run_league, evaluate
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
W, XI, LB, S0, S1 = 0.35, 0.0018, 1095, 2020, 2025
D = None
def init():
    global D
    D = pd.read_csv(f'{ROOT}/data/processed/xg_final.csv', parse_dates=['MatchDate'], low_memory=False)
def job(div):
    t0=time.time(); p = run_league(D, div, W, XI, LB, S0, S1)
    e = evaluate(p)
    if e: print(f"{div}: n={e['n']:5d} mod {e['ll_model']:.5f} Pinn {e['ll_market']:.5f} delta {e['delta']:+.5f} [{time.time()-t0:.0f}s]", flush=True)
    return p
if __name__ == '__main__':
    with Pool(2, initializer=init) as pool: res = pool.map(job, ['E0','D1','I1','SP1','F1'])
    df = pd.concat(res, ignore_index=True).sort_values('MatchDate').reset_index(drop=True)
    df.to_csv(f'{ROOT}/outputs/predictions_xg.csv', index=False)
    e = evaluate(df)
    print(f"\nTOTALE n={e['n']} modello {e['ll_model']:.5f} Pinnacle {e['ll_market']:.5f} delta {e['delta']:+.5f}")
