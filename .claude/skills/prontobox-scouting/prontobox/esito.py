"""Stadio 4 - esito e voto (§Stadio4). Combina l'esito dello Stadio 1, la
scorecard (Stadio 2) e il gate economico (Stadio 3) in un'unica decisione,
seguendo esattamente la tabella della spec."""

from __future__ import annotations

from typing import Any, Optional

from .campo import confidenza_di, valore_di


def _punteggio_economico(yoc_scenario_base: Optional[float], hurdle: float) -> float:
    if yoc_scenario_base is None:
        return 50.0
    delta = yoc_scenario_base - hurdle
    if delta <= -0.05:
        return 0.0
    if delta >= 0.05:
        return 100.0
    return 50.0 + (delta / 0.05) * 50.0


def _qualche_scenario_supera_hurdle(economico: dict[str, Any], hurdle: float) -> bool:
    base = economico.get("prezzo_base")
    if not base:
        return False
    for valori in (base.get("yoc") or {}).values():
        for v in (valori or {}).values():
            if v is not None and v >= hurdle:
                return True
    return False


def _penalita_sopra_max(superficie: Optional[float], config: dict[str, Any]) -> float:
    filtri_cfg = config["filtri"]
    max_mq = filtri_cfg["superficie_max_mq"]
    if not superficie or superficie <= max_mq:
        return 0.0
    eccedenza = superficie - max_mq
    unita = eccedenza / 1000.0
    import math

    punti = math.ceil(unita) * filtri_cfg["penalita_sopra_max_punti_per_1000mq"]
    return min(punti, filtri_cfg["penalita_sopra_max_massima"])


def calcola_esito_e_voto(
    *,
    stadio1: dict[str, Any],
    rec: dict[str, Any],
    scorecard: Optional[dict[str, Any]],
    economico: Optional[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    voto_cfg = config["voto"]

    if stadio1["esito"] == "SCARTA":
        return {"esito": "SCARTA", "motivo": stadio1["motivo"], "dettaglio": stadio1.get("dettaglio"), "voto": None, "classe": None}
    if stadio1["esito"] == "MONITORA":
        return {"esito": "MONITORA", "motivo": stadio1["motivo"], "dettaglio": stadio1.get("dettaglio"), "voto": None, "classe": None}

    assert scorecard is not None and economico is not None

    superficie = valore_di(rec.get("superficie_mq"))
    conf_superficie = confidenza_di(rec.get("superficie_mq"))
    altezza = valore_di(rec.get("altezza_m"))
    prezzo = valore_di(rec.get("prezzo_eur"))
    flag = set(stadio1.get("flag") or [])

    dato_decisivo_ignoto = (
        superficie is None
        or conf_superficie == "bassa"
        or altezza is None
        or prezzo is None
        or "TIPOLOGIA_DA_VERIFICARE" in flag
        or "COMUNE_DA_VERIFICARE" in flag
    )
    range_troppo_ampio = scorecard["classe"] is None and not scorecard["sotto_soglia_minima"]

    if dato_decisivo_ignoto or range_troppo_ampio:
        motivo_parti = []
        if superficie is None or conf_superficie == "bassa":
            motivo_parti.append("superficie")
        if altezza is None:
            motivo_parti.append("altezza")
        if prezzo is None:
            motivo_parti.append("prezzo")
        if "TIPOLOGIA_DA_VERIFICARE" in flag:
            motivo_parti.append("destinazione/tipologia")
        if range_troppo_ampio:
            motivo_parti.append(f"intervallo di score troppo ampio ({scorecard['ampiezza']:.0f} punti)")
        classe_provvisoria = scorecard["classe"]
        hurdle = _hurdle_per_classe(classe_provvisoria, config)
        yoc_base = economico.get("yoc_scenario_base")
        voto = _calcola_voto(scorecard, yoc_base, hurdle, superficie, config)
        return {
            "esito": "DA APPROFONDIRE",
            "motivo": "DATI_INSUFFICIENTI",
            "dettaglio": "dato decisivo mancante: " + ", ".join(motivo_parti),
            "voto": voto,
            "classe": classe_provvisoria,
        }

    if scorecard["sotto_soglia_minima"]:
        return {
            "esito": "SCARTA",
            "motivo": "SCORE_INSUFFICIENTE",
            "dettaglio": f"score atteso {scorecard['atteso']:.0f}, sotto la soglia minima di classe",
            "voto": None,
            "classe": None,
        }

    classe = scorecard["classe"]
    hurdle = _hurdle_per_classe(classe, config)
    yoc_base = economico.get("yoc_scenario_base")
    voto = _calcola_voto(scorecard, yoc_base, hurdle, superficie, config)

    if yoc_base is not None and yoc_base >= hurdle:
        return {"esito": "PROMUOVI", "motivo": None, "dettaglio": None, "voto": voto, "classe": classe}

    if _qualche_scenario_supera_hurdle(economico, hurdle):
        return {
            "esito": "MONITORA",
            "motivo": "PREZZO_ALTO",
            "dettaglio": f"YoC scenario base {yoc_base:.1%} sotto l'hurdle {hurdle:.1%}, ma un altro scenario lo supera" if yoc_base is not None else "prezzo da rivedere",
            "voto": voto,
            "classe": classe,
        }

    return {
        "esito": "SCARTA",
        "motivo": "YOC_INSUFFICIENTE",
        "dettaglio": f"YoC sotto l'hurdle {hurdle:.1%} in tutti gli scenari",
        "voto": None,
        "classe": None,
    }


def _hurdle_per_classe(classe: Optional[str], config: dict[str, Any]) -> float:
    eco = config["economia"]
    return {"A": eco["hurdle_classe_a"], "B": eco["hurdle_classe_b"], "C": eco["hurdle_classe_c"]}.get(classe, eco["hurdle_classe_c"])


def _calcola_voto(scorecard: dict[str, Any], yoc_base: Optional[float], hurdle: float, superficie: Optional[float], config: dict[str, Any]) -> int:
    voto_cfg = config["voto"]
    punteggio_eco = _punteggio_economico(yoc_base, hurdle)
    voto = (
        voto_cfg["peso_qualita"] * scorecard["atteso"]
        + voto_cfg["peso_economico"] * punteggio_eco
        - voto_cfg["coeff_incertezza"] * scorecard["ampiezza"]
        - _penalita_sopra_max(superficie, config)
    )
    return round(max(0.0, min(100.0, voto)))
