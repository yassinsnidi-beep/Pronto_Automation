"""Funzioni di servizio: date, file JSON atomici, testo, formattazione italiana."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:  # su Windows serve il pacchetto tzdata
    from zoneinfo import ZoneInfo

    TZ_ROMA = ZoneInfo("Europe/Rome")
except Exception:  # pragma: no cover - ripiego senza database dei fusi
    TZ_ROMA = None


def _offset_roma(d: datetime) -> timezone:
    """Ripiego senza tzdata: ora legale approssimata (ultima domenica di marzo/ottobre)."""
    anno = d.year

    def ultima_domenica(mese: int) -> datetime:
        giorno = datetime(anno, mese, 31)
        return giorno - timedelta(days=(giorno.weekday() + 1) % 7)

    inizio, fine = ultima_domenica(3), ultima_domenica(10)
    return timezone(timedelta(hours=2 if inizio <= d.replace(tzinfo=None) < fine else 1))


def ora_roma() -> datetime:
    adesso = datetime.now(timezone.utc)
    if TZ_ROMA is not None:
        return adesso.astimezone(TZ_ROMA)
    return adesso.astimezone(_offset_roma(adesso))


def a_roma(d: datetime) -> datetime:
    """Rende consapevole del fuso (Europe/Rome) una data ingenua, o la converte."""
    if d.tzinfo is None:
        if TZ_ROMA is not None:
            return d.replace(tzinfo=TZ_ROMA)
        return d.replace(tzinfo=_offset_roma(d))
    if TZ_ROMA is not None:
        return d.astimezone(TZ_ROMA)
    return d.astimezone(_offset_roma(d.replace(tzinfo=None)))


def oggi_roma() -> date:
    return ora_roma().date()


def id_run(d: date) -> str:
    return f"{d.year:04d}_{d.month:02d}_{d.day:02d}"


# --------------------------------------------------------------------------- file


def leggi_json(percorso: Path) -> Any:
    with open(percorso, "r", encoding="utf-8") as f:
        return json.load(f)


def scrivi_json_atomico(dati: Any, percorso: Path, indent: Optional[int] = 2) -> None:
    percorso = Path(percorso)
    percorso.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=percorso.name + ".", suffix=".tmp", dir=str(percorso.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(dati, f, ensure_ascii=False, indent=indent, default=_json_default)
            f.flush()
            os.fsync(f.fileno())
        _replace_con_retry(tmp, percorso)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _replace_con_retry(sorgente: str, destinazione: Path, tentativi: int = 8, attesa_iniziale_s: float = 0.05) -> None:
    """os.replace() con retry ed backoff: su Windows un antivirus o
    l'indicizzazione possono tenere il file di destinazione bloccato per
    pochi millisecondi subito dopo la scrittura, facendo fallire la
    rinomina atomica con PermissionError (WinError 5) anche se non c'e'
    nessun vero conflitto applicativo. Questo file viene riscritto molto
    spesso (ad ogni fase/aggiornamento di stato), quindi la finestra di
    rischio si presenta spesso: qui si ritenta con backoff breve invece di
    far fallire l'intera run per un blocco transitorio."""
    import time

    attesa = attesa_iniziale_s
    for tentativo in range(1, tentativi + 1):
        try:
            os.replace(sorgente, destinazione)
            return
        except PermissionError:
            if tentativo == tentativi:
                raise
            time.sleep(attesa)
            attesa = min(attesa * 2, 1.0)


def _json_default(o: Any) -> Any:
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, set):
        return sorted(o)
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"Tipo non serializzabile: {type(o)}")


def sha1_file(percorso: Path) -> str:
    h = hashlib.sha1()
    with open(percorso, "rb") as f:
        for blocco in iter(lambda: f.read(65536), b""):
            h.update(blocco)
    return h.hexdigest()


# --------------------------------------------------------------------------- testo


def senza_accenti(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_chiave(s: Optional[str]) -> str:
    """Minuscole, senza accenti né punteggiatura, spazi singoli."""
    if not s:
        return ""
    s = senza_accenti(str(s)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_testo(s: Optional[str]) -> str:
    """Per confronti di citazioni: minuscole, spazi compressi, apostrofi uniformi."""
    if not s:
        return ""
    s = str(s).replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def pulisci(s: Any) -> Optional[str]:
    if s is None:
        return None
    s = str(s).strip()
    if not s or s.lower() in {"none", "null", "nan", "n.d.", "nd", "-"}:
        return None
    return re.sub(r"\s+", " ", s)


def titolo_comune(s: str) -> str:
    """'crespina lorenzana' -> 'Crespina Lorenzana', rispettando apostrofi."""
    parole = []
    minuscole = {"di", "del", "della", "delle", "dei", "degli", "da", "in", "sul", "sull", "e", "d", "al", "alla"}
    for i, p in enumerate(re.split(r"(\s+|'|-)", s.strip().lower())):
        if not p or p.isspace() or p in {"'", "-"}:
            parole.append(p)
            continue
        parole.append(p if (i > 0 and p in minuscole) else p[:1].upper() + p[1:])
    return "".join(parole)


# --------------------------------------------------------------------------- formattazione italiana


def fmt_num(v: Optional[float], decimali: int = 0) -> str:
    if v is None:
        return "n.d."
    testo = f"{v:,.{decimali}f}"
    return testo.replace(",", "§").replace(".", ",").replace("§", ".")


def fmt_eur(v: Optional[float]) -> str:
    return "n.d." if v is None else f"{fmt_num(v)} €"


def fmt_mq(v: Optional[float]) -> str:
    return "n.d." if v is None else f"{fmt_num(v)} m²"


def fmt_pct(v: Optional[float], decimali: int = 1) -> str:
    return "n.d." if v is None else f"{fmt_num(v * 100, decimali)}%"


def fmt_data(v: Any) -> str:
    if v is None:
        return "n.d."
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v)
        except ValueError:
            return v
    return v.strftime("%d/%m/%Y")
