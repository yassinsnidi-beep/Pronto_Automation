#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_fallcoaste_master.py
==============================

Scraper per FallcoAste.it (piattaforma Zucchetti Software Giuridico) — rete
di aste giudiziarie usata da molti Istituti Vendite Giudiziarie italiani
(es. ivgforli.fallcoaste.it, ivgvicenza.fallcoaste.it...). La ricerca su
fallcoaste.it aggrega risultati da tutta la rete. Copre anche ANIVG
(asteivg.com), che si appoggia proprio a questa piattaforma
(asteivg.fallcoaste.it).

*** VALIDATO DAL VIVO *** — categorie, card e campi confermati con richieste
HTTP reali sui dati live del sito.

*** PAGINAZIONE (corretta settembre 2026) ***
Il parametro corretto è `&page=N` (in inglese, NON `&pagina=N` come in un
tentativo precedente, che infatti non produceva risultati diversi). Confermato
dal vivo tramite ispezione con Playwright del bottone "pagina successiva"
(`.table-pagination-container a.arrow.right`): il click naviga a
`...&page=2`, una GET normale gestibile con `requests` senza bisogno di
browser. Lo script ora pagina finché una pagina non restituisce annunci.

Categorie (codici confermati dall'albero categorie del sito):
    - 558 = Immobile industriale (capannoni/opifici)
    - 544 = Immobile commerciale (include 545 Deposito, 551/552/553 Magazzino)

Tecnologia
----------
- `requests` + `BeautifulSoup`, nessun browser necessario.

Installazione
--------------
    pip install requests beautifulsoup4 lxml

Esecuzione
----------
    python scraper_fallcoaste_master.py

Output
------
    scrapers_v2/dataset_fallcoaste_completo.json
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

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

BASE_URL = "https://www.fallcoaste.it"
SEARCH_URL = f"{BASE_URL}/ricerca.html"

# cat0 confermati dall'albero categorie (macro=527 "Beni Immobili"):
#   558 = Immobile industriale (Fabbricati per esigenze industriali, Opifici)
#   544 = Immobile commerciale (include Deposito, Magazzini e locali di deposito, Magazzino)
CATEGORY_CODES: Dict[str, str] = {
    "immobile-industriale": "558",
    "immobile-commerciale": "544",
}

CITIES: List[str] = [
    "Bolzano", "Bologna", "Trento", "Padova", "Milano", "Parma", "Modena",
    "Reggio Emilia", "Brescia", "Monza", "Bergamo", "Verona", "Vicenza",
    "Firenze", "Pavia", "Como", "Piacenza", "Treviso", "Novara", "Cremona",
    "Cesena", "Forlì", "Pisa", "Udine", "Varese", "Ravenna", "Ancona",
    "Sesto San Giovanni", "Cinisello Balsamo", "Busto Arsizio", "Roma",
    "Legnano", "Prato", "Cagliari", "Pistoia", "Lucca",
]

CITY_PROVINCE_ABBR: Dict[str, str] = {
    "Bolzano": "BZ", "Bologna": "BO", "Trento": "TN", "Padova": "PD", "Milano": "MI",
    "Parma": "PR", "Modena": "MO", "Reggio Emilia": "RE", "Brescia": "BS", "Monza": "MB",
    "Bergamo": "BG", "Verona": "VR", "Vicenza": "VI", "Firenze": "FI", "Pavia": "PV",
    "Como": "CO", "Piacenza": "PC", "Treviso": "TV", "Novara": "NO", "Cremona": "CR",
    "Cesena": "FC", "Forlì": "FC", "Pisa": "PI", "Udine": "UD", "Varese": "VA",
    "Ravenna": "RA", "Ancona": "AN", "Sesto San Giovanni": "MI", "Cinisello Balsamo": "MI",
    "Busto Arsizio": "VA", "Roma": "RM", "Legnano": "MI", "Prato": "PO", "Cagliari": "CA",
    "Pistoia": "PT", "Lucca": "LU",
}
TARGET_PROVINCE_ABBRS = set(CITY_PROVINCE_ABBR.values())

OUTPUT_FILE = Path(__file__).resolve().parent.parent / "opportunities" / "INPUT" / "ASTE" / "dataset_fallcoaste_completo.json"
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
DEFAULT_MAX_PAGES = 120  # safety cap per categoria (verificato dal vivo: "immobile-commerciale" arriva a 61 pagine reali)

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
            logging.FileHandler(Path(__file__).resolve().parent / "scraper_fallcoaste.log", encoding="utf-8"),
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


def matches_target_area(text: Optional[str]) -> bool:
    if not text:
        return False
    match = re.search(r"\(([A-Z]{2})\)", text)
    if not match:
        return False
    return match.group(1) in TARGET_PROVINCE_ABBRS


# ============================================================================
# PARSING
# ============================================================================

def parse_listing_cards(html: str) -> List[dict]:
    """
    Estrae gli annunci dalla pagina di ricerca. Ogni card è un
    <article data-auction-id="ID" data-auction-data-inizio="..." data-auction-data-termine="...">
    con titolo/URL in un <a title="..."><h3>...</h3></a>, tribunale/procedura in
    <span class="trib-info">, prezzo in <span class="price-block">, scadenza in
    <div class="cd-block">.
    """
    soup = BeautifulSoup(html, "lxml")
    results: List[dict] = []

    for art in soup.select("article[data-auction-id]"):
        auction_id = art.get("data-auction-id")
        if not auction_id:
            continue

        link = art.select_one("a[href][title]") or art.select_one("h3")
        if link and link.name == "a":
            href = link.get("href", "")
            titolo = link.get("title") or link.get_text(strip=True)
        else:
            a_tag = art.select_one("a[href]")
            href = a_tag.get("href", "") if a_tag else None
            h3 = art.select_one("h3")
            titolo = h3.get_text(strip=True) if h3 else None

        if not href:
            continue

        full_text = art.get_text(" ", strip=True)

        trib_el = art.select_one("span.trib-info, .trib-info")
        trib_text = trib_el.get_text(" ", strip=True) if trib_el else full_text
        tribunale_match = re.search(r"Tribunale di [\w' ]+", trib_text)
        procedura_match = re.search(r"Procedura\s*n\.?\s*[\d/]+", trib_text)

        price_el = art.select_one("span.price-block strong, .price-block .price-element")
        prezzo = price_el.get_text(strip=True) if price_el else None
        if not prezzo:
            prezzo_match = re.search(r"Prezzo base\s*€?:?\s*([\d.]+,\d{2})", full_text)
            prezzo = prezzo_match.group(1) if prezzo_match else None

        cd_el = art.select_one("div.cd-block strong, .cd-block .cd-element")
        data_termine = cd_el.get_text(" ", strip=True) if cd_el else None

        # Il "titolo" (attributo title del link) è in realtà una descrizione estesa che
        # spesso include già la superficie, ma il formato varia: "2.541 mq" oppure
        # "mq. 53,60" (stile atto legale, numero dopo "mq"). Si prova in entrambi gli ordini.
        mq_match = re.search(r"(?:mq\.?\s*([\d.,]+))|(?:([\d.,]+)\s*mq\b)", titolo or "", re.IGNORECASE)
        mq_value = (mq_match.group(1) or mq_match.group(2)) if mq_match else None
        if mq_value:
            mq_value = mq_value.strip(" ,.")

        results.append({
            "id": auction_id,
            "url": href,
            "titolo": titolo,
            "tribunale": tribunale_match.group(0) if tribunale_match else None,
            "procedura": procedura_match.group(0) if procedura_match else None,
            "prezzo": prezzo,
            "superficie_mq": mq_value,
            "data_inizio": art.get("data-auction-data-inizio"),
            "data_termine_asta": data_termine or art.get("data-auction-data-termine"),
            "descrizione_completa": full_text,
        })

    return results


# ============================================================================
# SCRAPER
# ============================================================================

class FallcoasteScraper:
    def __init__(
        self,
        delay_min: float = 1.0,
        delay_max: float = 2.5,
        resume: bool = True,
        output_path: Path = OUTPUT_FILE,
        only_target_area: bool = True,
    ) -> None:
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.resume = resume
        self.output_path = output_path
        self.only_target_area = only_target_area

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
                logging.warning("HTTP %s per %s (tentativo %d/%d)", resp.status_code, url, attempt, MAX_RETRIES)
            except Exception as exc:
                logging.warning("Errore di rete su %s: %s (tentativo %d/%d)", url, exc, attempt, MAX_RETRIES)
            time.sleep(2 ** attempt)
        logging.error("Impossibile recuperare %s dopo %d tentativi.", url, MAX_RETRIES)
        return None

    @staticmethod
    def build_url(cat0_code: str, page: int = 1) -> str:
        filt = quote(f"macro|527^cat0|{cat0_code}", safe="")
        url = f"{SEARCH_URL}?filter={filt}"
        if page > 1:
            url += f"&page={page}"
        return url

    def scrape_category(self, categoria: str, cat0_code: str) -> int:
        logging.info("=== Categoria: %s (cat0=%s) ===", categoria, cat0_code)

        new_count = 0
        page_num = 1
        seen_ids: set = set()
        while page_num <= DEFAULT_MAX_PAGES:
            url = self.build_url(cat0_code, page_num)
            html = self.fetch(url)
            if html is None:
                break

            results = parse_listing_cards(html)
            if not results:
                if page_num == 1:
                    logging.info("Nessun annuncio trovato per la categoria %s.", categoria)
                else:
                    logging.info("Fine paginazione per la categoria %s a pagina %d.", categoria, page_num)
                break

            current_page_ids = {r["id"] for r in results if r.get("id")}
            new_ids_on_page = current_page_ids - seen_ids
            if not new_ids_on_page:
                logging.info(
                    "Pagina %d della categoria %s non contiene annunci nuovi: fine paginazione.",
                    page_num, categoria,
                )
                break
            seen_ids |= current_page_ids

            in_area = [
                r for r in results
                if r.get("id") in new_ids_on_page and (not self.only_target_area or matches_target_area(r["titolo"]))
            ]
            logging.info(
                "Pagina %d, categoria %s: %d annunci trovati, %d nelle 36 città target.",
                page_num, categoria, len(results), len(in_area),
            )

            for item in in_area:
                key = f"fallcoaste_{item['id']}"
                if self.resume and key in self.dataset["listings"]:
                    continue
                self.dataset["listings"][key] = {
                    "source": "fallcoaste.it",
                    "categoria": categoria,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    **item,
                }
                new_count += 1

            page_num += 1
            polite_sleep(self.delay_min, self.delay_max)
        else:
            logging.warning(
                "Categoria %s: raggiunto il tetto di sicurezza di %d pagine mentre erano ancora disponibili "
                "risultati pieni — possibile troncamento, valutare di alzare DEFAULT_MAX_PAGES.",
                categoria, DEFAULT_MAX_PAGES,
            )

        return new_count

    def save(self) -> None:
        self.dataset["generated_at"] = datetime.now(timezone.utc).isoformat()
        self.dataset["total"] = len(self.dataset["listings"])
        atomic_save_json(self.dataset, self.output_path)
        logging.info("Dataset salvato in %s (%d annunci totali).", self.output_path, self.dataset["total"])

    def run(self) -> None:
        for categoria, code in CATEGORY_CODES.items():
            try:
                added = self.scrape_category(categoria, code)
                logging.info("-> %d nuovi annunci per categoria %s.", added, categoria)
            except Exception:
                logging.exception("Errore durante lo scraping della categoria %s", categoria)
            finally:
                self.save()
            polite_sleep(self.delay_min, self.delay_max)

        logging.info("Scraping completato. Totale annunci nel dataset: %d", len(self.dataset["listings"]))


# ============================================================================
# CLI
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scraper per FallcoAste.it (rete IVG / Zucchetti) - aste giudiziarie.")
    parser.add_argument("--delay-min", type=float, default=1.0, help="Delay minimo (secondi) tra le richieste.")
    parser.add_argument("--delay-max", type=float, default=2.5, help="Delay massimo (secondi) tra le richieste.")
    parser.add_argument("--no-resume", action="store_true", help="Ignora il dataset esistente e riparte da zero.")
    parser.add_argument("--no-area-filter", action="store_true", help="Non filtrare per le 36 città target.")
    parser.add_argument("--output", type=str, default=str(OUTPUT_FILE), help="Percorso del file JSON di output.")
    parser.add_argument("--log-level", type=str, default="INFO", help="Livello di log (DEBUG, INFO, WARNING, ERROR).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    scraper = FallcoasteScraper(
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        resume=not args.no_resume,
        output_path=Path(args.output),
        only_target_area=not args.no_area_filter,
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
