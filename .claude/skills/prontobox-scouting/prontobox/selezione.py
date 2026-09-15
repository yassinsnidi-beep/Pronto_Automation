"""§4 - Selezione settimanale delle 10 opportunita', con il controllo
sullo storico (§4.2) e le regole di confronto (§4.3). Tutto codice
deterministico: nessuna chiamata al modello qui dentro."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from . import util
from .campo import valore_di

ORDINE_ESITO = {"SCARTA": 0, "MONITORA": 1, "DA APPROFONDIRE": 2, "PROMUOVI": 3}


# --------------------------------------------------------------------------- snapshot (usato sia per il confronto sia per il report)


def costruisci_snapshot(rec: dict[str, Any], dati_mancanti: list[dict[str, str]]) -> dict[str, Any]:
    scadenza = valore_di(rec.get("scadenza_asta"))
    return {
        "prezzo_eur": valore_di(rec.get("prezzo_eur")),
        "offerta_minima_eur": valore_di(rec.get("offerta_minima_eur")),
        "superficie_mq": valore_di(rec.get("superficie_mq")),
        "altezza_m": valore_di(rec.get("altezza_m")),
        "comune": valore_di(rec.get("comune")),
        "provincia": valore_di(rec.get("provincia")),
        "lat": rec.get("lat"),
        "lon": rec.get("lon"),
        "scadenza_asta": scadenza.date().isoformat() if scadenza else None,
        "tribunale": rec.get("tribunale"),
        "procedura": rec.get("procedura"),
        "lotto": rec.get("lotto"),
        "categoria": valore_di(rec.get("categoria")),
        "stato_occupazione": rec.get("stato_occupazione"),
        "diritto": valore_di(rec.get("diritto")),
        "dati_mancanti": sorted({dm["dato"] for dm in dati_mancanti}),
    }


# --------------------------------------------------------------------------- classifica (§4.1)


def costruisci_classifica(valutazioni: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidati = [v for v in valutazioni if v["esito"] in ("PROMUOVI", "DA APPROFONDIRE")]

    def chiave(v: dict[str, Any]) -> tuple:
        voto = -(v["voto"] if v["voto"] is not None else 0)
        yoc = v["economico"]["yoc_scenario_base"] if v.get("economico") else None
        yoc_key = -(yoc if yoc is not None else -999.0)
        scadenza = valore_di(v["rec"].get("scadenza_asta"))
        scadenza_key = scadenza.isoformat() if scadenza else "9999-99-99"
        return (voto, yoc_key, scadenza_key, v["rec"]["id"])

    ordinati = sorted(candidati, key=chiave)
    for posizione, v in enumerate(ordinati, start=1):
        v["posizione_nel_ranking"] = posizione
    return ordinati


# --------------------------------------------------------------------------- lettura storico (§4.2, §5.3)


def leggi_summary(percorso: Path) -> tuple[dict[str, Any], str]:
    if not percorso.is_file():
        return {"meta": {"schema_version": 1}, "segnalazioni": []}, "assente: prima run"
    try:
        dati = util.leggi_json(percorso)
        if not isinstance(dati, dict) or not isinstance(dati.get("segnalazioni"), list):
            raise ValueError("struttura inattesa")
        return dati, "ok"
    except Exception:
        timestamp = util.ora_roma().strftime("%Y%m%d_%H%M%S")
        backup = percorso.with_name(f"summary.corrotto_{timestamp}.json")
        try:
            percorso.replace(backup)
        except OSError:
            pass
        return {"meta": {"schema_version": 1}, "segnalazioni": []}, f"corrotto: backup in {backup.name} e ripartenza da zero"


def _trova_precedente(opp_id: str, alias_ids: list[str], identity_key: str, righe_ok: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    tutti_id = {opp_id, *alias_ids}
    candidati = [r for r in righe_ok if r.get("id") in tutti_id or bool(set(r.get("alias_ids") or []) & tutti_id)]
    if not candidati:
        candidati = [r for r in righe_ok if identity_key and r.get("identity_key") == identity_key]
    if not candidati:
        return None
    return max(candidati, key=lambda r: (r.get("data") or "", r.get("file") or ""))


def _apri_report_storico(output_dir: Path, nome_file: str) -> Optional[dict[str, Any]]:
    percorso = output_dir / nome_file
    if not percorso.is_file():
        return None
    try:
        return util.leggi_json(percorso)
    except Exception:
        return None


def _trova_opportunita_in_report(report: dict[str, Any], opp_id: str) -> Optional[dict[str, Any]]:
    for opp in report.get("opportunita") or []:
        if opp.get("id") == opp_id:
            return opp
    return None


# --------------------------------------------------------------------------- confronto (§4.3)


def confronta(prec: dict[str, Any], attuale: dict[str, Any], config: dict[str, Any], data_run: date) -> dict[str, Any]:
    """prec e attuale: dict con 'voto', 'esito', 'hash_config', 'score_ampiezza'
    e 'snapshot' (vedi costruisci_snapshot)."""
    sel = config["selezione"]
    positivi: list[tuple[str, str]] = []
    negativi: list[tuple[str, str]] = []
    note: list[str] = []
    peggioramento_decisivo = False

    def confronta_prezzo(chiave: str, etichetta: str) -> None:
        p_prec = prec["snapshot"].get(chiave)
        p_att = attuale["snapshot"].get(chiave)
        if not p_prec or not p_att or p_prec <= 0:
            return
        variazione = (p_att - p_prec) / p_prec * 100
        if variazione <= sel["soglia_ribasso_pct"]:
            testo = f"{etichetta} sceso da {util.fmt_eur(p_prec)} a {util.fmt_eur(p_att)} ({variazione:.0f}%)"
            if variazione <= sel["soglia_ribasso_anomalo_pct"]:
                testo += " — ribasso anomalo: verificare che il perimetro del lotto non sia cambiato [VERIFICARE_PERIMETRO_LOTTO]"
            positivi.append(("PREZZO_SCESO", testo))
        elif variazione >= -sel["soglia_ribasso_pct"]:
            negativi.append(("PREZZO_SALITO", f"{etichetta} salito da {util.fmt_eur(p_prec)} a {util.fmt_eur(p_att)} (+{variazione:.0f}%)"))

    confronta_prezzo("prezzo_eur", "Prezzo base")
    confronta_prezzo("offerta_minima_eur", "Offerta minima")

    v_prec, v_att = prec.get("voto"), attuale.get("voto")
    hash_diverso = bool(prec.get("hash_config")) and bool(attuale.get("hash_config")) and prec.get("hash_config") != attuale.get("hash_config")
    if v_prec is not None and v_att is not None:
        delta = v_att - v_prec
        if delta >= sel["soglia_delta_voto"]:
            if hash_diverso:
                note.append(
                    f"il voto è salito da {v_prec} a {v_att}, ma la configurazione è cambiata (hash_config diverso): "
                    "la differenza può dipendere dai nuovi pesi, non dall'immobile, quindi da sola non basta a risegnalare"
                )
            else:
                positivi.append(("VOTO_SALITO", f"Voto salito da {v_prec} a {v_att}"))
        elif delta <= -sel["soglia_delta_voto"]:
            negativi.append(("VOTO_SCESO", f"Voto sceso da {v_prec} a {v_att}"))
            peggioramento_decisivo = True

    e_prec, e_att = prec.get("esito"), attuale.get("esito")
    esito_migliorato = False
    if e_prec and e_att and e_prec != e_att:
        rank_prec, rank_att = ORDINE_ESITO.get(e_prec, 0), ORDINE_ESITO.get(e_att, 0)
        if rank_att < rank_prec:
            negativi.append(("ESITO_PEGGIORATO", f"Esito peggiorato da {e_prec} a {e_att}"))
            peggioramento_decisivo = True
        elif rank_att > rank_prec:
            esito_migliorato = True

    prima_mancanti = set(prec["snapshot"].get("dati_mancanti") or [])
    ora_mancanti = set(attuale["snapshot"].get("dati_mancanti") or [])
    sbloccati = sorted(prima_mancanti - ora_mancanti)
    if sbloccati:
        ampiezza_prec, ampiezza_att = prec.get("score_ampiezza"), attuale.get("score_ampiezza")
        range_ristretto = (
            ampiezza_prec is not None and ampiezza_att is not None and (ampiezza_prec - ampiezza_att) >= sel["soglia_restringimento_range"]
        )
        if esito_migliorato or range_ristretto:
            dettagli = ", ".join(sbloccati)
            if esito_migliorato:
                testo = f"Ora c'è {dettagli} (prima mancante): l'esito passa da {e_prec} a {e_att}"
            else:
                testo = f"Ora c'è {dettagli} (prima mancante): l'intervallo di punteggio si è ristretto da {ampiezza_prec:.0f} a {ampiezza_att:.0f} punti"
            positivi.append(("DATO_SBLOCCATO", testo))

    s_prec = prec["snapshot"].get("scadenza_asta")
    s_att = attuale["snapshot"].get("scadenza_asta")
    if s_att and s_att != s_prec:
        try:
            giorni = (date.fromisoformat(s_att) - data_run).days
        except ValueError:
            giorni = None
        if giorni is not None and giorni >= config["filtri"]["giorni_minimi"]:
            positivi.append(("ASTA_DI_NUOVO_AZIONABILE", f"Nuova asta il {util.fmt_data(s_att)}: {giorni} giorni per preparare l'offerta"))

    occ_prec = (prec["snapshot"].get("stato_occupazione") or "").lower()
    occ_att = (attuale["snapshot"].get("stato_occupazione") or "").lower()
    if "occupat" not in occ_prec and "locat" not in occ_prec and ("occupat" in occ_att or "locat" in occ_att):
        negativi.append(("OCCUPAZIONE_SFAVOREVOLE", "Nuovo dato: l'immobile risulta ora occupato/locato"))

    dir_prec = (prec["snapshot"].get("diritto") or "").lower()
    dir_att = (attuale["snapshot"].get("diritto") or "").lower()
    if "quota" not in dir_prec and "quota" in dir_att:
        negativi.append(("DIRITTO_SFAVOREVOLE", f"Nuovo dato: {attuale['snapshot'].get('diritto')}"))

    if positivi and not peggioramento_decisivo:
        esito_confronto = "MIGLIORATA"
    elif positivi and peggioramento_decisivo:
        esito_confronto = "PEGGIORATA"
    elif negativi:
        esito_confronto = "PEGGIORATA"
    else:
        esito_confronto = "INVARIATA"

    motivi = [testo for _, testo in positivi] if esito_confronto == "MIGLIORATA" else []
    parti_dettaglio = [testo for _, testo in (positivi + negativi)] + note
    if not parti_dettaglio:
        parti_dettaglio = ["nessuna variazione sopra soglia"]
    if esito_confronto == "PEGGIORATA" and positivi and negativi:
        parti_dettaglio.insert(0, "caso misto: c'è sia un miglioramento sia un peggioramento")

    return {"esito": esito_confronto, "motivi": motivi, "dettaglio": "; ".join(parti_dettaglio)}


# --------------------------------------------------------------------------- orchestrazione (§4.2, §4.4)


def seleziona(
    valutazioni: list[dict[str, Any]],
    config: dict[str, Any],
    output_dir: Path,
    data_run: date,
    nome_file_run: str,
    hash_config_attuale: str,
) -> dict[str, Any]:
    classifica = costruisci_classifica(valutazioni)

    percorso_summary = output_dir / "summary.json"
    summary, stato_summary = leggi_summary(percorso_summary)
    righe_ok = [r for r in summary.get("segnalazioni", []) if r.get("file") != nome_file_run]

    n_segnalazioni = config["selezione"]["n_segnalazioni"]
    segnalate: list[dict[str, Any]] = []
    escluse_storico: list[dict[str, Any]] = []
    posizioni_scorse = 0

    for v in classifica:
        if len(segnalate) >= n_segnalazioni:
            break
        posizioni_scorse += 1
        rec = v["rec"]
        prec_riga = _trova_precedente(rec["id"], rec.get("alias_ids") or [], rec.get("identity_key", ""), righe_ok)

        if prec_riga is None:
            v["stato_segnalazione"] = "NUOVA"
            v["motivi_risegnalazione"] = []
            v["segnalazione_precedente"] = None
            segnalate.append(v)
            continue

        report_storico = _apri_report_storico(output_dir, prec_riga.get("file", ""))
        opp_storica = _trova_opportunita_in_report(report_storico, prec_riga["id"]) if report_storico else None
        if not report_storico or not opp_storica:
            v["stato_segnalazione"] = "NUOVA"
            v["motivi_risegnalazione"] = []
            v["segnalazione_precedente"] = None
            v["nota_storico"] = f"segnalata il {prec_riga.get('data')} ma il file storico {prec_riga.get('file')} è illeggibile: confronto impossibile"
            segnalate.append(v)
            continue

        prec_dict = {
            "voto": opp_storica.get("voto"),
            "esito": opp_storica.get("esito"),
            "hash_config": (report_storico.get("meta") or {}).get("hash_config"),
            "score_ampiezza": ((opp_storica.get("score") or {}).get("max") or 0) - ((opp_storica.get("score") or {}).get("min") or 0),
            "snapshot": opp_storica.get("snapshot") or {},
        }
        attuale_dict = {
            "voto": v["voto"],
            "esito": v["esito"],
            "hash_config": hash_config_attuale,
            "score_ampiezza": v["scorecard"]["ampiezza"] if v.get("scorecard") else None,
            "snapshot": v["snapshot"],
        }
        esito_confronto = confronta(prec_dict, attuale_dict, config, data_run)

        if esito_confronto["esito"] == "MIGLIORATA":
            v["stato_segnalazione"] = "RISEGNALATA"
            v["motivi_risegnalazione"] = esito_confronto["motivi"]
            v["segnalazione_precedente"] = {
                "data": prec_riga.get("data"),
                "file": prec_riga.get("file"),
                "voto": opp_storica.get("voto"),
                "esito": opp_storica.get("esito"),
                "prezzo_eur": (opp_storica.get("snapshot") or {}).get("prezzo_eur"),
            }
            segnalate.append(v)
        else:
            escluse_storico.append({
                "id": rec["id"],
                "canale": rec["canale"],
                "posizione_nel_ranking": v["posizione_nel_ranking"],
                "voto": v["voto"],
                "esito_confronto": esito_confronto["esito"],
                "dettaglio": esito_confronto["dettaglio"],
                "segnalazione_precedente": {"data": prec_riga.get("data"), "file": prec_riga.get("file")},
            })

    return {
        "classifica": classifica,
        "segnalate": segnalate,
        "escluse_storico": escluse_storico,
        "posizioni_scorse": posizioni_scorse,
        "summary_precedente": summary,
        "stato_summary": stato_summary,
        "righe_ok": righe_ok,
    }
