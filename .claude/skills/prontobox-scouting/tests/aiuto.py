"""Utilita' condivise dai test: config di prova, fixture su file reali
(estratti in fixtures/INPUT/, vedi tests/fixtures/INPUT/) e un costruttore
di record canonici sintetici per i casi non presenti nei dati reali."""

from __future__ import annotations

import copy
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

RADICE_SKILL = Path(__file__).resolve().parent.parent
if str(RADICE_SKILL) not in sys.path:
    sys.path.insert(0, str(RADICE_SKILL))

from prontobox import config as cfgmod  # noqa: E402
from prontobox import filtri, pipeline, report, selezione, util  # noqa: E402
from prontobox.campo import campo  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DATA_RUN_TEST = date(2026, 9, 16)


def config_di_prova(tmp_output_dir: Path) -> dict[str, Any]:
    """Config reale (config.yaml della skill) con i percorsi rediretti sulle
    fixture, cosi' i test usano gli stessi default di produzione."""
    config = cfgmod.carica_config(RADICE_SKILL / "config.yaml")
    config = copy.deepcopy(config)
    config["percorsi"]["input_aste"] = str(FIXTURES / "INPUT" / "ASTE")
    config["percorsi"]["input_immid"] = str(FIXTURES / "INPUT" / "IMM_ID" / "current_week")
    config["percorsi"]["output_dir"] = str(tmp_output_dir)
    return config


def root_fittizio() -> Path:
    """I percorsi di input/output in config_di_prova sono gia' assoluti,
    quindi 'root' serve solo come base per eventuali percorsi relativi
    residui: non importa cosa sia, basta che esista."""
    return FIXTURES


def rec_minimo(**override: Any) -> dict[str, Any]:
    """Costruisce un record canonico valido e completo, per isolare un
    singolo comportamento (es. un valore di superficie esatto) senza dover
    passare per un intero file di input. Di default e' un capannone a
    Milano, in asta tra 60 giorni, con tutti i dati noti — ogni test lo
    specializza con override mirati."""
    base: dict[str, Any] = {
        "id": "test_1",
        "alias_ids": [],
        "identity_key": "test|milano|capannone|1500",
        "canale": "ASTE",
        "tipo_vendita": "asta",
        "fonte": "test",
        "file_origine": "test.json",
        "url": "https://example.test/1",
        "urls_altre_fonti": [],
        "titolo": "Capannone di prova",
        "descrizione": "Capannone industriale di prova, piena proprietà, nessuna difformità.",
        "prezzo_eur": campo(500_000, "json", "alta"),
        "offerta_minima_eur": campo(None, "stima", "bassa"),
        "superficie_mq": campo(1500.0, "json", "alta"),
        "superfici_etichettate": [],
        "comune": campo("Milano", "json", "alta"),
        "provincia": campo("MI", "json", "alta"),
        "lat": None,
        "lon": None,
        "scadenza_asta": campo(util.a_roma(datetime(2026, 11, 15, 10, 0)), "json", "alta"),
        "tribunale": "Tribunale di Milano",
        "procedura": "1/2026",
        "lotto": "UNICO",
        "categoria": campo("capannone", "regex_testo", "media"),
        "altezza_m": campo(5.6, "raw_detail", "alta"),
        "piano": "piano terra",
        "n_piani": "1 piano",
        "stato_occupazione": "libero",
        "anno_costruzione": 2015,
        "diritto": campo("piena proprietà", "regex_testo", "alta"),
        "_attributi": {},
    }
    base.update(override)
    return base


def nome_file_run(data_run: date) -> str:
    return f"report_{data_run.year:04d}_{data_run.month:02d}_{data_run.day:02d}.json"


def esegui_run_sintetica(
    records: list[dict],
    config: dict[str, Any],
    output_dir: Path,
    data_run: date,
    *,
    hash_config: str = "hash-test-fisso",
    giudizi: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Riproduce esattamente l'orchestrazione di 'scout.py scrivi' (vedi
    scout.py:cmd_scrivi), ma a partire da record canonici gia' in memoria
    invece che da file in INPUT/: serve ai test di §9 'Selezione e storico'
    (18-35), dove serve costruire scenari precisi (prezzo che scende, voto
    che sale, ecc.) su piu' run consecutive senza dipendere dai dati reali
    del 16/09/2026, che non si prestano a un confronto storico controllato.

    Ritorna un dict con 'report' (il report scritto), 'sel' (il risultato
    grezzo di selezione.seleziona, utile per ispezionare classifica/
    escluse_storico), 'errori' (dalla validazione schema) e 'summary'
    (il summary.json risultante, riletto da disco)."""
    giudizi = giudizi or {}
    valutazioni = []
    for rec in records:
        stadio1 = filtri.applica_stadio1(rec, config, data_run)
        valutazioni.append({"rec": rec, "stadio1": stadio1})
    pipeline.valuta_tutto(valutazioni, giudizi, config, RADICE_SKILL)

    file_run = nome_file_run(data_run)
    sel = selezione.seleziona(valutazioni, config, output_dir, data_run, file_run, hash_config)
    statistiche = pipeline.costruisci_statistiche({
        "valutazioni": valutazioni,
        "selezione": sel,
        "record_letti": len(records),
        "duplicati_uniti": 0,
    })
    scartate, monitora = pipeline.costruisci_scartate_e_monitora(valutazioni)
    meta = report.costruisci_meta(
        data_run=data_run,
        nome_file_run=file_run,
        hash_config=hash_config,
        stato_summary=sel["stato_summary"],
        meta_input=[],
        statistiche=statistiche,
        note=pipeline.note_selezione(sel, config),
    )
    report_dict = report.costruisci_report(
        meta=meta,
        segnalate=sel["segnalate"],
        giudizi=giudizi,
        escluse_storico=sel["escluse_storico"],
        scartate_per_motivo=scartate,
        monitora=monitora,
    )
    errori = report.valida_report(report_dict)
    report.scrivi_report(report_dict, output_dir, file_run)
    if sel["segnalate"]:
        report.aggiorna_summary(sel["righe_ok"], sel["segnalate"], data_run, file_run, output_dir)

    percorso_summary = output_dir / "summary.json"
    summary_letto = util.leggi_json(percorso_summary) if percorso_summary.is_file() else None

    return {"report": report_dict, "sel": sel, "errori": errori, "summary": summary_letto, "statistiche": statistiche}
