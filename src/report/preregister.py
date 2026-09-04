"""Previsione PRE-REGISTRATA dell'esperimento, calcolata prima di conoscere qualsiasi esito.
Gli esiti sono estratti dalle probabilita' IMPLICITE NEL MERCATO (de-marginate), non da quelle
del modello: il backtest ha stabilito che il mercato e' meglio calibrato. Se il modello avesse
ragione, il risultato reale cadrebbe sistematicamente sopra questa distribuzione."""
import os, sys, numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, f'{ROOT}/src')
from ingest.build_dataset import shin_probs

def simulate(bets, n_round_totali=5, n_sim=20000, seed=42):
    """bets: le giocate di un turno. Estrapola a n_round_totali turni."""
    rng = np.random.default_rng(seed)
    stake = bets.stake.values; odds = bets.odds.values if 'odds' in bets else bets.odds_max.values
    p_true = 1.0/odds                       # prob implicita grezza
    p_true = p_true * (1 - 0.05)            # sconto prudenziale per il margine sulle quote massime
    pnl = np.zeros(n_sim)
    for _ in range(n_round_totali):
        win = rng.random((n_sim, len(stake))) < p_true
        pnl += np.where(win, stake*(odds-1), -stake).sum(axis=1)
    return pnl

if __name__ == '__main__':
    b = pd.read_csv(sys.argv[1] if len(sys.argv)>1 else f'{ROOT}/outputs/ledger.csv')
    pnl = simulate(b)
    turnover = b.stake.sum()*5
    q = np.percentile(pnl, [2.5, 25, 50, 75, 97.5])
    print("=== PREVISIONE PRE-REGISTRATA (2 settimane, ~5 turni) ===")
    print(f"giocate per turno: {len(b)} | esposizione per turno: {b.stake.sum():.2f} EUR")
    print(f"volume totale stimato: {turnover:.0f} EUR su ~{len(b)*5} giocate\n")
    print(f"  P&L atteso (mediana)      {q[2]:+8.2f} EUR   ({100*q[2]/turnover:+.2f}% ROI)")
    print(f"  intervallo 50%            {q[1]:+8.2f} .. {q[3]:+8.2f} EUR")
    print(f"  intervallo 95%            {q[0]:+8.2f} .. {q[4]:+8.2f} EUR")
    print(f"\n  bankroll finale atteso    {1000+q[2]:8.2f} EUR")
    print(f"  intervallo 95%            {1000+q[0]:8.2f} .. {1000+q[4]:8.2f} EUR")
    print(f"\n  probabilita' di chiudere in profitto: {100*(pnl>0).mean():.1f}%")
    print("\nSe il modello NON ha edge (cio' che dice il backtest), il risultato reale cadra'")
    print("dentro questo intervallo. Solo un esito ben SOPRA la mediana, ripetuto, sarebbe indizio")
    print("di un vantaggio -- e due settimane non bastano comunque a dimostrarlo.")
