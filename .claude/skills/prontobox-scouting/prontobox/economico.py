"""Stadio 3 - gate economico (§Stadio3). Tutti i calcoli sono deterministici
una volta noti superficie, altezza (o le loro assenze) e il punteggio del
percorso autorizzativo (che arriva dalla scorecard, §Stadio2/§Stadio4).

ASSUNZIONE DICHIARATA (vedi anche README): la spec da' la formula di
costo_all_in ma non una formula per costo_sanatoria (dice solo "oblazione +
tecnici, in base al percorso autorizzativo"). La si stima qui come una quota
di mesi_al_go_live moltiplicata per una piccola percentuale del prezzo,
proporzionale alla gravita' delle irregolarita' (punteggio percorso basso =
piu' mesi = piu' costo). E' una stima grezza, dichiarata come tale in
`assunzioni` nel blocco economico di ogni opportunita': va sostituita con un
preventivo reale in fase di due diligence.
"""

from __future__ import annotations

from typing import Any, Optional

CATEGORIE_ALTE = {"capannone", "opificio"}


def _interp(a: float, b: float, frazione: float) -> float:
    frazione = max(0.0, min(1.0, frazione))
    return a + (b - a) * frazione


def _fattore_soppalco(altezza_m: Optional[float], config: dict[str, Any]) -> float:
    eco = config["economia"]
    if altezza_m is None:
        return 0.0
    if altezza_m < 4.5:
        return eco["fattore_soppalco_basso_h"]
    if altezza_m < 5.5:
        return (eco["fattore_soppalco_medio_min"] + eco["fattore_soppalco_medio_max"]) / 2
    return (eco["fattore_soppalco_alto_min"] + eco["fattore_soppalco_alto_max"]) / 2


def _fattore_soppalco_max_categoria(categoria: Optional[str], config: dict[str, Any]) -> float:
    eco = config["economia"]
    if categoria in CATEGORIE_ALTE:
        return eco["fattore_soppalco_alto_max"]
    return eco["fattore_soppalco_medio_max"]


def calcola_sln(
    superficie_lorda: Optional[float],
    altezza_m: Optional[float],
    categoria: Optional[str],
    config: dict[str, Any],
) -> dict[str, Optional[float]]:
    """SLN = superficie_lorda × efficienza × (1 + fattore_soppalco).
    Ritorna {min, atteso, max}. Se la superficie e' ignota, tutto None
    (non si inventa un valore)."""
    if not superficie_lorda:
        return {"min": None, "atteso": None, "max": None, "altezza_nota": altezza_m is not None}

    eco = config["economia"]
    eff_min, eff_def, eff_max = eco["efficienza_sln_min"], eco["efficienza_sln_default"], eco["efficienza_sln_max"]

    if altezza_m is not None:
        fattore = _fattore_soppalco(altezza_m, config)
        return {
            "min": round(superficie_lorda * eff_min * (1 + fattore), 1),
            "atteso": round(superficie_lorda * eff_def * (1 + fattore), 1),
            "max": round(superficie_lorda * eff_max * (1 + fattore), 1),
            "altezza_nota": True,
        }

    fattore_max = _fattore_soppalco_max_categoria(categoria, config)
    return {
        "min": round(superficie_lorda * eff_min * 1.0, 1),
        "atteso": round(superficie_lorda * eff_def * 1.0, 1),  # prudente: senza soppalco finche' non verificato
        "max": round(superficie_lorda * eff_max * (1 + fattore_max), 1),
        "altezza_nota": False,
    }


