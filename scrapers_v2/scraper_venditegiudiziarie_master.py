#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_venditegiudiziarie_master.py
======================================

Scraper per la rete "VGI" — Venditegiudiziarieitalia.it, che condivide lo
STESSO backend dati con Astegiudiziarie.it e GaraVirtuale.it (confermato:
ogni annuncio riporta esplicitamente su quali siti della rete è pubblicato,
tramite il campo "siti" del documento). Un solo script copre quindi 3 dei
siti della lista originale.

*** VALIDATO DAL VIVO — QUALITÀ ECCELLENTE ***
Il sito usa Typesense (motore di ricerca) con una API key di sola-ricerca
esposta pubblicamente nel codice della pagina (prassi normale per i widget
di ricerca client-side, non è una falla). Invece di fare scraping HTML,
questo script interroga DIRETTAMENTE l'API REST di Typesense: risposta in
JSON strutturato, con dati completissimi per ogni annuncio — tribunale,
numero/anno procedura, prezzo base, offerta minima, data e modalità di
vendita, custode/delegato con contatti, dati catastali, descrizione
completa di ogni bene del lotto.

Tecnologia
----------
- `requests` verso l'endpoint Typesense (nessun HTML da interpretare):
    POST https://giu08flbkyo31djqp.a1.typesense.net/multi_search
- Ricerca testuale (query_by su descrizione beni e titolo annuncio) con le
  parole chiave "capannone", "magazzino", "deposito", poi filtro lato
  script sulle 36 città target (il campo "citta" del documento ha formato
  "Regione > Provincia > Comune").

*** NOTA SULLA CHIAVE API ***
La chiave qui sotto è stata letta pubblicamente dal codice sorgente della
pagina di ricerca del sito (variabile JS `typesenseApiKey`), esattamente
come la userebbe un browser normale che carica la pagina. Se in futuro il
sito la cambia, va semplicemente aggiornata qui (bastano gli strumenti di
sviluppo del browser, scheda "Rete", cercando "typesense.net").

Installazione
--------------
    pip install requests

Esecuzione
----------
    python scraper_venditegiudiziarie_master.py

Output
------
    scrapers_v2/dataset_venditegiudiziarie_completo.json
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    print("ERRORE: la libreria 'requests' non è installata.\nInstallala con:  pip install requests", file=sys.stderr)
    sys.exit(1)


# ============================================================================
# CONFIGURAZIONE
# ============================================================================

TYPESENSE_HOST = "giu08flbkyo31djqp.a1.typesense.net"
TYPESENSE_API_KEY = "NXeB3vnfapEy7UnOz3j5RU9JcmW09PI7"  # chiave pubblica di sola ricerca, vedi nota sopra
TYPESENSE_COLLECTION = "vgi_prod_inserzioni"
SEARCH_URL = f"https://{TYPESENSE_HOST}/multi_search?x-typesense-api-key={TYPESENSE_API_KEY}"

QUERY_BY = "modalitaVendita,urlInserzioneH1,descrizioneBeni"
KEYWORDS = ["capannone", "magazzino", "deposito"]

CITIES: List[str] = [
    "Bolzano", "Bologna", "Trento", "Padova", "Milano", "Parma", "Modena",
    "Reggio Emilia", "Brescia", "Monza", "Bergamo", "Verona", "Vicenza",
    "Firenze", "Pavia", "Como", "Piacenza", "Treviso", "Novara", "Cremona",
    "Cesena", "Forlì", "Pisa", "Udine", "Varese", "Ravenna", "Ancona",
    "Sesto San Giovanni", "Cinisello Balsamo", "Busto Arsizio", "Roma",
    "Legnano", "Prato", "Cagliari", "Pistoia", "Lucca",
]
CITIES_LOWER = {c.lower() for c in CITIES}

OUTPUT_FILE = Path(__file__).resolve().parent.parent / "opportunities" / "INPUT" / "ASTE" / "dataset_venditegiudiziarie_completo.json"
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
PER_PAGE = 50
DEFAULT_MAX_PAGES = 20

HEADERS = {"Content-Type": "application/json"}


