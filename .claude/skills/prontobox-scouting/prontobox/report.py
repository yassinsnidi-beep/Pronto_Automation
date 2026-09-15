"""§5 - Scrittura di report_AAAA_MM_GG.json e summary.json, con l'ordine e
la robustezza richiesti da §5.3."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path
from typing import Any, Optional

from . import testo, util


def _versione_skill() -> str:
    from . import VERSIONE_SKILL as V

    return V


# --------------------------------------------------------------------------- blocchi per opportunita'


def costruisci_provenienza_campi(rec: dict[str, Any]) -> dict[str, Any]:
    campi: dict[str, Any] = {}
    for chiave in ("prezzo_eur", "offerta_minima_eur", "superficie_mq", "comune", "provincia", "scadenza_asta", "categoria", "altezza_m", "diritto"):
        c = rec.get(chiave)
        if isinstance(c, dict) and c.get("valore") is not None:
            campi[chiave] = dict(c)
    return campi


def costruisci_valutazione_testuale(v: dict[str, Any], giudizio: Optional[dict[str, Any]]) -> str:
    frasi: list[str] = []
    if v["stato_segnalazione"] == "RISEGNALATA" and v.get("motivi_risegnalazione"):
        frasi.append("Risegnalata — " + " | ".join(v["motivi_risegnalazione"]) + ".")
    if v.get("nota_storico"):
        # §4.2: precedente segnalazione trovata nel summary, ma il file di
        # report storico a cui punta è illeggibile o non contiene più
        # l'opportunità: trattata come NUOVA, ma lo si dichiara qui perché
        # non esiste un campo dedicato nello schema del report (§5.1).
        frasi.append(v["nota_storico"].capitalize() + ".")
    frasi.append(f"Voto {v['voto']}/100, classe {v['classe'] or 'n.d.'}, esito {v['esito']}.")

    if not giudizio:
        frasi.append("[giudizio] Valutazione testuale non disponibile: il file dei giudizi non conteneva questo immobile; generarla manualmente prima di procedere.")
        return " ".join(frasi)

    if giudizio.get("punti_di_forza"):
        frasi.append("Punti di forza: [giudizio] " + "; ".join(giudizio["punti_di_forza"]) + ".")
    if giudizio.get("rischi_principali"):
        frasi.append("Rischi principali: [giudizio] " + "; ".join(giudizio["rischi_principali"]) + ".")
    if giudizio.get("percorso_autorizzativo_motivo"):
        frasi.append(f"Percorso autorizzativo stimato: [giudizio] {giudizio['percorso_autorizzativo_motivo']}.")
    if giudizio.get("da_verificare"):
        frasi.append("Da verificare per primo: [giudizio] " + "; ".join(giudizio["da_verificare"]) + ".")
    if giudizio.get("fonte") == "stima_euristica":
        frasi.append("[giudizio generato con euristiche di ripiego, non da un modello linguistico: va riletto prima di agire]")
    return " ".join(frasi)


def _economico_per_report(economico: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not economico:
        return None
    base = economico.get("prezzo_base") or {}
    minima = economico.get("offerta_minima") or {}
    return {
        "sln_mq": economico["sln_mq"],
        "costo_all_in_eur": base.get("costo_all_in_eur"),
        "costo_per_mq_sln": base.get("costo_per_mq_sln_eur"),
        "yoc": base.get("yoc"),
        "yoc_offerta_minima": minima.get("yoc"),
        "hurdle": economico["hurdle"],
        "mesi_al_go_live": economico["mesi_al_go_live"],
        "sconto_da_chiedere_eur": economico["sconto_da_chiedere_eur"],
        "capex_eur": economico["capex_eur"],
        "assunzioni": economico["assunzioni"],
    }


def costruisci_opportunita(v: dict[str, Any], giudizio: Optional[dict[str, Any]], posizione: int) -> dict[str, Any]:
    rec = v["rec"]
    testo_completo = f"{rec.get('titolo') or ''} {rec.get('descrizione') or ''}"
    checklist = testo.checklist_dd_da_testo(testo_completo, piano=rec.get("piano"))
    if giudizio and giudizio.get("indizi_rischio"):
        gia = {(c["voce"], c["indizio"]) for c in checklist}
        for extra in giudizio["indizi_rischio"]:
            if (extra.get("voce"), extra.get("indizio")) not in gia:
                checklist.append(extra)

    flag = list(dict.fromkeys((v["stadio1"].get("flag") or [])))

    return {
        "posizione": posizione,
        "posizione_nel_ranking": v["posizione_nel_ranking"],
        "id": rec["id"],
        "alias_ids": rec.get("alias_ids") or [],
        "identity_key": rec.get("identity_key"),
        "canale": rec["canale"],
        "fonte": rec.get("fonte"),
        "tipo_vendita": rec.get("tipo_vendita"),
        "url": rec.get("url"),
        "urls_altre_fonti": rec.get("urls_altre_fonti") or [],
        "stato_segnalazione": v["stato_segnalazione"],
        "motivi_risegnalazione": v.get("motivi_risegnalazione") or [],
        "segnalazione_precedente": v.get("segnalazione_precedente"),
        "voto": v["voto"],
        "esito": v["esito"],
        "classe": v["classe"],
        "score": {
            "min": v["scorecard"]["min"],
            "atteso": v["scorecard"]["atteso"],
            "max": v["scorecard"]["max"],
            "breakdown": v["scorecard"]["breakdown"],
        } if v.get("scorecard") else None,
        "economico": _economico_per_report(v.get("economico")),
        "snapshot": v["snapshot"],
        "provenienza_campi": costruisci_provenienza_campi(rec),
        "flag": flag,
        "checklist_dd": checklist,
        "dati_mancanti": v["stadio1"].get("dati_mancanti") or [],
        "valutazione_testuale": costruisci_valutazione_testuale(v, giudizio),
    }


# --------------------------------------------------------------------------- meta e report completo


def costruisci_meta(
    *,
    data_run: date,
    nome_file_run: str,
    hash_config: str,
    stato_summary: str,
    meta_input: list[dict[str, Any]],
    statistiche: dict[str, Any],
    note: list[str],
) -> dict[str, Any]:
    return {
        "report_id": util.id_run(data_run),
        "file": nome_file_run,
        "generato_il": util.ora_roma().isoformat(),
        "versione_skill": _versione_skill(),
        "hash_config": hash_config,
        "stato_summary": stato_summary,
        "input": meta_input,
        "statistiche": statistiche,
        "note": note,
    }


def costruisci_report(
    *,
    meta: dict[str, Any],
    segnalate: list[dict[str, Any]],
    giudizi: dict[str, Any],
    escluse_storico: list[dict[str, Any]],
    scartate_per_motivo: dict[str, list[str]],
    monitora: list[dict[str, Any]],
) -> dict[str, Any]:
    opportunita = [
        costruisci_opportunita(v, giudizi.get(v["rec"]["id"]), posizione)
        for posizione, v in enumerate(segnalate, start=1)
    ]
    return {
        "meta": meta,
        "opportunita": opportunita,
        "escluse_per_storico": escluse_storico,
        "scartate": scartate_per_motivo,
        "monitora": monitora,
    }


# --------------------------------------------------------------------------- scrittura (§5.3)


def scrivi_report(report: dict[str, Any], output_dir: Path, nome_file_run: str) -> Path:
    percorso = output_dir / nome_file_run
    util.scrivi_json_atomico(report, percorso)
    return percorso


def valida_report(report: dict[str, Any]) -> list[str]:
    """Controlli minimi dello schema (§5.1, §9 test 35). Ritorna la lista
    degli errori trovati (vuota se il report e' valido)."""
    errori: list[str] = []
    for opp in report.get("opportunita") or []:
        pid = opp.get("id", "?")
        if opp.get("canale") not in ("ASTE", "IMM_ID"):
            errori.append(f"{pid}: canale non valido ({opp.get('canale')!r})")
        voto = opp.get("voto")
        if not isinstance(voto, int) or not (0 <= voto <= 100):
            errori.append(f"{pid}: voto non valido ({voto!r})")
        if not opp.get("valutazione_testuale"):
            errori.append(f"{pid}: valutazione_testuale vuota")
        snap = opp.get("snapshot")
        if not isinstance(snap, dict) or "dati_mancanti" not in snap:
            errori.append(f"{pid}: snapshot incompleto o assente")
        if opp.get("stato_segnalazione") == "RISEGNALATA" and not opp.get("motivi_risegnalazione"):
            errori.append(f"{pid}: RISEGNALATA senza motivi_risegnalazione")
    return errori


def aggiorna_summary(
    righe_ok: list[dict[str, Any]],
    segnalate: list[dict[str, Any]],
    data_run: date,
    nome_file_run: str,
    output_dir: Path,
) -> dict[str, Any]:
    percorso = output_dir / "summary.json"
    if percorso.is_file():
        try:
            shutil.copy2(percorso, percorso.with_suffix(".json.bak"))
        except OSError:
            pass

    nuove_righe = [
        {
            "id": v["rec"]["id"],
            "data": data_run.isoformat(),
            "file": nome_file_run,
            "canale": v["rec"]["canale"],
            "identity_key": v["rec"].get("identity_key"),
            "alias_ids": v["rec"].get("alias_ids") or [],
            "stato_segnalazione": v["stato_segnalazione"],
            "voto": v["voto"],
        }
        for v in segnalate
    ]

    nuovo_summary = {
        "meta": {"schema_version": 1, "aggiornato_il": util.ora_roma().isoformat()},
        "segnalazioni": righe_ok + nuove_righe,
    }
    util.scrivi_json_atomico(nuovo_summary, percorso)
    return nuovo_summary
