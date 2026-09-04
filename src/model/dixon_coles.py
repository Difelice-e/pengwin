"""Modello Dixon-Coles (1997) con decadimento temporale.

Ogni squadra ha una forza offensiva (alpha) e difensiva (beta). I gol attesi sono
    lambda = exp(alpha_casa + beta_ospite + gamma)   [gamma = vantaggio del campo]
    mu     = exp(alpha_ospite + beta_casa)
I gol seguono due Poisson, corrette dal fattore tau di Dixon-Coles che riequilibra
i punteggi bassi (0-0, 1-0, 0-1, 1-1), dove l'indipendenza fra le due Poisson e'
empiricamente falsa. Le partite lontane nel tempo pesano meno: w = exp(-xi * giorni).
"""
import os as _os, sys as _sys
_libs = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "pylibs")
if _os.path.isdir(_libs) and _libs not in _sys.path:
    _sys.path.append(_libs)

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

def _tau(x, y, lam, mu, rho):
    """Correzione Dixon-Coles per i punteggi bassi."""
    t = np.ones_like(lam)
    m00 = (x == 0) & (y == 0); m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0); m11 = (x == 1) & (y == 1)
    t = np.where(m00, 1 - lam*mu*rho, t)
    t = np.where(m01, 1 + lam*rho,    t)
    t = np.where(m10, 1 + mu*rho,     t)
    t = np.where(m11, 1 - rho,        t)
    return t

def fit(home_idx, away_idx, hg, ag, weights, n_teams, x0=None):
    """Stima i parametri per massima verosimiglianza pesata.
    Ritorna dict con attack, defense, home_adv, rho."""
    hg = np.asarray(hg, float); ag = np.asarray(ag, float)
    w = np.asarray(weights, float)

    def nll(p):
        a = p[:n_teams]; b = p[n_teams:2*n_teams]
        a = a - a.mean()                      # vincolo di identificabilita'
        gamma, rho = p[-2], p[-1]
        lam = np.exp(a[home_idx] + b[away_idx] + gamma)
        mu  = np.exp(a[away_idx] + b[home_idx])
        lam = np.clip(lam, 1e-6, 30); mu = np.clip(mu, 1e-6, 30)
        t = _tau(hg, ag, lam, mu, rho)
        if np.any(t <= 0):
            return 1e10
        ll = (np.log(t)
              + hg*np.log(lam) - lam
              + ag*np.log(mu)  - mu)          # termini fattoriali costanti omessi
        return -np.sum(w * ll)

    if x0 is None:
        x0 = np.concatenate([np.zeros(n_teams), np.zeros(n_teams), [0.25], [-0.05]])
    bounds = [(-3, 3)]*n_teams + [(-3, 3)]*n_teams + [(-1, 1), (-0.25, 0.15)]
    r = minimize(nll, x0, method='L-BFGS-B', bounds=bounds,
                 options={'maxiter': 500, 'ftol': 1e-9})
    a = r.x[:n_teams]; a = a - a.mean()
    return {'attack': a, 'defense': r.x[n_teams:2*n_teams],
            'home_adv': r.x[-2], 'rho': r.x[-1],
            'success': r.success, 'nll': r.fun}

def score_matrix(par, i, j, max_goals=12):
    """Matrice di probabilita' dei punteggi per casa=i, ospite=j."""
    lam = np.exp(par['attack'][i] + par['defense'][j] + par['home_adv'])
    mu  = np.exp(par['attack'][j] + par['defense'][i])
    k = np.arange(max_goals + 1)
    M = np.outer(poisson.pmf(k, lam), poisson.pmf(k, mu))
    rho = par['rho']
    M[0, 0] *= 1 - lam*mu*rho
    M[0, 1] *= 1 + lam*rho
    M[1, 0] *= 1 + mu*rho
    M[1, 1] *= 1 - rho
    return M / M.sum()

def markets(M):
    """Deriva tutte le probabilita' di mercato dalla matrice dei punteggi."""
    n = M.shape[0]
    idx = np.arange(n)
    home = np.tril(M, -1).sum()      # gol casa > gol ospite
    draw = np.trace(M)
    away = np.triu(M, 1).sum()
    tot = idx[:, None] + idx[None, :]
    out = {'H': home, 'D': draw, 'A': away,
           '1X': home + draw, 'X2': draw + away, '12': home + away,
           'BTTS': M[1:, 1:].sum()}
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        out[f'O{line}'] = M[tot > line].sum()
        out[f'U{line}'] = M[tot < line].sum()
    return out