def calcola_capex(
    superficie_lorda: Optional[float],
    altezza_m: Optional[float],
    *,
    anno_costruzione: Optional[int],
    indizi_amianto: bool,
    config: dict[str, Any],
) -> dict[str, Any]:
    if not superficie_lorda:
        return {"min": None, "atteso": None, "max": None}

    eco = config["economia"]
    applica_soppalco = altezza_m is not None and altezza_m >= 4.5
    applica_sismico = anno_costruzione is not None and anno_costruzione < 2008

    def somma(chiave_min: str, chiave_max: str, applica: bool = True) -> tuple[float, float]:
        if not applica:
            return 0.0, 0.0
        return eco[chiave_min], eco[chiave_max]

    base_min, base_max = somma("capex_baseline_min_eur_mq", "capex_baseline_max_eur_mq")
    sop_min, sop_max = somma("capex_soppalco_min_eur_mq", "capex_soppalco_max_eur_mq", applica_soppalco)
    sis_min, sis_max = somma("capex_sismico_min_eur_mq", "capex_sismico_max_eur_mq", applica_sismico)
    strip_min, strip_max = somma("capex_strip_out_min_eur_mq", "capex_strip_out_max_eur_mq")
    ami_min, ami_max = somma("capex_amianto_min_eur_mq", "capex_amianto_max_eur_mq", indizi_amianto)
    rec_min, rec_max = eco["capex_recuperi_min_eur_mq"], eco["capex_recuperi_max_eur_mq"]

    per_mq_min = base_min + sop_min + sis_min + strip_min + ami_min - rec_max
    per_mq_max = base_max + sop_max + sis_max + strip_max + ami_max - rec_min
    per_mq_atteso = (per_mq_min + per_mq_max) / 2

    return {
        "min": round(superficie_lorda * per_mq_min, 0),
        "atteso": round(superficie_lorda * per_mq_atteso, 0),
        "max": round(superficie_lorda * per_mq_max, 0),
        "eur_mq_atteso": round(per_mq_atteso, 0),
        "applica_soppalco": applica_soppalco,
        "applica_sismico": applica_sismico,
        "applica_amianto": indizi_amianto,
    }


def mesi_al_go_live(punteggio_percorso_autorizzativo: Optional[float], config: dict[str, Any]) -> float:
    eco = config["economia"]
    if punteggio_percorso_autorizzativo is None:
        # ignoto: prudenza, si assume la meta' del range come stima grezza
        frazione = 0.5
    else:
        frazione = (12 - punteggio_percorso_autorizzativo) / (12 - 2)
    return round(_interp(eco["mesi_al_go_live_min"], eco["mesi_al_go_live_max"], frazione), 1)


