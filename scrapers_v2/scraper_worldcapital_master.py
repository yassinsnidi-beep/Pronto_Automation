#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_worldcapital_master.py
================================

Scraper per World Capital Group (worldcapital.it) — agenzia immobiliare
specializzata in immobili industriali/logistici (NON aste giudiziarie: è
compravendita "normale", come Immobiliare/Idealista). Estrae capannoni IN
VENDITA (esclude affitto), filtrando per le 36 città target.

*** VALIDATO DAL VIVO *** — struttura card e paginazione confermate con
richieste HTTP reali (tema WordPress "RealHomes").

*** CAMPO PREZZO NON DISPONIBILE IN LISTA ***
A differenza degli altri portali, il prezzo non compare nella pagina di
elenco (probabilmente "prezzo su richiesta" per molti immobili industriali di
questa fascia, o mostrato solo nella scheda del singolo annuncio). Il campo
"prezzo" resta quindi `null`: recuperarlo richiederebbe la visita alla scheda
di ogni annuncio, che va contro l'obiettivo di velocità del progetto.

Tecnologia
----------
- `requests` + `BeautifulSoup`, nessun browser necessario.
- Categoria unica pertinente: https://www.worldcapital.it/vendita-affitto-capannoni/
  con paginazione /page/N/. Ogni annuncio è una card <article class="rh_list_card">
  con `data-rh-id`, stato Vendita/Affitto in `.rh_label__status`, e titolo
  completo (tipo, città, mq, codice riferimento) nell'attributo aria-label
  del link principale.

Installazione
--------------
    pip install requests beautifulsoup4 lxml

Esecuzione
----------
    python scraper_worldcapital_master.py

Output
------
    scrapers_v2/dataset_worldcapital_completo.json
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    print("ERRORE: la libreria 'requests' non è installata.\nInstallala con:  pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print(
        "ERRORE: la libreria 'beautifulsoup4' non è installata.\n"
        "Installala con:  pip install beautifulsoup4 lxml",
        file=sys.stderr,
    )
    sys.exit(1)


# ============================================================================
# CONFIGURAZIONE
# ============================================================================

BASE_URL = "https://www.worldcapital.it"
CATEGORY_URL = f"{BASE_URL}/vendita-affitto-capannoni/"

CITIES: List[str] = [
    "Bolzano", "Bologna", "Trento", "Padova", "Milano", "Parma", "Modena",
    "Reggio Emilia", "Brescia", "Monza", "Bergamo", "Verona", "Vicenza",
    "Firenze", "Pavia", "Como", "Piacenza", "Treviso", "Novara", "Cremona",
    "Cesena", "Forlì", "Pisa", "Udine", "Varese", "Ravenna", "Ancona",
    "Sesto San Giovanni", "Cinisello Balsamo", "Busto Arsizio", "Roma",
    "Legnano", "Prato", "Cagliari", "Pistoia", "Lucca",
]

OUTPUT_FILE = Path(__file__).resolve().parent.parent / "opportunities" / "INPUT" / "ASTE" / "dataset_worldcapital_completo.json"
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
DEFAULT_MAX_PAGES = 50  # safety cap (verificato dal vivo: la categoria arriva a 30 pagine reali, 598 annunci vendita+affitto)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9",
}


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
            logging.FileHandler(Path(__file__).resolve().parent / "scraper_worldcapital.log", encoding="utf-8"),
        ],
    )


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_str = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_str = ascii_str.lower().strip()
    ascii_str = re.sub(r"[^a-z0-9]+", "-", ascii_str)
    return ascii_str.strip("-")


CITY_SLUGS = {slugify(c): c for c in CITIES}
# Variante grafica comune per "Reggio Emilia" nei siti immobiliari.
CITY_SLUGS["reggio-nellemilia"] = "Reggio Emilia"


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


def find_target_city(url_slug: str) -> Optional[str]:
    """Cerca lo slug di una delle 36 città target dentro lo slug URL dell'annuncio."""
    for slug, city in CITY_SLUGS.items():
        if slug in url_slug:
            return city
    return None


# ============================================================================
# PARSING
# ============================================================================

