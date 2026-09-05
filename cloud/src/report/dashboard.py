"""
Genera la dashboard come pagina HTML autonoma, leggendo tutto da Supabase.

Scelta di fondo: la pagina e' ordinata per peso probatorio, non per estetica.
Il CLV viene prima del ROI perche' e' il criterio dichiarato nel par.6 della
specifica; il ROI su poche decine di giocate e' rumore e viene presentato come
tale. Una dashboard che apre col ROI positivo racconterebbe una storia che i
dati non sostengono.

Uso:  python -m src.report.dashboard [percorso_output.html]
"""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.client import client  # noqa: E402

FUSO = ZoneInfo("Europe/Rome")
LEGHE = {"E0": "Premier League", "I1": "Serie A", "SP1": "La Liga",
         "D1": "Bundesliga", "F1": "Ligue 1"}
SEL_IT = {("1X2", "1"): "1", ("1X2", "X"): "X", ("1X2", "2"): "2",
          ("OU25", "over"): "Over 2.5", ("OU25", "under"): "Under 2.5",
          ("GG", "gg"): "Gol", ("GG", "ng"): "NoGol"}


def e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def num(v, dec=2, segno=False):
    if v is None:
        return "—"
    s = f"{float(v):+.{dec}f}" if segno else f"{float(v):.{dec}f}"
    return s.replace(".", ",")


def pct(v, dec=1, segno=True):
    if v is None:
        return "—"
    s = f"{float(v)*100:+.{dec}f}" if segno else f"{float(v)*100:.{dec}f}"
    return s.replace(".", ",") + "%"


# ----------------------------------------------------------------- dati
def raccogli(db=None) -> dict:
    db = db or client()
    g = db.select("giocate", colonne="*", ordina="data_partita.asc")
    prereg = {r["chiave"]: r["valore"] for r in db.select("preregistrazioni", colonne="chiave,valore")}
    log = db.select("log_esecuzioni", colonne="eseguito_il,job,esito,righe",
                    ordina="eseguito_il.desc", limite=6)
    part = db.select("partite", colonne="lega,data", filtri={"stagione": "eq.2627"})
    fresh = {}
    for r in part:
        fresh[r["lega"]] = max(fresh.get(r["lega"], ""), r["data"])

    chiuse = [x for x in g if x["esito"] in ("vinta", "persa")]
    aperte = [x for x in g if x["esito"] == "aperta"]
    stake = sum(float(x["stake"]) for x in chiuse)
    ritorno = sum(float(x["ritorno"] or 0) for x in chiuse)
    clvs = [float(x["clv"]) for x in chiuse if x["clv"] is not None]

    turni = sorted({x["turno"] for x in g if x["turno"]})
    cum, tot = [0.0], 0.0
    for t in turni:
        ch = [x for x in chiuse if x["turno"] == t]
        tot += sum(float(x["ritorno"] or 0) - float(x["stake"]) for x in ch)
        cum.append(round(tot, 2))

    return {
        "aperte": aperte, "chiuse": chiuse, "turni": turni, "cum": cum,
        "stake": stake, "ritorno": ritorno,
        "roi": (ritorno - stake) / stake if stake else None,
        "pnl": ritorno - stake,
        "vinte": sum(1 for x in chiuse if x["esito"] == "vinta"),
        "clv_medio": sum(clvs) / len(clvs) if clvs else None,
        "clv_pos": sum(1 for c in clvs if c > 0), "clv_n": len(clvs), "clvs": clvs,
        "esposto": sum(float(x["stake"]) for x in aperte),
        "band": prereg.get("band_esperimento"),
        "meta": prereg.get("meta_esperimento", {}),
        "log": log, "fresh": fresh,
        "aggiornato": datetime.now(FUSO).strftime("%d/%m/%Y %H:%M"),
    }