def calcola_gate_economico(
    *,
    superficie_lorda: Optional[float],
    altezza_m: Optional[float],
    categoria: Optional[str],
    prezzo_eur: Optional[float],
    offerta_minima_eur: Optional[float],
    anno_costruzione: Optional[int],
    indizi_amianto: bool,
    punteggio_percorso_autorizzativo: Optional[float],
    classe: Optional[str],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Ritorna il blocco 'economico' cosi' come richiesto dallo schema §5.1."""
    eco = config["economia"]
    sln = calcola_sln(superficie_lorda, altezza_m, categoria, config)
    capex = calcola_capex(superficie_lorda, altezza_m, anno_costruzione=anno_costruzione, indizi_amianto=indizi_amianto, config=config)

    hurdle_per_classe = {
        "A": eco["hurdle_classe_a"],
        "B": eco["hurdle_classe_b"],
        "C": eco["hurdle_classe_c"],
    }
    hurdle = hurdle_per_classe.get(classe, eco["hurdle_classe_c"])

    mesi = mesi_al_go_live(punteggio_percorso_autorizzativo, config)

    costo_per_prezzo: dict[str, Any] = {}
    for etichetta, prezzo_riferimento in (("prezzo_base", prezzo_eur), ("offerta_minima", offerta_minima_eur)):
        if prezzo_riferimento is None or capex["atteso"] is None:
            costo_per_prezzo[etichetta] = None
            continue
        oneri_acquisto = prezzo_riferimento * eco["oneri_acquisto_pct"] / 100
        costo_sanatoria = prezzo_riferimento * 0.02 * (mesi / eco["mesi_al_go_live_max"]) if eco["mesi_al_go_live_max"] else 0.0
        costo_all_in = prezzo_riferimento + oneri_acquisto + costo_sanatoria + capex["atteso"]

        yoc_per_scenario: dict[str, Any] = {}
        for tariffa in eco["scenari_tariffa_eur_mq_anno"]:
            if sln["atteso"] is None:
                yoc_per_scenario[f"tariffa_{tariffa}"] = {"senza_soppalco": None, "con_soppalco": None}
                continue
            if sln.get("altezza_nota"):
                sln_senza = sln["atteso"]
                sln_con = sln["atteso"]
            else:
                sln_senza = superficie_lorda * eco["efficienza_sln_default"] * 1.0
                fattore_max = _fattore_soppalco_max_categoria(categoria, config)
                sln_con = superficie_lorda * eco["efficienza_sln_default"] * (1 + fattore_max)
            ebitda_senza = sln_senza * tariffa * eco["occupancy_regime"] * eco["margine_ebitda"]
            ebitda_con = sln_con * tariffa * eco["occupancy_regime"] * eco["margine_ebitda"]
            yoc_per_scenario[f"tariffa_{tariffa}"] = {
                "senza_soppalco": round(ebitda_senza / costo_all_in, 4) if costo_all_in else None,
                "con_soppalco": round(ebitda_con / costo_all_in, 4) if costo_all_in else None,
            }

        costo_per_prezzo[etichetta] = {
            "oneri_acquisto_eur": round(oneri_acquisto, 0),
            "costo_sanatoria_eur": round(costo_sanatoria, 0),
            "costo_all_in_eur": round(costo_all_in, 0),
            "costo_per_mq_sln_eur": round(costo_all_in / sln["atteso"], 0) if sln["atteso"] else None,
            "yoc": yoc_per_scenario,
        }

    base = costo_per_prezzo.get("prezzo_base")
    scenario_base_tariffa = f"tariffa_{eco['scenari_tariffa_eur_mq_anno'][0]}"
    if base and base["yoc"].get(scenario_base_tariffa):
        yoc_base_dict = base["yoc"][scenario_base_tariffa]
        yoc_scenario_base = yoc_base_dict["senza_soppalco"] if not sln.get("altezza_nota") else yoc_base_dict["con_soppalco"]
    else:
        yoc_scenario_base = None

    ebitda_annuo_base = None
    if base and sln["atteso"] is not None:
        tariffa_base = eco["scenari_tariffa_eur_mq_anno"][0]
        ebitda_annuo_base = sln["atteso"] * tariffa_base * eco["occupancy_regime"] * eco["margine_ebitda"]

    sconto_da_chiedere = None
    if ebitda_annuo_base is not None and base is not None:
        margine_mensile_mancato = ebitda_annuo_base * eco["margine_mensile_mancato_frazione_ebitda_annuo"]
        oneri_finanziari = base["costo_all_in_eur"] * eco["oneri_finanziari_mensili_pct"] / 100
        sconto_da_chiedere = round((margine_mensile_mancato + oneri_finanziari) * mesi, 0)

    return {
        "sln_mq": sln,
        "capex_eur": capex,
        "prezzo_base": costo_per_prezzo.get("prezzo_base"),
        "offerta_minima": costo_per_prezzo.get("offerta_minima"),
        "hurdle": hurdle,
        "yoc_scenario_base": yoc_scenario_base,
        "mesi_al_go_live": mesi,
        "sconto_da_chiedere_eur": sconto_da_chiedere,
        "assunzioni": [
            "tariffa_mq_anno di riferimento (261 €/m²/anno) è una media nazionale di settore: sostituire con il ricavo reale Prontobox",
            "costo_sanatoria è una stima approssimata proporzionale ai mesi_al_go_live, non un preventivo tecnico",
        ],
    }