# ============================================================================
# UTILITY
# ============================================================================

def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(Path(__file__).resolve().parent / "scraper_venditegiudiziarie.log", encoding="utf-8"),
        ],
    )


def polite_sleep(delay_min: float, delay_max: float) -> None:
    time.sleep(random.uniform(delay_min, delay_max))


def atomic_save_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def load_existing_dataset(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = json.load(f)
            if isinstance(content, dict) and "listings" in content:
                return content
        except Exception:
            logging.warning("Impossibile leggere il dataset esistente: si riparte da zero.")
    return {"generated_at": None, "total": 0, "listings": {}}


def matches_target_city(citta_field: Optional[str]) -> Optional[str]:
    """Il campo 'citta' ha formato 'Regione > Provincia > Comune': cerca una delle 36 città target."""
    if not citta_field:
        return None
    parts = [p.strip() for p in citta_field.split(">")]
    for part in parts:
        for city in CITIES:
            if city.lower() == part.lower():
                return city
    return None


# ============================================================================
# CLIENT TYPESENSE
# ============================================================================

class VgiScraper:
    def __init__(
        self,
        delay_min: float = 1.0,
        delay_max: float = 2.0,
        max_pages: int = DEFAULT_MAX_PAGES,
        resume: bool = True,
        output_path: Path = OUTPUT_FILE,
        only_target_city: bool = True,
    ) -> None:
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_pages = max_pages
        self.resume = resume
        self.output_path = output_path
        self.only_target_city = only_target_city

        self.session = requests.Session()
        self.session.headers.update(HEADERS)

        self.dataset: Dict[str, Any] = (
            load_existing_dataset(output_path) if resume else {"generated_at": None, "total": 0, "listings": {}}
        )

    def search(self, keyword: str, page: int) -> Optional[dict]:
        body = {
            "searches": [{
                "collection": TYPESENSE_COLLECTION,
                "q": keyword,
                "query_by": QUERY_BY,
                "filter_by": "isClosed:=[false]",
                "page": page,
                "per_page": PER_PAGE,
            }]
        }
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.post(SEARCH_URL, json=body, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("results", [{}])[0]
                logging.warning("HTTP %s dalla ricerca Typesense (tentativo %d/%d)", resp.status_code, attempt, MAX_RETRIES)
            except Exception as exc:
                logging.warning("Errore di rete: %s (tentativo %d/%d)", exc, attempt, MAX_RETRIES)
            time.sleep(2 ** attempt)
        logging.error("Impossibile completare la ricerca Typesense per %r pagina %d.", keyword, page)
        return None

    @staticmethod
    def extract_fields(doc: dict) -> dict:
        vendita = doc.get("datiVendita", {}) if isinstance(doc.get("datiVendita"), dict) else {}
        procedura = {}
        try:
            procedura = doc["datiProcedura"]["proceduraGiudiziaria"]
        except (KeyError, TypeError):
            pass

        descrizione_beni = doc.get("descrizioneBeni")
        if isinstance(descrizione_beni, list):
            descrizione_completa = " | ".join(descrizione_beni)
        else:
            descrizione_completa = descrizione_beni

        url_info = doc.get("urlInserzione", {}) if isinstance(doc.get("urlInserzione"), dict) else {}
        url_path = url_info.get("url", "")

        return {
            "id": doc.get("idInserzioneEspVendita") or doc.get("id"),
            "titolo": doc.get("urlInserzioneH1"),
            "url": f"https://www.venditegiudiziarieitalia.it{url_path}" if url_path else None,
            "citta_completa": doc.get("citta"),
            "regione": doc.get("regione"),
            "tribunale": doc.get("tribunale"),
            "numero_procedura": procedura.get("numeroProcedura"),
            "anno_procedura": procedura.get("annoProcedura"),
            "rito": procedura.get("rito"),
            "categoria": doc.get("category"),
            "sottocategoria": doc.get("subcategory"),
            "prezzo_base": vendita.get("prezzoValoreBase"),
            "offerta_minima": vendita.get("offertaMinima"),
            "rialzo_minimo": vendita.get("rialzoMinimo"),
            "modalita_vendita": doc.get("modalitaVendita"),
            "data_vendita": vendita.get("dataOraVendita"),
            "termine_presentazione_offerte": vendita.get("terminePresentazioneOfferte"),
            "descrizione_completa": descrizione_completa,
            "pubblicato_anche_su": [s.get("url") for s in doc.get("siti", []) if isinstance(s, dict) and s.get("url")],
        }

    def scrape_keyword(self, keyword: str) -> int:
        logging.info("=== Parola chiave: %s ===", keyword)
        new_count = 0
        page = 1
        while page <= self.max_pages:
            result = self.search(keyword, page)
            if result is None:
                break

            hits = result.get("hits", [])
            if not hits:
                if page == 1:
                    logging.info("Nessun annuncio trovato per %r.", keyword)
                else:
                    logging.info("Fine paginazione per %r a pagina %d.", keyword, page)
                break

            in_area = []
            for hit in hits:
                doc = hit.get("document", {})
                city = matches_target_city(doc.get("citta"))
                if self.only_target_city and not city:
                    continue
                fields = self.extract_fields(doc)
                fields["citta_target"] = city
                in_area.append(fields)

            logging.info(
                "Pagina %d: %d risultati totali (found=%s), %d nelle 36 città target.",
                page, len(hits), result.get("found"), len(in_area),
            )

            for item in in_area:
                if not item["id"]:
                    continue
                key = f"vgi_{item['id']}"
                if self.resume and key in self.dataset["listings"]:
                    continue
                self.dataset["listings"][key] = {
                    "source": "venditegiudiziarieitalia.it (rete: anche astegiudiziarie.it, garavirtuale.it)",
                    "keyword_ricerca": keyword,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    **item,
                }
                new_count += 1

            if len(hits) < PER_PAGE:
                break  # ultima pagina

            page += 1
            polite_sleep(self.delay_min, self.delay_max)

        return new_count

    def save(self) -> None:
        self.dataset["generated_at"] = datetime.now(timezone.utc).isoformat()
        self.dataset["total"] = len(self.dataset["listings"])
        atomic_save_json(self.dataset, self.output_path)
        logging.info("Dataset salvato in %s (%d annunci totali).", self.output_path, self.dataset["total"])

    def run(self) -> None:
        for keyword in KEYWORDS:
            try:
                added = self.scrape_keyword(keyword)
                logging.info("-> %d nuovi annunci per %r.", added, keyword)
            except Exception:
                logging.exception("Errore durante la ricerca di %r", keyword)
            finally:
                self.save()
            polite_sleep(self.delay_min, self.delay_max)

        logging.info("Scraping completato. Totale annunci nel dataset: %d", len(self.dataset["listings"]))


# ============================================================================
# CLI
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scraper per la rete VGI (Venditegiudiziarieitalia.it / Astegiudiziarie.it / GaraVirtuale.it)."
    )
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Numero massimo di pagine per parola chiave.")
    parser.add_argument("--delay-min", type=float, default=1.0, help="Delay minimo (secondi) tra le richieste.")
    parser.add_argument("--delay-max", type=float, default=2.0, help="Delay massimo (secondi) tra le richieste.")
    parser.add_argument("--no-resume", action="store_true", help="Ignora il dataset esistente e riparte da zero.")
    parser.add_argument("--no-city-filter", action="store_true", help="Non filtrare per le 36 città target.")
    parser.add_argument("--output", type=str, default=str(OUTPUT_FILE), help="Percorso del file JSON di output.")
    parser.add_argument("--log-level", type=str, default="INFO", help="Livello di log (DEBUG, INFO, WARNING, ERROR).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    scraper = VgiScraper(
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        max_pages=args.max_pages,
        resume=not args.no_resume,
        output_path=Path(args.output),
        only_target_city=not args.no_city_filter,
    )

    try:
        scraper.run()
    except KeyboardInterrupt:
        logging.warning("Interruzione manuale ricevuta: salvataggio dello stato parziale...")
        scraper.save()
    except Exception:
        logging.exception("Errore fatale durante lo scraping.")
        scraper.save()
        sys.exit(1)


if __name__ == "__main__":
    main()
