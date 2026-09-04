"""
Client Supabase via API REST (PostgREST).

Motivo: l'ambiente cloud di esecuzione instrada solo traffico HTTP/HTTPS
attraverso un proxy; il protocollo Postgres nativo sulla porta 5432
(psycopg2) non passa. Tutto qui sotto usa https://<progetto>.supabase.co/rest/v1.

Configurazione via variabili d'ambiente:
  SUPABASE_URL   es. https://cshlvcfahevvdcdjlola.supabase.co
  SUPABASE_KEY   publishable key (sb_publishable_...) o secret key (sb_secret_...)
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Iterable, Sequence
from urllib.parse import urlencode

import requests

DEFAULT_URL = "https://cshlvcfahevvdcdjlola.supabase.co"
BATCH = 500          # righe per richiesta
TIMEOUT = 60
RETRY = 3


def _uniforma(righe: Sequence[dict]) -> list[dict]:
    """PostgREST (PGRST102) esige che tutte le righe di un batch abbiano le
    stesse chiavi. Le fonti omettono le colonne assenti (quote mancanti per
    certe partite), quindi si completa con l'unione delle chiavi e None."""
    chiavi: list[str] = []
    viste: set[str] = set()
    for r in righe:
        for k in r:
            if k not in viste:
                viste.add(k)
                chiavi.append(k)
    return [{k: r.get(k) for k in chiavi} for r in righe]


class SupabaseError(RuntimeError):
    pass


class Supabase:
    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = (url or os.getenv("SUPABASE_URL") or DEFAULT_URL).rstrip("/")
        self.key = key or os.getenv("SUPABASE_KEY")
        if not self.key:
            raise SupabaseError(
                "Chiave mancante: esporta SUPABASE_KEY "
                "(publishable sb_publishable_... o secret sb_secret_...)."
            )
        self.rest = f"{self.url}/rest/v1"
        self.s = requests.Session()
        self.s.headers.update({
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # ---------------------------------------------------------- basso livello
    def _req(self, method: str, path: str, **kw) -> requests.Response:
        url = f"{self.rest}/{path.lstrip('/')}"
        last = None
        for tentativo in range(RETRY):
            try:
                r = self.s.request(method, url, timeout=TIMEOUT, **kw)
            except requests.RequestException as e:
                last = e
                time.sleep(1.5 * (tentativo + 1))
                continue
            if r.status_code < 400:
                return r
            # 5xx / 429 -> riprova; 4xx -> errore immediato
            if r.status_code in (429,) or r.status_code >= 500:
                last = SupabaseError(f"HTTP {r.status_code}: {r.text[:300]}")
                time.sleep(1.5 * (tentativo + 1))
                continue
            raise SupabaseError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:500]}")
        raise SupabaseError(f"{method} {path} fallita dopo {RETRY} tentativi: {last}")

    # ------------------------------------------------------------- diagnostica
    def ping(self) -> tuple[bool, str]:
        """True se l'endpoint risponde e la chiave e' valida."""
        try:
            r = self.s.get(f"{self.rest}/squadre",
                           params={"select": "id", "limit": 1}, timeout=20)
        except requests.RequestException as e:
            return False, f"rete: {e}"
        if r.status_code == 200:
            return True, "ok — schema presente"
        if r.status_code == 404 and "PGRST205" in r.text:
            return False, "chiave valida ma schema assente: eseguire sql/schema.sql"
        if r.status_code in (401, 403):
            return False, f"autenticazione rifiutata: {r.text[:200]}"
        return False, f"HTTP {r.status_code}: {r.text[:200]}"

    # -------------------------------------------------------------- scrittura
    def upsert(self, tabella: str, righe: Sequence[dict], *,
               on_conflict: str | None = None, ignora_duplicati: bool = False) -> int:
        """Inserisce/aggiorna a blocchi. Ritorna il numero di righe inviate."""
        righe = _uniforma([r for r in righe if r])
        if not righe:
            return 0
        pref = "resolution=ignore-duplicates" if ignora_duplicati else "resolution=merge-duplicates"
        headers = {"Prefer": f"{pref},return=minimal"}
        params = {"on_conflict": on_conflict} if on_conflict else None
        inviate = 0
        for i in range(0, len(righe), BATCH):
            blocco = righe[i:i + BATCH]
            self._req("POST", tabella, params=params, headers=headers,
                      data=json.dumps(blocco, default=str))
            inviate += len(blocco)
        return inviate

    def insert(self, tabella: str, righe: Sequence[dict], *, ritorna: bool = False):
        righe = _uniforma([r for r in righe if r])
        if not righe:
            return [] if ritorna else 0
        headers = {"Prefer": "return=representation" if ritorna else "return=minimal"}
        out: list[dict] = []
        for i in range(0, len(righe), BATCH):
            blocco = righe[i:i + BATCH]
            r = self._req("POST", tabella, headers=headers,
                          data=json.dumps(blocco, default=str))
            if ritorna:
                out.extend(r.json())
        return out if ritorna else len(righe)

    def update(self, tabella: str, filtro: dict[str, str], valori: dict) -> list[dict]:
        headers = {"Prefer": "return=representation"}
        r = self._req("PATCH", tabella, params=filtro, headers=headers,
                      data=json.dumps(valori, default=str))
        return r.json() if r.text.strip() else []

    def delete(self, tabella: str, filtro: dict[str, str]) -> None:
        self._req("DELETE", tabella, params=filtro,
                  headers={"Prefer": "return=minimal"})

    # --------------------------------------------------------------- lettura
    def select(self, tabella: str, *, colonne: str = "*", filtri: dict | None = None,
               ordina: str | None = None, limite: int | None = None) -> list[dict]:
        """Legge tutte le righe paginando (PostgREST limita a 1000 per default)."""
        base: dict[str, Any] = {"select": colonne}
        if filtri:
            base.update(filtri)
        if ordina:
            base["order"] = ordina
        risultati: list[dict] = []
        passo = 1000
        offset = 0
        while True:
            p = dict(base)
            n = passo if limite is None else min(passo, limite - len(risultati))
            if n <= 0:
                break
            p["limit"] = n
            p["offset"] = offset
            r = self._req("GET", tabella, params=p)
            blocco = r.json()
            risultati.extend(blocco)
            if len(blocco) < n:
                break
            offset += n
        return risultati

    def conta(self, tabella: str, filtri: dict | None = None) -> int:
        p = dict(filtri or {})
        p["select"] = "id"
        r = self._req("GET", tabella, params=p,
                      headers={"Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"})
        cr = r.headers.get("content-range", "*/0")
        return int(cr.split("/")[-1]) if cr.split("/")[-1] != "*" else 0

    # ------------------------------------------------------------------ log
    def log(self, job: str, esito: str, righe: int | None = None, dettaglio: str = "") -> None:
        try:
            self.insert("log_esecuzioni",
                        [{"job": job, "esito": esito, "righe": righe,
                          "dettaglio": dettaglio[:2000]}])
        except SupabaseError:
            pass  # il log non deve mai far fallire il job


def client() -> Supabase:
    return Supabase()


if __name__ == "__main__":
    db = Supabase()
    ok, msg = db.ping()
    print(("OK  " if ok else "KO  ") + msg)
