"""Il tipo "Campo": ogni dato derivato porta {valore, fonte, confidenza}.

Regola non negoziabile della spec (§3.0): "Vietato produrre un valore senza
fonte." Ogni funzione di normalizzazione/estrazione restituisce un Campo
(un semplice dict, per restare serializzabile in JSON senza convertitori)
invece di un valore nudo, cosi' il resto della pipeline puo' sempre sapere
quanto fidarsi di un dato.
"""

from __future__ import annotations

from typing import Any, Optional

FONTI = {
    "json",
    "regex_testo",
    "raw_detail",
    "fetch_pagina",
    "fetch_perizia",
    "geocoding",
    "stima",
}
CONFIDENZE = {"alta", "media", "bassa"}


def campo(valore: Any, fonte: str, confidenza: str = "media", **extra: Any) -> dict[str, Any]:
    """Costruisce un Campo. `extra` per chiavi aggiuntive (es. testo=snippet)."""
    d: dict[str, Any] = {"valore": valore, "fonte": fonte, "confidenza": confidenza}
    d.update(extra)
    return d


def campo_ignoto(fonte: str = "stima", motivo: Optional[str] = None) -> dict[str, Any]:
    d = campo(None, fonte, "bassa")
    if motivo:
        d["motivo"] = motivo
    return d


def valore_di(c: Optional[dict[str, Any]]) -> Any:
    """Estrae .valore da un Campo, tollerando None o un valore nudo (per
    compatibilita' con codice che passa gia' un valore semplice)."""
    if c is None:
        return None
    if isinstance(c, dict) and "valore" in c:
        return c["valore"]
    return c


def confidenza_di(c: Optional[dict[str, Any]]) -> Optional[str]:
    if isinstance(c, dict):
        return c.get("confidenza")
    return None


def e_noto(c: Optional[dict[str, Any]]) -> bool:
    return valore_di(c) is not None
