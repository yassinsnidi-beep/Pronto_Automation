#!/usr/bin/env python3
"""scout.py - motore della skill prontobox-scouting.

Sottocomandi:
    prepara    normalizza gli input, applica i filtri deterministici (Stadio 1)
               e scrive il file di lavoro con i soli testi da interpretare.
    seleziona  ricalcola tutta la pipeline (Stadi 1-4), fa la selezione
               settimanale con il controllo sullo storico (§4) e scrive
               un'ANTEPRIMA leggibile in OUTPUT/ (non e' un passo obbligato
               prima di 'scrivi': vedi la nota "PROGETTAZIONE" sotto).
    scrivi     ricalcola la pipeline, valida lo schema e scrive
               report_AAAA_MM_GG.json + aggiorna summary.json.
    tutto      incatena prepara + scrivi in un solo processo (comodo per i
               test e per una run manuale da terminale).

NOTA DI PROGETTAZIONE (scelta per ambiguita', vedi anche README.md): la
pipeline (adattatori, filtri, scorecard, gate economico) e' interamente
deterministica - stesso input + stessa data + stessa config = stesso
risultato. Per questo 'seleziona' e 'scrivi' sono entrambi AUTOSUFFICIENTI:
ciascuno ricarica gli input e ricalcola tutto da zero (con lo stesso
--giudizi, se passato), invece di dipendere da un fragile file intermedio
scritto dal passo precedente. Il costo e' ricalcolare gli Stadi 1-4 due
volte se si lancia prima 'seleziona' e poi 'scrivi' (trascurabile: poche
centinaia di record, nessuna chiamata di rete con fetch_abilitato=false).
In produzione (§7.3) il pannello lancia comunque 'seleziona' e 'scrivi' in
sequenza nella stessa run, quindi il comportamento visibile all'utente e'
identico a una pipeline con hand-off su file.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prontobox import VERSIONE_SKILL
from prontobox import config as cfgmod
from prontobox import pipeline, report, util


def _percorso_config(args: argparse.Namespace, root: Path) -> Path:
    if args.config:
        return Path(args.config)
    return root / ".claude" / "skills" / "prontobox-scouting" / "config.yaml"


def _data_run(args: argparse.Namespace) -> date:
    if args.data:
        return date.fromisoformat(args.data)
    return util.oggi_roma()


def _nome_file_run(config: dict[str, Any], data_run: date) -> str:
    fmt = config["percorsi"]["formato_nome_report"]
    return fmt.format(AAAA=f"{data_run.year:04d}", MM=f"{data_run.month:02d}", GG=f"{data_run.day:02d}")


def _carica_giudizi(percorso: Optional[str]) -> Optional[dict[str, Any]]:
    if not percorso:
        return None
    p = Path(percorso)
    if not p.is_file():
        print(f"[avviso] --giudizi {p} non trovato: uso il ripiego euristico", file=sys.stderr)
        return None
    try:
        dati = util.leggi_json(p)
    except Exception as exc:
        print(f"[avviso] --giudizi {p} illeggibile ({exc}): uso il ripiego euristico", file=sys.stderr)
        return None
    if not isinstance(dati, dict):
        print(f"[avviso] --giudizi {p} non è un oggetto JSON id->giudizio: uso il ripiego euristico", file=sys.stderr)
        return None
    return dati


def _setup_comune(args: argparse.Namespace) -> tuple[Path, dict[str, Any], date, str, str, Path]:
    root = Path(args.root).resolve()
    percorso_config = _percorso_config(args, root)
    config = cfgmod.carica_config(percorso_config)
    data_run = _data_run(args)
    nome_file_run = _nome_file_run(config, data_run)
    hash_config = cfgmod.hash_config(percorso_config)
    output_dir = root / config["percorsi"]["output_dir"]
    return root, config, data_run, nome_file_run, hash_config, output_dir


def cmd_prepara(args: argparse.Namespace) -> int:
    root, config, data_run, _nome_file_run_val, _hash, output_dir = _setup_comune(args)
    preparato = pipeline.carica_e_prepara(config, root, data_run)

    lavoro = {
        "meta": {
            "data_run": data_run.isoformat(),
            "versione_skill": VERSIONE_SKILL,
            "record_letti": preparato["record_letti"],
            "duplicati_uniti": preparato["duplicati_uniti"],
            "n_sopravvissuti_stadio1": len(preparato["lavoro_items"]),
            "note": preparato["note"],
            "istruzioni_per_il_modello": (
                "Per ogni elemento di 'items', classifica percorso_autorizzativo_punti "
                "(uno tra 12, 10, 7, 5, 2 - vedi SKILL.md/§Stadio2) e aggiungi eventuali "
                "indizi_rischio, punti_di_forza, rischi_principali, da_verificare. "
                "Scrivi il risultato come oggetto JSON {id: {...}} nel file passato a "
                "'scout.py seleziona/scrivi --giudizi'."
            ),
        },
        "items": preparato["lavoro_items"],
    }

    if args.dry_run:
        print(json.dumps(lavoro, ensure_ascii=False, indent=2, default=str))
        print(f"\n[dry-run] {len(preparato['lavoro_items'])} elementi pronti per il giudizio (nessun file scritto).", file=sys.stderr)
        return 0

    percorso = output_dir / f".lavoro_testi_{util.id_run(data_run)}.json"
    util.scrivi_json_atomico(lavoro, percorso)
    print(f"Scritto {percorso} ({len(preparato['lavoro_items'])} elementi da valutare)")
    return 0


def _esegui_pipeline_completa(args: argparse.Namespace) -> tuple[Path, dict[str, Any], date, str, str, Path, dict[str, Any]]:
    root, config, data_run, nome_file_run, hash_config, output_dir = _setup_comune(args)
    giudizi_esterni = _carica_giudizi(getattr(args, "giudizi", None))
    risultato = pipeline.esegui_seleziona(config, root, data_run, nome_file_run, hash_config, giudizi_esterni)
    return root, config, data_run, nome_file_run, hash_config, output_dir, risultato


def cmd_seleziona(args: argparse.Namespace) -> int:
    root, config, data_run, nome_file_run, hash_config, output_dir, risultato = _esegui_pipeline_completa(args)
    statistiche = pipeline.costruisci_statistiche(risultato)
    sel = risultato["selezione"]

    anteprima = {
        "data_run": data_run.isoformat(),
        "file_run": nome_file_run,
        "hash_config": hash_config,
        "stato_summary": sel["stato_summary"],
        "statistiche": statistiche,
        "segnalate": [
            {"posizione": i + 1, "id": v["rec"]["id"], "canale": v["rec"]["canale"], "voto": v["voto"], "esito": v["esito"], "stato_segnalazione": v["stato_segnalazione"]}
            for i, v in enumerate(sel["segnalate"])
        ],
        "escluse_per_storico": sel["escluse_storico"],
        "note": risultato["note"],
    }

    testo_out = json.dumps(anteprima, ensure_ascii=False, indent=2, default=str)
    if args.dry_run:
        print(testo_out)
        return 0

    percorso = output_dir / f".selezione_{util.id_run(data_run)}.json"
    util.scrivi_json_atomico(anteprima, percorso)
    print(f"Scritta anteprima {percorso} ({len(sel['segnalate'])} opportunità selezionate su {statistiche['in_classifica']} in classifica)")
    return 0


def cmd_scrivi(args: argparse.Namespace) -> int:
    root, config, data_run, nome_file_run, hash_config, output_dir, risultato = _esegui_pipeline_completa(args)
    statistiche = pipeline.costruisci_statistiche(risultato)
    scartate, monitora = pipeline.costruisci_scartate_e_monitora(risultato["valutazioni"])
    sel = risultato["selezione"]

    meta = report.costruisci_meta(
        data_run=data_run,
        nome_file_run=nome_file_run,
        hash_config=hash_config,
        stato_summary=sel["stato_summary"],
        meta_input=risultato["meta_input"],
        statistiche=statistiche,
        note=risultato["note"] + pipeline.note_selezione(sel, config),
    )
    report_dict = report.costruisci_report(
        meta=meta,
        segnalate=sel["segnalate"],
        giudizi=risultato["giudizi"],
        escluse_storico=sel["escluse_storico"],
        scartate_per_motivo=scartate,
        monitora=monitora,
    )

    errori = report.valida_report(report_dict)
    if errori:
        for e in errori:
            print(f"ERRORE VALIDAZIONE: {e}", file=sys.stderr)
        return 1

    n_opp = len(report_dict["opportunita"])
    if args.dry_run:
        print(json.dumps(report_dict, ensure_ascii=False, indent=2, default=str))
        print(f"\n[dry-run] report NON scritto su disco. {n_opp} opportunità, statistiche: {statistiche}", file=sys.stderr)
        return 0

    percorso_report = report.scrivi_report(report_dict, output_dir, nome_file_run)
    if sel["segnalate"]:
        report.aggiorna_summary(sel["righe_ok"], sel["segnalate"], data_run, nome_file_run, output_dir)
    print(f"Scritto {percorso_report} ({n_opp} opportunità segnalate)")
    return 0


def cmd_tutto(args: argparse.Namespace) -> int:
    esito_prepara = cmd_prepara(args)
    if esito_prepara != 0:
        return esito_prepara
    return cmd_scrivi(args)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Motore della skill prontobox-scouting")
    parser.add_argument("--root", required=True, help="Cartella Pronto_Automation")
    parser.add_argument("--config", default=None, help="Percorso di config.yaml (default: .claude/skills/prontobox-scouting/config.yaml sotto --root)")
    parser.add_argument("--data", default=None, help="Data della run (AAAA-MM-GG), default: oggi (fuso Europe/Rome)")
    parser.add_argument("--dry-run", action="store_true", help="Esegue tutto senza scrivere in OUTPUT/")

    sotto = parser.add_subparsers(dest="comando", required=True)

    p_prepara = sotto.add_parser("prepara", help="Normalizza e applica i filtri deterministici; scrive il file di lavoro")
    p_prepara.set_defaults(func=cmd_prepara)

    p_seleziona = sotto.add_parser("seleziona", help="Calcola scorecard/economico/voto, seleziona le opportunità, scrive un'anteprima")
    p_seleziona.add_argument("--giudizi", default=None, help="File JSON {id: giudizio} da un modello; assente = ripiego euristico")
    p_seleziona.set_defaults(func=cmd_seleziona)

    p_scrivi = sotto.add_parser("scrivi", help="Valida e scrive report_AAAA_MM_GG.json e summary.json")
    p_scrivi.add_argument("--giudizi", default=None, help="File JSON {id: giudizio} da un modello; assente = ripiego euristico")
    p_scrivi.set_defaults(func=cmd_scrivi)

    p_tutto = sotto.add_parser("tutto", help="Incatena prepara + scrivi (comodo per i test)")
    p_tutto.add_argument("--giudizi", default=None, help="File JSON {id: giudizio} da un modello; assente = ripiego euristico")
    p_tutto.set_defaults(func=cmd_tutto)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # ultima rete di sicurezza: mai un traceback nudo verso l'utente del pannello
        print(f"ERRORE: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
