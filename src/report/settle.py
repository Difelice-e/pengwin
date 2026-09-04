"""Contabilizza le giocate aperte confrontandole coi risultati scaricati, e rigenera i dati
della dashboard. Non modifica mai una giocata gia' contabilizzata."""
import os, sys, json, numpy as np, pandas as pd
from zoneinfo import ZoneInfo
ROMA = ZoneInfo('Europe/Rome')   # la VM gira in UTC: gli orari mostrati sono ora italiana
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEDGER = f'{ROOT}/outputs/ledger.csv'
RESULTS = f'{ROOT}/data/raw/current/pengwin_results.csv'   # scaricato dal browser

def esito(sel, hg, ag):
    tot = hg + ag
    if sel == '1':          return hg > ag
    if sel == 'X':          return hg == ag
    if sel == '2':          return hg < ag
    if sel == 'Over 2.5':   return tot > 2.5
    if sel == 'Under 2.5':  return tot < 2.5
    return None

def main():
    led = pd.read_csv(LEDGER, parse_dates=['MatchDate'])
    if not os.path.exists(RESULTS):
        print(f"nessun file risultati in {RESULTS} — scaricalo prima"); return led
    r = pd.read_csv(RESULTS)
    r['MatchDate'] = pd.to_datetime(r['Date'], dayfirst=True, errors='coerce')
    r = r.dropna(subset=['MatchDate','FTHG','FTAG'])
    key = {(row.Div, row.MatchDate.date(), row.HomeTeam): (int(row.FTHG), int(row.FTAG))
           for _, row in r.iterrows()}
    n = 0
    for i, b in led.iterrows():
        if b.status != 'aperta': continue
        k = (b.Div, b.MatchDate.date(), b.HomeTeam)
        if k not in key: continue
        hg, ag = key[k]
        w = esito(b.sel, hg, ag)
        if w is None: continue
        payout = b.stake * b.odds if w else 0.0
        led.at[i, 'status']    = 'vinta' if w else 'persa'
        led.at[i, 'result']    = f"{hg}-{ag}"
        led.at[i, 'payout']    = round(payout, 2)
        led.at[i, 'pnl']       = round(payout - b.stake, 2)
        led.at[i, 'settled_at']= pd.Timestamp.now(tz=ROMA).strftime('%Y-%m-%d %H:%M')
        n += 1
    led.to_csv(LEDGER, index=False)
    s = led[led.status.isin(['vinta','persa'])]
    print(f"contabilizzate {n} nuove giocate | totale concluse {len(s)}, aperte {(led.status=='aperta').sum()}")
    if len(s):
        print(f"P&L {s.pnl.sum():+.2f} EUR su {s.stake.sum():.2f} di volume  (ROI {100*s.pnl.sum()/s.stake.sum():+.2f}%)")
        print(f"vinte {(s.status=='vinta').sum()}/{len(s)}")
    return led

def rebuild_dashboard_data(led=None):
    """Rigenera outputs/dashboard_data.json mantenendo la banda pre-registrata INVARIATA."""
    led = pd.read_csv(LEDGER, parse_dates=['MatchDate']) if led is None else led
    prev = json.load(open(f'{ROOT}/outputs/dashboard_data.json'))
    bets = led.assign(MatchDate=led.MatchDate.dt.strftime('%d/%m')).to_dict('records')
    for b in bets:
        for k in ('payout','pnl'):
            if pd.isna(b[k]): b[k] = None
        b['result'] = '' if pd.isna(b['result']) else b['result']
    prev['bets'] = bets                      # la banda 'band' NON viene ricalcolata: e' pre-registrata
    prev['meta']['n_bets'] = len(led)
    prev['meta']['esposto'] = round(float(led[led.status=='aperta'].stake.sum()), 2)
    prev['meta']['aggiornato'] = pd.Timestamp.now(tz=ROMA).strftime('%d/%m/%Y %H:%M')
    open(f'{ROOT}/outputs/dashboard_data.json','w').write(json.dumps(prev, ensure_ascii=False))
    print("dashboard_data.json aggiornato")

if __name__ == '__main__':
    led = main()
    rebuild_dashboard_data(led)
