"""Registro delle giocate simulate. Ogni giocata e' scritta PRIMA dell'evento e mai modificata."""
import os, pandas as pd, numpy as np
from datetime import datetime
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEDGER = f'{ROOT}/outputs/ledger.csv'
COLS = ['bet_id','placed_at','MatchDate','Div','HomeTeam','AwayTeam','sel','p','odds','stake',
        'status','result','payout','pnl','settled_at']

def load():
    if os.path.exists(LEDGER):
        d = pd.read_csv(LEDGER, parse_dates=['MatchDate'])
        return d
    return pd.DataFrame(columns=COLS)

def add(sel_df):
    """Aggiunge le giocate nuove. Due protezioni:
    - non duplica una giocata identica (stessa partita, stesso mercato);
    - non accetta partite che non siano SUCCESSIVE all'ultima gia' registrata,
      cioe' non riapre un turno gia' selezionato."""
    led = load()
    existing = set(zip(led.get('MatchDate', pd.Series(dtype='datetime64[ns]')).astype(str),
                       led.get('HomeTeam', pd.Series(dtype=str)), led.get('sel', pd.Series(dtype=str))))
    # confine del turno gia' selezionato: si accettano solo partite SUCCESSIVE all'ultima
    # gia' presente nel registro. Senza questo, una seconda passata sullo stesso turno
    # sceglierebbe partite che alla prima non superavano la soglia (il modello nel frattempo
    # si e' ristimato), gonfiando l'esposizione e invalidando la previsione pre-registrata.
    ultima = pd.to_datetime(led['MatchDate']).max() if len(led) else None
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    rows, nid = [], (led.bet_id.max()+1 if len(led) else 1)
    for _, r in sel_df.iterrows():
        key = (str(pd.Timestamp(r.MatchDate)), r.HomeTeam, r.sel)
        if key in existing: continue
        if ultima is not None and pd.Timestamp(r.MatchDate) <= ultima: continue  # turno gia' selezionato
        rows.append({'bet_id':nid, 'placed_at':now, 'MatchDate':r.MatchDate, 'Div':r.Div,
                     'HomeTeam':r.HomeTeam, 'AwayTeam':r.AwayTeam, 'sel':r.sel,
                     'p':round(r.p,4), 'odds':r.odds_max, 'stake':r.stake,
                     'status':'aperta', 'result':'', 'payout':np.nan, 'pnl':np.nan, 'settled_at':''})
        nid += 1
    if rows:
        led = pd.concat([led, pd.DataFrame(rows)], ignore_index=True)
        led.to_csv(LEDGER, index=False)
    return len(rows), led
