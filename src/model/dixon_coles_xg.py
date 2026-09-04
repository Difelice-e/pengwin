"""Dixon-Coles che stima le forze su un obiettivo misto gol/xG.

target = w * gol + (1-w) * xG
w = 1 -> modello base (solo gol).  w = 0 -> solo xG.
I gol restano il riferimento per la correzione tau sui punteggi bassi, perche' e'
la distribuzione dei GOL che dobbiamo prevedere; gli xG informano solo la media attesa.
"""
import os as _os, sys as _sys
# I pacchetti installati a mano non sopravvivono alla sessione: la home della VM
# locale e' effimera. scipy sta nella cartella pengwin/pylibs, che invece resta.
_libs = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "pylibs")
if _os.path.isdir(_libs) and _libs not in _sys.path:
    _sys.path.append(_libs)

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

def _tau(x, y, lam, mu, rho):
    t = np.ones_like(lam)
    t = np.where((x==0)&(y==0), 1 - lam*mu*rho, t)
    t = np.where((x==0)&(y==1), 1 + lam*rho,    t)
    t = np.where((x==1)&(y==0), 1 + mu*rho,     t)
    t = np.where((x==1)&(y==1), 1 - rho,        t)
    return t

def fit(hi, ai, th, ta, gh, ga, w_, n, x0=None):
    """hi/ai indici squadre; th/ta target (gol misti xG); gh/ga gol interi per tau; w_ pesi temporali."""
    th = np.asarray(th, float); ta = np.asarray(ta, float)
    gh = np.asarray(gh, float); ga = np.asarray(ga, float); w_ = np.asarray(w_, float)
    def nll(p):
        a = p[:n]; b = p[n:2*n]; a = a - a.mean()
        gamma, rho = p[-2], p[-1]
        lam = np.clip(np.exp(a[hi] + b[ai] + gamma), 1e-6, 30)
        mu  = np.clip(np.exp(a[ai] + b[hi]), 1e-6, 30)
        t = _tau(gh, ga, lam, mu, rho)
        if np.any(t <= 0): return 1e10
        ll = np.log(t) + th*np.log(lam) - lam + ta*np.log(mu) - mu
        return -np.sum(w_ * ll)
    if x0 is None:
        x0 = np.concatenate([np.zeros(n), np.zeros(n), [0.25], [-0.05]])
    bnd = [(-3,3)]*n + [(-3,3)]*n + [(-1,1), (-0.25,0.15)]
    r = minimize(nll, x0, method='L-BFGS-B', bounds=bnd, options={'maxiter':500,'ftol':1e-9})
    a = r.x[:n]; a = a - a.mean()
    return {'attack':a, 'defense':r.x[n:2*n], 'home_adv':r.x[-2], 'rho':r.x[-1], 'nll':r.fun}

def score_matrix(par, i, j, mx=12):
    lam = np.exp(par['attack'][i] + par['defense'][j] + par['home_adv'])
    mu  = np.exp(par['attack'][j] + par['defense'][i])
    k = np.arange(mx+1)
    M = np.outer(poisson.pmf(k, lam), poisson.pmf(k, mu))
    r = par['rho']
    M[0,0] *= 1 - lam*mu*r; M[0,1] *= 1 + lam*r; M[1,0] *= 1 + mu*r; M[1,1] *= 1 - r
    return M / M.sum()

def markets(M):
    n = M.shape[0]; idx = np.arange(n)
    home = np.tril(M,-1).sum(); draw = np.trace(M); away = np.triu(M,1).sum()
    tot = idx[:,None] + idx[None,:]
    return {'H':home, 'D':draw, 'A':away, 'O2.5':M[tot>2.5].sum(), 'U2.5':M[tot<2.5].sum(),
            'BTTS':M[1:,1:].sum()}