# --------------------------------------------------------------- grafici
def grafico_banda(d: dict) -> str:
    b = d["band"]
    if not b:
        return "<p class='vuoto'>banda pre-registrata non disponibile</p>"
    W, H = 720, 312
    L, R, T, B = 58, 18, 18, 34
    rounds = b["rounds"]
    lo, hi = min(b["p2_5"]) * 1.05, max(b["p97_5"]) * 1.15
    def X(i): return L + (W - L - R) * (i / max(rounds))
    def Y(v): return T + (H - T - B) * (1 - (v - lo) / (hi - lo))

    def area(sup, inf):
        p = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(sup))
        q = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in reversed(list(enumerate(inf))))
        return f'<polygon points="{p} {q}" />'

    linea = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(b["p50"]))
    reale = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(d["cum"]))
    punti = "".join(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="4" class="pt" />'
                    for i, v in enumerate(d["cum"]))
    ticks = ""
    passo = 60
    v = int(lo // passo) * passo
    while v <= hi:
        ticks += (f'<line x1="{L}" y1="{Y(v):.1f}" x2="{W-R}" y2="{Y(v):.1f}" class="grid" />'
                  f'<text x="{L-8}" y="{Y(v)+4:.1f}" class="tick" text-anchor="end">{v:+d}</text>')
        v += passo
    xlab = "".join(f'<text x="{X(i):.1f}" y="{H-12}" class="tick" text-anchor="middle">{i}</text>'
                   for i in rounds)
    return f'''<svg viewBox="0 0 {W} {H}" class="chart" role="img"
     aria-label="Banda di simulazione pre-registrata e andamento reale del P&amp;L">
  {ticks}
  <g class="banda-est">{area(b["p97_5"], b["p2_5"])}</g>
  <g class="banda-int">{area(b["p75"], b["p25"])}</g>
  <polyline points="{linea}" class="mediana" />
  <polyline points="{reale}" class="reale" />
  {punti}{xlab}
  <text x="{(L+W-R)/2:.0f}" y="{H-1}" class="tick" text-anchor="middle">turni conclusi</text>
</svg>'''


def grafico_clv(d: dict) -> str:
    c = d["clvs"]
    if not c:
        return "<p class='vuoto'>nessuna giocata conclusa con quota di chiusura disponibile</p>"
    W, H, L, R = 720, 92, 18, 18
    lo, hi = min(min(c), -0.06), max(max(c), 0.06)
    def X(v): return L + (W - L - R) * ((v - lo) / (hi - lo))
    marks = "".join(
        f'<line x1="{X(v):.1f}" y1="24" x2="{X(v):.1f}" y2="56" '
        f'class="{"clvpos" if v > 0 else "clvneg"}" />' for v in c)
    med = sum(c) / len(c)
    return f'''<svg viewBox="0 0 {W} {H}" class="chart" role="img"
     aria-label="Distribuzione del CLV per giocata conclusa">
  <line x1="{L}" y1="40" x2="{W-R}" y2="40" class="grid" />
  {marks}
  <line x1="{X(0):.1f}" y1="16" x2="{X(0):.1f}" y2="64" class="zero" />
  <text x="{X(0):.1f}" y="80" class="tick" text-anchor="middle">0</text>
  <line x1="{X(med):.1f}" y1="20" x2="{X(med):.1f}" y2="60" class="mediaclv" />
  <text x="{W-R}" y="14" class="tick" text-anchor="end">media {pct(med)}</text>
  <text x="{L}" y="14" class="tick" text-anchor="start">quota peggiore della chiusura</text>
  <text x="{W-R}" y="80" class="tick" text-anchor="end">quota migliore</text>
</svg>'''


# ---------------------------------------------------------------- tabelle
def tipo(x) -> str:
    """Etichetta della selezione. E' anche il valore su cui filtra la pagina."""
    return SEL_IT.get((x["mercato"], x["selezione"]), x["selezione"])


def risultato(x) -> str:
    """Il punteggio finale e' annotato in `note` nella forma 'risultato 1-3'."""
    n = (x.get("note") or "").strip()
    return n[len("risultato"):].strip() if n.lower().startswith("risultato") else ""


def riga_aperta(x) -> str:
    return f'''<tr data-lega="{e(x["lega"])}" data-tipo="{e(tipo(x))}">
 <td class="d">{e(x["data_partita"][8:10])}/{e(x["data_partita"][5:7])}</td>
 <td><span class="lega">{e(x["lega"])}</span></td>
 <td class="m">{e(x["casa"])} <span class="v">–</span> {e(x["trasferta"])}</td>
 <td><span class="sel">{e(tipo(x))}</span></td>
 <td class="n">{num(x["prob_modello"], 3)}</td>
 <td class="n">{num(x["quota"])}</td>
 <td class="n edge">{pct(x["edge"])}</td>
 <td class="n">{num(x["stake"])}</td></tr>'''


def riga_chiusa(x) -> str:
    vinta = x["esito"] == "vinta"
    pnl = float(x["ritorno"] or 0) - float(x["stake"])
    clv = x["clv"]
    # zero non e' un CLV negativo: quota presa e chiusura coincidono, non si colora
    cls = "" if clv is None or float(clv) == 0 else ("pos" if float(clv) > 0 else "neg")
    return f'''<tr data-lega="{e(x["lega"])}" data-tipo="{e(tipo(x))}">
 <td class="d">{e(x["data_partita"][8:10])}/{e(x["data_partita"][5:7])}</td>
 <td><span class="lega">{e(x["lega"])}</span></td>
 <td class="m">{e(x["casa"])} <span class="v">–</span> {e(x["trasferta"])}</td>
 <td><span class="sel">{e(tipo(x))}</span></td>
 <td class="n">{num(x["quota"])}</td>
 <td class="n">{num(x["quota_chiusura"]) if x["quota_chiusura"] else "—"}</td>
 <td class="n {cls}">{pct(clv) if clv is not None else "—"}</td>
 <td class="n ris">{e(risultato(x)) or "—"}</td>
 <td><span class="esito {'v' if vinta else 'p'}">{'vinta' if vinta else 'persa'}</span></td>
 <td class="n {'pos' if pnl > 0 else 'neg'}">{num(pnl, 2, True)}</td></tr>'''


def barra_filtri(d: dict) -> str:
    """Filtri per stato, campionato e tipo di giocata.

    Deliberatamente NON ricalcolano CLV e ROI del riquadro in alto: su 26 giocate
    concluse, un CLV per singolo campionato sarebbe costruito su 3-8 righe e non
    direbbe nulla. La pagina mostra quante righe sono in vista, non una metrica
    nuova per ogni combinazione di filtri.
    """
    tutte = d["aperte"] + d["chiuse"]
    n_lega, n_tipo = {}, {}
    for x in tutte:
        n_lega[x["lega"]] = n_lega.get(x["lega"], 0) + 1
        n_tipo[tipo(x)] = n_tipo.get(tipo(x), 0) + 1

    def chip(gruppo, val, testo, n=None):
        vuoto = " vuoto" if n == 0 else ""
        cnt = f' <span class="cnt">{n}</span>' if n is not None else ""
        return (f'<button type="button" class="chip{vuoto}" data-gruppo="{gruppo}" '
                f'data-val="{e(val)}">{e(testo)}{cnt}</button>')

    stato = "".join([chip("stato", "tutte", "Tutte"),
                     chip("stato", "aperte", "In corso", len(d["aperte"])),
                     chip("stato", "chiuse", "Concluse", len(d["chiuse"]))])
    leghe = chip("lega", "", "Tutti") + "".join(
        chip("lega", k, v, n_lega.get(k, 0)) for k, v in LEGHE.items())
    ordine = ["1", "X", "2", "Over 2.5", "Under 2.5", "Gol", "NoGol"]
    presenti = [t for t in ordine if t in n_tipo] + [t for t in n_tipo if t not in ordine]
    tipi = chip("tipo", "", "Tutte") + "".join(chip("tipo", t, t, n_tipo[t]) for t in presenti)

    return f'''<div class="filtri" id="filtri">
  <div class="gruppo"><span class="glab">Stato</span><div class="chips">{stato}</div></div>
  <div class="gruppo"><span class="glab">Campionato</span><div class="chips">{leghe}</div></div>
  <div class="gruppo"><span class="glab">Giocata</span><div class="chips">{tipi}</div></div>
  <div class="filtro-stato">
    <span id="conteggio">tutte le {len(tutte)} giocate</span>
    <button type="button" id="azzera" hidden>azzera i filtri</button>
  </div>
</div>'''


SCRIPT = """
(function () {
  var barra = document.getElementById('filtri');
  if (!barra) return;
  var sel = { stato: 'tutte', lega: '', tipo: '' };
  var sezioni = { aperte: document.getElementById('sez-aperte'),
                  chiuse: document.getElementById('sez-chiuse') };

  function applica() {
    var visti = 0;
    ['aperte', 'chiuse'].forEach(function (k) {
      var sez = sezioni[k];
      if (!sez) return;
      var mostraSez = (sel.stato === 'tutte' || sel.stato === k);
      sez.hidden = !mostraSez;
      var n = 0;
      var righe = sez.querySelectorAll('tbody tr[data-lega]');
      Array.prototype.forEach.call(righe, function (tr) {
        var ok = (!sel.lega || tr.getAttribute('data-lega') === sel.lega) &&
                 (!sel.tipo || tr.getAttribute('data-tipo') === sel.tipo);
        tr.hidden = !ok;
        if (ok) n++;
      });
      var vuoto = sez.querySelector('.nessuna');
      if (vuoto) vuoto.hidden = (n > 0);
      var cnt = sez.querySelector('.conta');
      if (cnt) cnt.textContent = n + (k === 'aperte' ? ' in corso' : ' concluse');
      if (mostraSez) visti += n;
    });

    var filtra = (sel.stato !== 'tutte' || sel.lega || sel.tipo);
    var etichetta = document.getElementById('conteggio');
    if (etichetta) {
      etichetta.textContent = filtra
        ? (visti === 1 ? '1 giocata in vista' : visti + ' giocate in vista')
        : 'tutte le ' + visti + ' giocate';
    }
    var azzera = document.getElementById('azzera');
    if (azzera) azzera.hidden = !filtra;

    Array.prototype.forEach.call(barra.querySelectorAll('.chip'), function (b) {
      var attivo = sel[b.getAttribute('data-gruppo')] === b.getAttribute('data-val');
      b.classList.toggle('attivo', attivo);
      b.setAttribute('aria-pressed', attivo ? 'true' : 'false');
    });
  }

  barra.addEventListener('click', function (ev) {
    var b = ev.target.closest ? ev.target.closest('.chip') : null;
    if (b) {
      var g = b.getAttribute('data-gruppo'), v = b.getAttribute('data-val');
      // riclicca il filtro attivo per toglierlo
      sel[g] = (sel[g] === v && g !== 'stato') ? '' : v;
      applica();
      return;
    }
    if (ev.target.id === 'azzera') {
      sel = { stato: 'tutte', lega: '', tipo: '' };
      applica();
    }
  });

  applica();
})();
"""


CSS = """
:root{
  --ground:#F3F5F9; --surface:#FFFFFF; --raise:#FAFBFD;
  --ink:#171A21; --muted:#59616F; --faint:#8B93A2; --line:#DCE1EA;
  --accent:#3A4C8C; --accent-soft:#E7EAF6;
  --pos:#17705F; --neg:#A2413A; --warn:#8E6415;
  --band-out:rgba(58,76,140,.10); --band-in:rgba(58,76,140,.20);
}
@media (prefers-color-scheme:dark){ :root:not([data-theme="light"]){
  --ground:#0F1219; --surface:#171B24; --raise:#1D222D;
  --ink:#E7EAF1; --muted:#8F98A8; --faint:#6C7484; --line:#242A35;
  --accent:#8A9BE0; --accent-soft:#1E2436;
  --pos:#46B39C; --neg:#E28A80; --warn:#D2A24E;
  --band-out:rgba(138,155,224,.10); --band-in:rgba(138,155,224,.20);
}}
:root[data-theme="dark"]{
  --ground:#0F1219; --surface:#171B24; --raise:#1D222D;
  --ink:#E7EAF1; --muted:#8F98A8; --faint:#6C7484; --line:#242A35;
  --accent:#8A9BE0; --accent-soft:#1E2436;
  --pos:#46B39C; --neg:#E28A80; --warn:#D2A24E;
  --band-out:rgba(138,155,224,.10); --band-in:rgba(138,155,224,.20);
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);
  font-family:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:40px 22px 72px;
  display:flex;flex-direction:column;gap:34px}
h1,h2,h3{font-family:Newsreader,Georgia,serif;font-weight:500;text-wrap:balance;margin:0}
h1{font-size:33px;letter-spacing:-.01em;line-height:1.15}
h2{font-size:21px;letter-spacing:-.005em}
.eyebrow{font-size:11px;letter-spacing:.13em;text-transform:uppercase;
  color:var(--faint);font-weight:600}
.sub{color:var(--muted);margin:6px 0 0;max-width:66ch}
header{border-bottom:1px solid var(--line);padding-bottom:26px}
.head-row{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;flex-wrap:wrap}
.stamp{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:12px;color:var(--faint);
  text-align:right;line-height:1.7}

/* verdetto: il pezzo che porta il senso della pagina */
.verdetto{background:var(--surface);border:1px solid var(--line);
  border-left:3px solid var(--warn);padding:20px 24px;border-radius:2px}
.verdetto p{margin:8px 0 0;color:var(--muted);max-width:72ch}
.verdetto strong{color:var(--ink);font-weight:600}

section{display:flex;flex-direction:column;gap:14px}
.shead{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap}
.nota{color:var(--muted);font-size:13.5px;max-width:70ch;margin:0}

/* metriche: CLV per primo, e' il criterio dichiarato */
.metriche{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line);border-radius:2px;overflow:hidden}
.met{background:var(--surface);padding:18px 20px}
.met .k{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);
  font-weight:600}
.met .val{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:27px;
  font-variant-numeric:tabular-nums;margin-top:7px;letter-spacing:-.02em}
.met .note{font-size:12.5px;color:var(--muted);margin-top:3px}
.met.primaria{background:var(--raise)}
.pos{color:var(--pos)} .neg{color:var(--neg)}

.pannello{background:var(--surface);border:1px solid var(--line);border-radius:2px;
  padding:20px 22px}
.chart{width:100%;height:auto;display:block}
.grid{stroke:var(--line);stroke-width:1}
.tick{fill:var(--faint);font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:10.5px}
.banda-est polygon{fill:var(--band-out)}
.banda-int polygon{fill:var(--band-in)}
.mediana{fill:none;stroke:var(--accent);stroke-width:1.5;stroke-dasharray:5 4}
.reale{fill:none;stroke:var(--ink);stroke-width:2.25}
.pt{fill:var(--ink)}
.clvpos{stroke:var(--pos);stroke-width:2.5;opacity:.8}
.clvneg{stroke:var(--neg);stroke-width:2.5;opacity:.8}
.zero{stroke:var(--ink);stroke-width:1.5}
.mediaclv{stroke:var(--warn);stroke-width:2;stroke-dasharray:3 3}
.legenda{display:flex;gap:20px;flex-wrap:wrap;font-size:12.5px;color:var(--muted);
  margin-top:12px}
.legenda i{display:inline-block;width:15px;height:3px;margin-right:7px;vertical-align:middle;
  border-radius:1px}

.tabellone{overflow-x:auto;background:var(--surface);border:1px solid var(--line);
  border-radius:2px}
table{border-collapse:collapse;width:100%;font-size:13.5px;min-width:640px}
th{text-align:left;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--faint);font-weight:600;padding:12px 12px 9px;border-bottom:1px solid var(--line);
  white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:baseline}
tr:last-child td{border-bottom:none}
td.n,th.n{text-align:right;font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-variant-numeric:tabular-nums;white-space:nowrap}
td.d{font-family:"IBM Plex Mono",ui-monospace,monospace;color:var(--muted);white-space:nowrap}
td.m{min-width:210px}
.v{color:var(--faint)}
.lega{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:11px;color:var(--muted);
  background:var(--raise);border:1px solid var(--line);padding:1px 6px;border-radius:2px}
.sel{font-size:12.5px;color:var(--muted);white-space:nowrap}
.edge{color:var(--accent)}
.esito{font-size:11px;letter-spacing:.06em;text-transform:uppercase;font-weight:600;
  padding:2px 8px;border-radius:2px;border:1px solid}
.esito.v{color:var(--pos);border-color:var(--pos)}
.esito.p{color:var(--neg);border-color:var(--neg)}
.stato{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:14px}
.freschezza{background:var(--surface);border:1px solid var(--line);border-radius:2px;
  padding:13px 15px}
.freschezza .l{font-size:12px;color:var(--muted)}
.freschezza .d{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:15px;margin-top:3px}
.freschezza.vecchio{border-color:var(--warn)}
.freschezza.vecchio .d{color:var(--warn)}
.vuoto{color:var(--faint);font-style:italic;margin:0}

/* filtri */
.filtri{background:var(--surface);border:1px solid var(--line);border-radius:2px;
  padding:15px 18px;display:flex;flex-direction:column;gap:11px}
.gruppo{display:flex;align-items:baseline;gap:13px;flex-wrap:wrap}
.glab{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);
  font-weight:600;min-width:86px;flex-shrink:0}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{font:inherit;font-size:13px;color:var(--muted);background:var(--raise);
  border:1px solid var(--line);border-radius:2px;padding:3px 10px;cursor:pointer;
  display:inline-flex;align-items:center;gap:6px;line-height:1.5}
.chip:hover{border-color:var(--accent);color:var(--ink)}
.chip.attivo{background:var(--accent-soft);border-color:var(--accent);color:var(--accent);
  font-weight:600}
.chip .cnt{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:11px;
  color:var(--faint);font-weight:400}
.chip.attivo .cnt{color:var(--accent)}
.chip.vuoto{opacity:.42}
.filtro-stato{display:flex;align-items:center;gap:14px;border-top:1px solid var(--line);
  padding-top:10px;font-size:12.5px;color:var(--muted)}
#azzera{font:inherit;font-size:12.5px;background:none;border:none;padding:0;cursor:pointer;
  color:var(--accent);text-decoration:underline;text-underline-offset:2px}
tr[hidden]{display:none}

footer{border-top:1px solid var(--line);padding-top:20px;color:var(--faint);font-size:12.5px}
footer p{margin:0 0 6px;max-width:74ch}
code{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:12px;
  background:var(--raise);border:1px solid var(--line);padding:1px 5px;border-radius:2px}
@media (max-width:640px){
  h1{font-size:26px} .wrap{padding:26px 15px 52px;gap:26px}
  .gruppo{flex-direction:column;align-items:flex-start;gap:6px}
  .glab{min-width:0}
}
"""


def costruisci(d: dict) -> str:
    clv_neg = d["clv_medio"] is not None and d["clv_medio"] < 0
    quota_pos = f"{d['clv_pos']}/{d['clv_n']}" if d["clv_n"] else "—"

    verdetto = (
        "Il CLV medio è negativo: sulle giocate concluse la quota ottenuta era in media "
        "<strong>peggiore</strong> di quella a cui il mercato ha chiuso. È il segnale che il "
        "par. 6 della specifica indica come precoce e affidabile, e per ora dice che non c'è "
        "vantaggio. Il ROI positivo qui sotto non lo smentisce: su poche decine di giocate è rumore."
    ) if clv_neg else (
        "Il CLV medio è positivo: le quote ottenute battono in media la chiusura del mercato. "
        "Serve un campione molto più ampio prima di chiamarlo vantaggio, ma è il segno giusto."
    )

    fresche = ""
    for lega, nome in LEGHE.items():
        dt = d["fresh"].get(lega)
        vecchio = ""
        if dt:
            giorni = (datetime.now(FUSO).date() - datetime.fromisoformat(dt).date()).days
            vecchio = " vecchio" if giorni > 10 else ""
            testo = f"{dt[8:10]}/{dt[5:7]}"
            nota = f"{giorni} giorni fa"
        else:
            testo, nota, vecchio = "—", "nessun dato", " vecchio"
        fresche += (f'<div class="freschezza{vecchio}"><div class="l">{nome}</div>'
                    f'<div class="d">{testo}</div><div class="l">{nota}</div></div>')

    log = "".join(
        f'<tr><td class="d">{e(x["eseguito_il"][:16].replace("T", " "))}</td>'
        f'<td><code>{e(x["job"])}</code></td><td>{e(x["esito"])}</td>'
        f'<td class="n">{e(x["righe"] if x["righe"] is not None else "—")}</td></tr>'
        for x in d["log"])

    aperte = "".join(riga_aperta(x) for x in sorted(d["aperte"], key=lambda z: z["data_partita"]))
    chiuse = "".join(riga_chiusa(x) for x in sorted(d["chiuse"], key=lambda z: z["data_partita"],
                                                    reverse=True))
    bank = float(d["meta"].get("bankroll0", 1000))

    return f"""<title>Pengwin — Registro dell'esperimento</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>{CSS}</style>
<div class="wrap">

<header>
  <div class="head-row">
    <div>
      <div class="eyebrow">Bankroll simulato · Top 5 europee</div>
      <h1>Registro dell'esperimento</h1>
      <p class="sub">Modello Dixon-Coles su obiettivo misto 35% gol / 65% xG. Ogni previsione è
      registrata prima dell'evento e non è più modificabile.</p>
    </div>
    <div class="stamp">aggiornato<br>{d["aggiornato"]}</div>
  </div>
</header>

<div class="verdetto">
  <div class="eyebrow">Come leggere questa pagina</div>
  <p>{verdetto}</p>
</div>

<section>
  <div class="shead"><h2>Le due misure, in ordine di peso</h2></div>
  <div class="metriche">
    <div class="met primaria">
      <div class="k">CLV medio</div>
      <div class="val {'neg' if clv_neg else 'pos'}">{pct(d["clv_medio"])}</div>
      <div class="note">positivo su {quota_pos} giocate · criterio primario</div>
    </div>
    <div class="met">
      <div class="k">ROI</div>
      <div class="val {'pos' if (d["roi"] or 0) > 0 else 'neg'}">{pct(d["roi"])}</div>
      <div class="note">su {len(d["chiuse"])} concluse · campione troppo piccolo</div>
    </div>
    <div class="met">
      <div class="k">P&amp;L</div>
      <div class="val {'pos' if d["pnl"] > 0 else 'neg'}">{num(d["pnl"], 2, True)} €</div>
      <div class="note">volume {num(d["stake"])} € su bankroll {num(bank, 0)} €</div>
    </div>
    <div class="met">
      <div class="k">In corso</div>
      <div class="val">{len(d["aperte"])}</div>
      <div class="note">{num(d["esposto"])} € esposti</div>
    </div>
  </div>
</section>

<section>
  <div class="shead"><h2>CLV, giocata per giocata</h2></div>
  <p class="nota">Ogni tratto è una giocata conclusa: quanto la quota presa era migliore (verde,
  a destra dello zero) o peggiore (rossa) della quota di chiusura sullo stesso esito. Non dipende
  da come è finita la partita, e per questo è informativo molto prima del ROI.</p>
  <div class="pannello">{grafico_clv(d)}</div>
</section>

<section>
  <div class="shead"><h2>Andamento contro la banda pre-registrata</h2></div>
  <p class="nota">La banda è stata calcolata <em>prima</em> di iniziare, simulando l'esperimento
  sotto l'ipotesi di assenza di vantaggio: è il metro di paragone, e non viene mai ricalcolata.
  La linea continua è l'andamento reale. Restare dentro la banda non prova nulla — uscirne in alto,
  e restarci, sarebbe la prima cosa interessante.</p>
  <div class="pannello">
    {grafico_banda(d)}
    <div class="legenda">
      <span><i style="background:var(--ink)"></i>andamento reale</span>
      <span><i style="background:var(--accent)"></i>mediana attesa senza vantaggio</span>
      <span><i style="background:var(--band-in)"></i>50% centrale</span>
      <span><i style="background:var(--band-out)"></i>95% centrale</span>
      <span>probabilità di chiudere in utile senza vantaggio: {num(d["band"]["p_profit"] if d["band"] else 0, 1)}%</span>
    </div>
  </div>
</section>

<section>
  <div class="shead"><h2>Le giocate</h2></div>
  <p class="nota">I filtri cambiano solo che cosa è in tabella. CLV e ROI qui sopra restano quelli
  dell'esperimento intero: su {len(d["chiuse"])} giocate concluse, un CLV per singolo campionato
  poggerebbe su una manciata di righe e non direbbe nulla.</p>
  {barra_filtri(d)}
</section>

<section id="sez-aperte">
  <div class="shead"><h2>Giocate in corso</h2><span class="stamp conta">{len(d["aperte"])} in corso</span></div>
  <div class="tabellone"><table>
    <thead><tr><th>Data</th><th>Lega</th><th>Partita</th><th>Giocata</th>
      <th class="n">p</th><th class="n">Quota</th><th class="n">Edge</th><th class="n">Punta €</th></tr></thead>
    <tbody>{aperte or '<tr><td colspan="8" class="vuoto">nessuna giocata aperta</td></tr>'}
      <tr class="nessuna" hidden><td colspan="8" class="vuoto">nessuna giocata in corso con questi filtri</td></tr></tbody>
  </table></div>
</section>

<section id="sez-chiuse">
  <div class="shead"><h2>Giocate concluse</h2><span class="stamp conta">{len(d["chiuse"])} concluse</span></div>
  <div class="tabellone"><table>
    <thead><tr><th>Data</th><th>Lega</th><th>Partita</th><th>Giocata</th>
      <th class="n">Presa</th><th class="n">Chiusura</th><th class="n">CLV</th><th class="n">Finita</th>
      <th>Esito</th><th class="n">P&amp;L €</th></tr></thead>
    <tbody>{chiuse or '<tr><td colspan="10" class="vuoto">nessuna giocata conclusa</td></tr>'}
      <tr class="nessuna" hidden><td colspan="10" class="vuoto">nessuna giocata conclusa con questi filtri</td></tr></tbody>
  </table></div>
</section>

<section>
  <div class="shead"><h2>Stato dei dati</h2></div>
  <p class="nota">Ultima partita presente in archivio per campionato. Un campionato fermo da più di
  dieci giorni viene evidenziato: il modello lo starebbe prevedendo senza aver visto le ultime
  giornate.</p>
  <div class="stato">{fresche}</div>
  <div class="tabellone" style="margin-top:6px"><table>
    <thead><tr><th>Quando</th><th>Procedura</th><th>Esito</th><th class="n">Righe</th></tr></thead>
    <tbody>{log}</tbody>
  </table></div>
</section>

<footer>
  <p>Esperimento a bankroll simulato: nessuna somma reale è impegnata. Le regole (banda di edge
  2–10%, Kelly 1/4, cap 1% per giocata, massimo 25 giocate a turno) sono fissate nel runbook e non
  vengono modificate a esperimento in corso.</p>
  <p>Dati: football-data.co.uk per risultati e quote, Understat per gli xG. Il benchmark è la quota
  massima di chiusura; Pinnacle non è più pubblicato dal 2026/27.</p>
</footer>

</div>
<script>{SCRIPT}</script>"""


def main(argv=None) -> int:
    out = Path(argv[0]) if argv else Path("dashboard.html")
    d = raccogli()
    out.write_text(costruisci(d), encoding="utf-8")
    print(f"scritta {out} ({out.stat().st_size/1024:.1f} KB) — "
          f"{len(d['aperte'])} aperte, {len(d['chiuse'])} concluse, CLV {pct(d['clv_medio'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
