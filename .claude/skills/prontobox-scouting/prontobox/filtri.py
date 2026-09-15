"""Stadio 1 - hard filter (§Stadio1). Si applica solo su dati certi: un
valore mancante o a bassa confidenza non scarta mai, al massimo produce un
flag e una voce in dati_mancanti (esito finale DA APPROFONDIRE, deciso allo
Stadio 4)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from . import economico, geografia, testo
from .campo import confidenza_di, valore_di

MOTIVI_SCARTO = {
    "PERIMETRO_GEOGRAFICO",
    "TIPOLOGIA",
    "SUPERFICIE_INSUFFICIENTE",
    "DIRITTO",
    "VINCOLO_INSANABILE",
    "PREZZO_FUORI_SCALA",
}
MOTIVI_MONITORA = {"ASTA_SCADUTA", "TEMPO_INSUFFICIENTE"}

_PAROLE_VINCOLO_INSANABILE = (
    "inedificabilita assoluta", "inedificabile in modo assoluto",
    "abuso non sanabile", "abuso edilizio non sanabile",
    "vincolo di inedificabilita assoluta", "titolo abilitativo non ricostruibile",
)
_PAROLE_DIRITTO_SUPERFICIE_SCADENZA = ("diritto di superficie in scadenza",)


def _giorni_a(scadenza: Optional[datetime], data_run: date) -> Optional[int]:
    if scadenza is None:
        return None
    return (scadenza.date() - data_run).days


def applica_stadio1(rec: dict[str, Any], config: dict[str, Any], data_run: date) -> dict[str, Any]:
    """Ritorna {esito: PASSA|SCARTA|MONITORA, motivo, flag[], dati_mancanti[]}."""
    flag: list[str] = []
    dati_mancanti: list[dict[str, str]] = []

    # --- 1. Perimetro geografico -------------------------------------------------
    comune = valore_di(rec.get("comune"))
    provincia = valore_di(rec.get("provincia"))
    perimetro_cfg = config["perimetro"]
    esito_perimetro = geografia.valuta_perimetro(
        comune=comune,
        provincia_sigla=provincia,
        lat=rec.get("lat"),
        lon=rec.get("lon"),
        comuni_target=perimetro_cfg["comuni_target"],
        comuni_esclusi=perimetro_cfg["comuni_esclusi"],
        raggio_km=perimetro_cfg["raggio_km"],
    )
    if esito_perimetro["in_perimetro"] is False:
        return {
            "esito": "SCARTA",
            "motivo": "PERIMETRO_GEOGRAFICO",
            "dettaglio": esito_perimetro["motivo"],
            "flag": flag,
            "dati_mancanti": dati_mancanti,
        }
    if esito_perimetro["in_perimetro"] is None:
        flag.append("COMUNE_DA_VERIFICARE")
        dati_mancanti.append({
            "dato": "comune",
            "dove_trovarlo": "indirizzo completo sull'annuncio originale o sulla perizia",
            "azione": "aprire l'annuncio e verificare il comune esatto per confermare che sia nel perimetro",
        })

    # --- 2. Tipologia --------------------------------------------------------------
    categoria = valore_di(rec.get("categoria"))
    conf_categoria = confidenza_di(rec.get("categoria"))
    testo_completo = f"{rec.get('titolo') or ''} {rec.get('descrizione') or ''}"
    ha_indizi = testo.ha_indizi_industriali(testo_completo)

    if categoria in {"garage", "mobili"}:
        return {"esito": "SCARTA", "motivo": "TIPOLOGIA", "dettaglio": f"categoria: {categoria}", "flag": flag, "dati_mancanti": dati_mancanti}
    if categoria == "residenziale" and not ha_indizi:
        return {"esito": "SCARTA", "motivo": "TIPOLOGIA", "dettaglio": "residenziale senza indizi industriali nel testo", "flag": flag, "dati_mancanti": dati_mancanti}
    if categoria in {"altro", "commerciale"} and not ha_indizi:
        if conf_categoria == "bassa":
            flag.append("TIPOLOGIA_DA_VERIFICARE")
            dati_mancanti.append({
                "dato": "tipologia dell'immobile",
                "dove_trovarlo": "descrizione completa sull'annuncio o perizia",
                "azione": "verificare se si tratta di un immobile industriale/artigianale prima di scartare",
            })
        else:
            return {"esito": "SCARTA", "motivo": "TIPOLOGIA", "dettaglio": f"categoria '{categoria}' senza indizi industriali", "flag": flag, "dati_mancanti": dati_mancanti}

    # --- 3. Superficie ---------------------------------------------------------------
    filtri_cfg = config["filtri"]
    min_mq, max_mq = filtri_cfg["superficie_min_mq"], filtri_cfg["superficie_max_mq"]
    tolleranza_pct = filtri_cfg["tolleranza_soglia_pct"]
    superficie = valore_di(rec.get("superficie_mq"))
    conf_superficie = confidenza_di(rec.get("superficie_mq"))

    if superficie is None:
        flag.append("SUPERFICIE_IGNOTA")
        dati_mancanti.append({
            "dato": "superficie",
            "dove_trovarlo": "perizia CTU sul PVP (aste) o scheda tecnica/agenzia (vendite)",
            "azione": "scaricare la perizia o contattare l'agenzia per la metratura esatta",
        })
    elif conf_superficie == "bassa":
        flag.append("SUPERFICIE_CONFIDENZA_BASSA")
        dati_mancanti.append({
            "dato": "superficie (valore incerto)",
            "dove_trovarlo": "perizia CTU o planimetria",
            "azione": f"confermare la superficie: nel testo si leggono valori ambigui, tra cui {superficie:.0f} m² circa",
        })
    else:
        soglia_bassa = min_mq * (1 - tolleranza_pct / 100)
        if superficie < soglia_bassa:
            return {
                "esito": "SCARTA",
                "motivo": "SUPERFICIE_INSUFFICIENTE",
                "dettaglio": f"{superficie:.0f} m² sotto il minimo ({min_mq} m², tolleranza {tolleranza_pct}%)",
                "flag": flag,
                "dati_mancanti": dati_mancanti,
            }
        if superficie < min_mq:
            flag.append("AL_LIMITE_SOGLIA")
        elif superficie > max_mq:
            flag.append("FUORI_TAGLIA_GRANDE")

    altezza_scartata = (rec.get("_attributi") or {}).get("altezza_scartata_non_plausibile")
    if altezza_scartata is not None:
        flag.append("ALTEZZA_DA_VERIFICARE")
        dati_mancanti.append({
            "dato": "altezza sotto trave",
            "dove_trovarlo": "planimetria, perizia o sopralluogo",
            "azione": f"il valore letto ({altezza_scartata:g} m) non è plausibile ed è stato scartato: verificare l'altezza reale",
        })

    # --- 4. Tempi dell'asta ----------------------------------------------------------
    if rec.get("tipo_vendita") == "asta":
        scadenza = valore_di(rec.get("scadenza_asta"))
        giorni_minimi = filtri_cfg["giorni_minimi"]
        giorni_urgenza = filtri_cfg["giorni_urgenza"]
        if scadenza is None:
            flag.append("SCADENZA_IGNOTA")
            dati_mancanti.append({
                "dato": "scadenza dell'asta",
                "dove_trovarlo": "portale della fonte o PVP",
                "azione": "verificare la data dell'asta prima di procedere",
            })
        else:
            giorni = _giorni_a(scadenza, data_run)
            if giorni is not None and giorni < 0:
                return {"esito": "MONITORA", "motivo": "ASTA_SCADUTA", "dettaglio": f"scadenza il {scadenza.date().isoformat()}, {abs(giorni)} giorni fa", "flag": flag, "dati_mancanti": dati_mancanti}
            if giorni is not None and giorni < giorni_minimi:
                return {"esito": "MONITORA", "motivo": "TEMPO_INSUFFICIENTE", "dettaglio": f"mancano solo {giorni} giorni (minimo {giorni_minimi})", "flag": flag, "dati_mancanti": dati_mancanti}
            if giorni is not None and giorni <= giorni_urgenza:
                flag.append("URGENZA")

    # --- 5. Diritto sull'immobile ------------------------------------------------------
    diritto = valore_di(rec.get("diritto")) or ""
    if diritto.startswith("quota indivisa") or diritto == "nuda proprietà":
        return {"esito": "SCARTA", "motivo": "DIRITTO", "dettaglio": diritto, "flag": flag, "dati_mancanti": dati_mancanti}
    if any(p in diritto.lower() for p in _PAROLE_DIRITTO_SUPERFICIE_SCADENZA):
        return {"esito": "SCARTA", "motivo": "DIRITTO", "dettaglio": diritto, "flag": flag, "dati_mancanti": dati_mancanti}
    if diritto == "diritto di superficie":
        flag.append("DIRITTO_DA_VERIFICARE")
        dati_mancanti.append({
            "dato": "durata residua del diritto di superficie",
            "dove_trovarlo": "visura catastale/ipotecaria o perizia",
            "azione": "verificare quanti anni restano prima di considerare l'immobile idoneo",
        })

    # --- 6. Vincolo insanabile (solo indizi espliciti nel testo) -----------------------
    testo_basso = testo_completo.lower()
    for parola in _PAROLE_VINCOLO_INSANABILE:
        if parola in testo_basso:
            return {"esito": "SCARTA", "motivo": "VINCOLO_INSANABILE", "dettaglio": f"indizio testuale: '{parola}'", "flag": flag, "dati_mancanti": dati_mancanti}

    # --- 7. Prezzo fuori scala (serve una SLN stimata) ----------------------------------
    prezzo = valore_di(rec.get("prezzo_eur"))
    if prezzo is not None and superficie is not None and conf_superficie != "bassa":
        altezza = valore_di(rec.get("altezza_m"))
        sln = economico.calcola_sln(superficie, altezza, categoria, config)
        if sln["atteso"] and sln["atteso"] > 0:
            rapporto = prezzo / sln["atteso"]
            if rapporto > filtri_cfg["soglia_hard_eur_mq_sln"]:
                return {
                    "esito": "SCARTA",
                    "motivo": "PREZZO_FUORI_SCALA",
                    "dettaglio": f"{rapporto:.0f} €/m² SLN stimata, sopra la soglia di {filtri_cfg['soglia_hard_eur_mq_sln']}",
                    "flag": flag,
                    "dati_mancanti": dati_mancanti,
                }

    if prezzo is not None and prezzo < filtri_cfg["soglia_prezzo_anomalo_eur"]:
        flag.append("PREZZO_ANOMALO")
    if prezzo is None:
        flag.append("PREZZO_IGNOTO")
        dati_mancanti.append({
            "dato": "prezzo",
            "dove_trovarlo": "agenzia/portale (trattativa riservata) o PVP",
            "azione": "chiedere il prezzo all'agenzia o al delegato della procedura",
        })

    return {"esito": "PASSA", "motivo": None, "dettaglio": None, "flag": flag, "dati_mancanti": dati_mancanti}
