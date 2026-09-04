import sys, os, time, pandas as pd
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest.walkforward import run_league, evaluate
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
XI, LB, S0, S1 = 0.0018, 1825, 2010, 2024
D = None
def init():
    global D
    D = pd.read_csv(f'{ROOT}/data/processed/top5.csv', parse_dates=['MatchDate'], low_memory=False)
def job(div):
    t0 = time.time()
    p = run_league(D, div, XI, LB, S0, S1)
    print(f"{div}: {len(p)} previsioni in {time.time()-t0:.0f}s", flush=True)
    return p
if __name__ == '__main__':
    with Pool(2, initializer=init) as pool:
        res = pool.map(job, ['E0','D1','I1','SP1','F1'])
    df = pd.concat(res, ignore_index=True).sort_values('MatchDate').reset_index(drop=True)
    df.to_csv(f'{ROOT}/outputs/predictions_walkforward.csv', index=False)
    e = evaluate(df)
    print(f"\nTOTALE n={e['n']}  1X2 modello {e['ll_model']:.5f} mercato {e['ll_market']:.5f} delta {e['delta']:+.5f}")
    print(f"           O/U modello {e['ll_model_ou']:.5f} mercato {e['ll_market_ou']:.5f} delta {e['delta_ou']:+.5f}")