def parse_listing_cards(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "lxml")
    results: List[dict] = []

    for art in soup.select("article.rh_list_card"):
        rh_id = art.get("data-rh-id")
        link = art.select_one("a[aria-label][href]")
        if not link or not rh_id:
            continue

        href = link.get("href", "")
        aria_label = link.get("aria-label", "")
        # Ripulisce eventuali tag HTML residui nell'aria-label (es. "<br><h3>...</h3>").
        titolo = re.sub(r"<[^>]+>", " ", aria_label)
        titolo = re.sub(r"\s+", " ", titolo).strip()

        status_el = art.select_one(".rh_label__status .rh_label__wrap")
        status = status_el.get_text(strip=True) if status_el else None

        mq_match = re.search(r"([\d.]+)\s*mq", titolo, re.IGNORECASE)
        rif_match = re.search(r"rif\.?\s*([\w\-]+)", titolo, re.IGNORECASE)

        url_slug = href.rstrip("/").rsplit("/", 1)[-1].lower()
        citta = find_target_city(url_slug)

        results.append({
            "id": rh_id,
            "url": href,
            "titolo": titolo,
            "stato": status,
            "in_vendita": bool(status and "vendita" in status.lower()),
            "superficie_mq": mq_match.group(1) if mq_match else None,
            "riferimento": rif_match.group(1) if rif_match else None,
            "citta": citta,
            "prezzo": None,  # non disponibile in lista, vedi nota nel docstring
            "descrizione_completa": titolo,
        })

    return results


def has_next_page(html: str, current_page: int) -> bool:
    return bool(re.search(rf"page/{current_page + 1}/", html))


def parse_detail_fields(html: str) -> dict:
    """
    Estrae prezzo e superficie dalla scheda del singolo annuncio (non
    disponibili nella pagina di elenco): <p class="wcg-detail-item">
    <strong>Prezzo:</strong> € 4.875.000</p> e analogo per "Dimensione".
    """
    soup = BeautifulSoup(html, "lxml")
    prezzo = None
    superficie_mq = None

    for p in soup.select("p.wcg-detail-item"):
        label_el = p.select_one("strong")
        label = label_el.get_text(strip=True).lower() if label_el else ""
        value = p.get_text(" ", strip=True)
        if label_el:
            value = value.replace(label_el.get_text(strip=True), "", 1).strip()

        if "prezzo" in label:
            prezzo_match = re.search(r"([\d.]+)", value)
            prezzo = prezzo_match.group(1) if prezzo_match else None
        elif "dimensione" in label:
            mq_match = re.search(r"([\d.]+)\s*mq", value, re.IGNORECASE)
            superficie_mq = mq_match.group(1) if mq_match else None

    return {"prezzo": prezzo, "superficie_mq_scheda": superficie_mq}


# ============================================================================
# SCRAPER
# ============================================================================

