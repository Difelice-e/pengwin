"""Screening: il modello ha edge nei campionati meno battuti dai modellisti?
Stessa pipeline, stessi parametri, mercati diversi."""
import sys, os, time, pandas as pd, numpy as np
from multiprocessing import Pool
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest.walkforward import run_league, evaluate
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
XI, LB, S0, S1 = 0.0018, 1825, 2012, 2024
DIVS = ['E1','E2','E3','SC0','SC2','I2','SP2','G1']
D = None
def init():
    global D
    D = pd.read_csv(f'{ROOT}/data/processed/all_leagues.csv', parse_dates=['MatchDate'], low_memory=False)
def job(div):
    t0 = time.time()
    p = run_league(D, div, XI, LB, S0, S1)
    if len(p):
        e = evaluate(p)
        print(f"{div}: n={e['n']:5d} 1X2 mod {e['ll_model']:.5f} mkt {e['ll_market']:.5f} "
              f"delta {e['delta']:+.5f} | O/U delta {e.get('delta_ou', float('nan')):+.5f} "
              f"[{time.time()-t0:.0f}s]", flush=True)
    return p
if __name__ == '__main__':
    with Pool(2, initializer=init) as pool:
        res = pool.map(job, DIVS)
    df = pd.concat([r for r in res if len(r)], ignore_index=True).sort_values('MatchDate').reset_index(drop=True)
    df.to_csv(f'{ROOT}/outputs/predictions_minor.csv', index=False)
    e = evaluate(df)
    print(f"\nAGGREGATO n={e['n']} 1X2 delta {e['delta']:+.5f} | O/U delta {e['delta_ou']:+.5f}")