class WorldCapitalScraper:
    def __init__(
        self,
        delay_min: float = 1.0,
        delay_max: float = 2.5,
        max_pages: int = DEFAULT_MAX_PAGES,
        resume: bool = True,
        output_path: Path = OUTPUT_FILE,
        only_target_city: bool = True,
        only_vendita: bool = True,
        fetch_price: bool = True,
        detail_delay_min: float = 0.8,
        detail_delay_max: float = 1.8,
    ) -> None:
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_pages = max_pages
        self.resume = resume
        self.output_path = output_path
        self.only_target_city = only_target_city
        self.only_vendita = only_vendita
        self.fetch_price = fetch_price
        self.detail_delay_min = detail_delay_min
        self.detail_delay_max = detail_delay_max

        self.session = requests.Session()
        self.session.headers.update(HEADERS)

        self.dataset: Dict[str, Any] = (
            load_existing_dataset(output_path) if resume else {"generated_at": None, "total": 0, "listings": {}}
        )

    def fetch(self, url: str) -> Optional[str]:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 404:
                    return None
                logging.warning("HTTP %s per %s (tentativo %d/%d)", resp.status_code, url, attempt, MAX_RETRIES)
            except Exception as exc:
                logging.warning("Errore di rete su %s: %s (tentativo %d/%d)", url, exc, attempt, MAX_RETRIES)
            time.sleep(2 ** attempt)
        logging.error("Impossibile recuperare %s dopo %d tentativi.", url, MAX_RETRIES)
        return None

    def run(self) -> None:
        page = 1
        seen_ids: set = set()
        while page <= self.max_pages:
            url = CATEGORY_URL if page == 1 else f"{CATEGORY_URL}page/{page}/"
            html = self.fetch(url)
            if html is None:
                break

            results = parse_listing_cards(html)
            if not results:
                if page == 1:
                    logging.info("Nessun annuncio trovato.")
                else:
                    logging.info("Fine paginazione a pagina %d.", page)
                break

            current_ids = {r["id"] for r in results}
            new_ids = current_ids - seen_ids
            if not new_ids:
                logging.info("Pagina %d senza annunci nuovi: fine paginazione.", page)
                break
            seen_ids |= current_ids

            filtered = [
                r for r in results
                if r["id"] in new_ids
                and (not self.only_vendita or r["in_vendita"])
                and (not self.only_target_city or r["citta"])
            ]
            logging.info(
                "Pagina %d: %d annunci totali, %d in vendita nelle 36 città target.",
                page, len(results), len(filtered),
            )

            for item in filtered:
                key = f"worldcapital_{item['id']}"
                if self.resume and key in self.dataset["listings"]:
                    continue

                # Il prezzo non è nella pagina di elenco: si visita la scheda,
                # ma SOLO per gli annunci già filtrati (poche unità per run),
                # non per tutti gli annunci della categoria — resta veloce.
                if self.fetch_price:
                    polite_sleep(self.detail_delay_min, self.detail_delay_max)
                    detail_html = self.fetch(item["url"])
                    if detail_html:
                        detail_fields = parse_detail_fields(detail_html)
                        item["prezzo"] = detail_fields["prezzo"]
                        if not item.get("superficie_mq"):
                            item["superficie_mq"] = detail_fields["superficie_mq_scheda"]

                self.dataset["listings"][key] = {
                    "source": "worldcapital.it",
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    **item,
                }

            if not has_next_page(html, page):
                break

            page += 1
            polite_sleep(self.delay_min, self.delay_max)
        else:
            logging.warning(
                "Raggiunto il tetto di sicurezza di %d pagine mentre erano ancora disponibili risultati "
                "pieni — possibile troncamento, valutare di alzare DEFAULT_MAX_PAGES.",
                self.max_pages,
            )

        self.save()
        logging.info("Scraping completato. Totale annunci nel dataset: %d", len(self.dataset["listings"]))

    def save(self) -> None:
        self.dataset["generated_at"] = datetime.now(timezone.utc).isoformat()
        self.dataset["total"] = len(self.dataset["listings"])
        atomic_save_json(self.dataset, self.output_path)
        logging.info("Dataset salvato in %s (%d annunci totali).", self.output_path, self.dataset["total"])


# ============================================================================
# CLI
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scraper per WorldCapital.it - capannoni in vendita.")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Numero massimo di pagine.")
    parser.add_argument("--delay-min", type=float, default=1.0, help="Delay minimo (secondi) tra le richieste.")
    parser.add_argument("--delay-max", type=float, default=2.5, help="Delay massimo (secondi) tra le richieste.")
    parser.add_argument("--no-resume", action="store_true", help="Ignora il dataset esistente e riparte da zero.")
    parser.add_argument("--no-city-filter", action="store_true", help="Non filtrare per le 36 città target.")
    parser.add_argument("--include-affitto", action="store_true", help="Includi anche gli annunci in affitto.")
    parser.add_argument(
        "--no-price", action="store_true",
        help="Non visitare la scheda del singolo annuncio per il prezzo (più veloce, ma prezzo sempre null).",
    )
    parser.add_argument("--output", type=str, default=str(OUTPUT_FILE), help="Percorso del file JSON di output.")
    parser.add_argument("--log-level", type=str, default="INFO", help="Livello di log (DEBUG, INFO, WARNING, ERROR).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    scraper = WorldCapitalScraper(
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        max_pages=args.max_pages,
        resume=not args.no_resume,
        output_path=Path(args.output),
        only_target_city=not args.no_city_filter,
        only_vendita=not args.include_affitto,
        fetch_price=not args.no_price,
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
