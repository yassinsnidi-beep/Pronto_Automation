#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_immobiliare_master.py
==============================

Scraper "stealth" per Immobiliare.it — estrae annunci di Capannoni, Magazzini
e Depositi in vendita su 36 città italiane target.

Tecnologia
----------
Immobiliare.it è protetto da DataDome, che oltre al fingerprint TLS richiede
l'esecuzione di una vera challenge JavaScript per rilasciare il cookie di
validazione, e lega quel cookie all'impronta TLS/HTTP esatta del client che
l'ha ottenuto. Un client HTTP puro (es. curl_cffi con TLS impersonation) può
risolvere/ricevere il cookie tramite un browser reale, ma se poi lo si passa a
un client diverso (con un'impronta TLS anche solo leggermente diversa, es. una
versione di Chrome diversa da quella realmente installata) DataDome rifiuta
la richiesta: il cookie è "legato" al device che l'ha generato.

Per questo motivo lo scraper usa **Playwright con un vero Chrome persistente**
per l'intero ciclo di vita della richiesta (navigazione delle pagine di
ricerca *e* recupero dei dati di arricchimento), esattamente come
`scraper_idealista_master.py`:

- `launch_persistent_context(channel="chrome")`: usa il binario Chrome reale
  installato sul sistema, con un profilo persistente su disco
  ("./profilo_chrome_immobiliare") che mantiene i cookie DataDome tra una
  run e l'altra.
- Le pagine di ricerca vengono caricate con `page.goto(...)` e i dati vengono
  estratti dal JSON `__NEXT_DATA__` incorporato nell'HTML (Immobiliare è un
  sito Next.js).
- L'arricchimento di ogni annuncio (descrizione completa, dati energetici,
  costi) viene ottenuto interrogando l'endpoint interno
  `_next/data/{buildId}/annunci/{id}.json` con l'header `x-nextjs-data: 1|,
  ma la richiesta viene eseguita con un `fetch()` lanciato **dentro** la
  pagina del browser (`page.evaluate`) invece che con un client HTTP esterno:
  in questo modo la richiesta condivide sempre la stessa identica sessione
  TLS/cookie del browser che ha superato la challenge di DataDome, senza mai
  incorrere nel problema del "cookie legato a un altro device".

Installazione
--------------
    pip install playwright
    playwright install chrome

Esecuzione
----------
    python scraper_immobiliare_master.py
    python scraper_immobiliare_master.py --max-pages 5 --cities Milano,Roma
    python scraper_immobiliare_master.py --no-resume --delay-min 3 --delay-max 6

    # Prima esecuzione (o se DataDome mostra un captcha "duro"): finestra
    # visibile, per poter risolvere manualmente un eventuale controllo.
    python scraper_immobiliare_master.py --headless=false

Output
------
    scrapers_v2/dataset_immobiliare_completo.json

Note importanti
----------------
Immobiliare.it è un sito Next.js e la struttura interna del JSON
(`__NEXT_DATA__` e le risposte di `_next/data`) può cambiare nel tempo. Per
questo motivo l'estrazione dei singoli campi usa funzioni di ricerca
ricorsiva ("deep_find") resilienti a piccoli cambiamenti di schema, e il
JSON grezzo di ogni annuncio viene comunque sempre salvato integralmente
(`raw_search` e `raw_detail`) così da non perdere mai informazione anche se
il parsing "comodo" dei singoli campi dovesse fallire.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from playwright.sync_api import (
        sync_playwright,
        Page,
        BrowserContext,
        TimeoutError as PWTimeoutError,
    )
except ImportError:
    print(
        "ERRORE: la libreria 'playwright' non è installata.\n"
        "Installala con:  pip install playwright\n"
        "e poi:            playwright install chrome",
        file=sys.stderr,
    )
    sys.exit(1)


# ============================================================================
# CONFIGURAZIONE
# ============================================================================

BASE_URL = "https://www.immobiliare.it"

# Le 36 città target del progetto.
CITIES: List[str] = [
    "Bolzano", "Bologna", "Trento", "Padova", "Milano", "Parma", "Modena",
    "Reggio Emilia", "Brescia", "Monza", "Bergamo", "Verona", "Vicenza",
    "Firenze", "Pavia", "Como", "Piacenza", "Treviso", "Novara", "Cremona",
    "Cesena", "Forlì", "Pisa", "Udine", "Varese", "Ravenna", "Ancona",
    "Sesto San Giovanni", "Cinisello Balsamo", "Busto Arsizio", "Roma",
    "Legnano", "Prato", "Cagliari", "Pistoia", "Lucca",
]

# Categorie richieste: Capannoni e Magazzini/Depositi in vendita.
CATEGORIES: List[str] = ["vendita-capannoni", "vendita-magazzini"]

OUTPUT_FILE = Path(__file__).resolve().parent / "dataset_immobiliare_completo.json"
RECENT_OUTPUT_FILE = Path(__file__).resolve().parent / "dataset_immobiliare_recenti.json"
PROFILE_DIR = Path(__file__).resolve().parent / "profilo_chrome_immobiliare"

NAV_TIMEOUT_MS = 45_000
MAX_RETRIES = 3
DEFAULT_MAX_PAGES = 25  # safety cap per città/categoria
SOGLIA_BLOCCHI_CONSECUTIVI = 3  # città/categorie bloccate fin dalla 1a pagina, di fila, prima di fermare l'intera run
MANUAL_SOLVE_WAIT_S = 20  # tempo concesso per risolvere un eventuale captcha a mano
DEFAULT_STALENESS_DAYS = 14  # soglia "due settimane" per la modalità --recent-only
DEFAULT_STALE_STREAK = 3  # annunci vecchi consecutivi richiesti prima di fermarsi (assorbe outlier "supervetrina")

# Query di ordinamento "più recenti" (per data di modifica/aggiornamento annuncio),
# confermata manualmente su immobiliare.it dall'utente.
RECENT_SORT_QUERY = "criterio=data&ordine=desc"

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)

BLOCKED_MARKERS = [
    "captcha-delivery.com",
    "please enable js",
    "unusual traffic",
    "traffico insolito",
]

# Script eseguito DENTRO il browser (stesso identico contesto/cookie/TLS della
# pagina che ha già superato la challenge DataDome) per chiamare l'endpoint
# di dati di Next.js senza incorrere nel problema del "cookie legato al device".
FETCH_JSON_JS = """
async (url) => {
    try {
        const resp = await fetch(url, { headers: { 'x-nextjs-data': '1' } });
        let body = null;
        try { body = await resp.json(); } catch (e) { body = null; }
        return { status: resp.status, body: body };
    } catch (e) {
        return { status: 0, body: null };
    }
}
"""


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
            logging.FileHandler(
                Path(__file__).resolve().parent / "scraper_immobiliare.log",
                encoding="utf-8",
            ),
        ],
    )


def slugify(city: str) -> str:
    """
    Converte un nome di città nello slug usato nelle URL di Immobiliare.it.
    Esempi: 'Reggio Emilia' -> 'reggio-emilia', 'Forlì' -> 'forli',
            'Sesto San Giovanni' -> 'sesto-san-giovanni'.
    """
    normalized = unicodedata.normalize("NFKD", city)
    ascii_str = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_str = ascii_str.lower().strip()
    ascii_str = re.sub(r"[^a-z0-9]+", "-", ascii_str)
    return ascii_str.strip("-")


def str2bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y", "si", "sì")


def polite_sleep(delay_min: float, delay_max: float) -> None:
    time.sleep(random.uniform(delay_min, delay_max))


def deep_find_all(obj: Any, key: str) -> List[Any]:
    """Cerca ricorsivamente tutte le occorrenze di `key` in una struttura JSON annidata."""
    results: List[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                results.append(v)
            results.extend(deep_find_all(v, key))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(deep_find_all(item, key))
    return results


def deep_find_first(obj: Any, key: str) -> Optional[Any]:
    found = deep_find_all(obj, key)
    return found[0] if found else None


def atomic_save_json(data: Any, path: Path) -> None:
    """Scrive il JSON su un file temporaneo e poi lo rinomina, per evitare file corrotti in caso di crash."""
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


def is_blocked(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in BLOCKED_MARKERS)


def load_baseline_scraped_at(path: Path) -> Dict[str, datetime]:
    """
    Legge un dataset già generato in precedenza (es. dataset_immobiliare_completo.json)
    e ritorna una mappa {chiave_annuncio: scraped_at} usata in modalità --recent-only
    per capire da quanto tempo NON aggiorniamo un dato annuncio.
    """
    baseline: Dict[str, datetime] = {}
    if not path.exists():
        logging.warning("File di baseline %s non trovato: nessun confronto scraped_at disponibile.", path)
        return baseline
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = json.load(f)
    except Exception:
        logging.warning("Impossibile leggere il file di baseline %s.", path)
        return baseline

    for key, item in (content.get("listings") or {}).items():
        raw_scraped_at = item.get("scraped_at") if isinstance(item, dict) else None
        if not raw_scraped_at:
            continue
        try:
            baseline[key] = datetime.fromisoformat(raw_scraped_at)
        except ValueError:
            continue
    logging.info("Baseline caricata da %s: %d annunci con scraped_at.", path, len(baseline))
    return baseline


# ============================================================================
# SCRAPER
# ============================================================================

class ImmobiliareScraper:
    def __init__(
        self,
        headless: bool = True,
        delay_min: float = 1.5,
        delay_max: float = 3.5,
        enrich_delay_min: float = 0.4,
        enrich_delay_max: float = 1.0,
        max_pages: int = DEFAULT_MAX_PAGES,
        resume: bool = True,
        output_path: Path = OUTPUT_FILE,
        profile_dir: Path = PROFILE_DIR,
        recent_only: bool = False,
        staleness_days: int = DEFAULT_STALENESS_DAYS,
        stale_streak: int = DEFAULT_STALE_STREAK,
        baseline_path: Optional[Path] = None,
    ) -> None:
        self.headless = headless
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.enrich_delay_min = enrich_delay_min
        self.enrich_delay_max = enrich_delay_max
        self.max_pages = max_pages
        self.resume = resume
        self.output_path = output_path
        self.profile_dir = profile_dir
        self.build_id: Optional[str] = None

        self.recent_only = recent_only
        self.staleness_days = staleness_days
        self.stale_streak = stale_streak
        self.baseline_map: Dict[str, datetime] = (
            load_baseline_scraped_at(baseline_path or OUTPUT_FILE) if recent_only else {}
        )
        # Diagnostica non bloccante: se l'ordinamento "più recenti" non sembra
        # davvero decrescente per data, lo segnaliamo nei log senza fermare lo
        # scraping (il parametro RECENT_SORT_QUERY andrebbe rivisto).
        self._last_updated_dt: Optional[datetime] = None
        self._sort_order_warning_logged = False
        self._pagina1_bloccata = False

        self.dataset: Dict[str, Any] = (
            load_existing_dataset(output_path) if resume else {"generated_at": None, "total": 0, "listings": {}}
        )

        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    # -- Ciclo di vita del browser --------------------------------------------

    def start(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()

        # Profilo Chrome persistente: mantiene il cookie DataDome tra una run
        # e l'altra, così non serve risolvere una nuova challenge ogni volta.
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            channel="chrome",  # usa il Chrome reale installato nel sistema
            headless=self.headless,
            locale="it-IT",
            timezone_id="Europe/Rome",
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        self.page = self._context.new_page()
        logging.info("Browser Chrome persistente avviato (headless=%s, profilo=%s).", self.headless, self.profile_dir)

    def stop(self) -> None:
        if self._context is not None:
            self._context.close()
        if self._playwright is not None:
            self._playwright.stop()

    # -- Navigazione con gestione blocco DataDome ------------------------------

    def goto(self, url: str) -> Optional[str]:
        """
        Naviga verso `url` con il browser persistente e ritorna l'HTML della
        pagina, gestendo retry ed eventuale blocco/verifica DataDome.
        """
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self.page.goto(url, wait_until="domcontentloaded")
            except PWTimeoutError:
                logging.warning("Timeout di navigazione su %s (tentativo %d/%d)", url, attempt, MAX_RETRIES)
                polite_sleep(self.delay_min, self.delay_max)
                continue
            except Exception as exc:
                logging.warning("Errore di navigazione su %s: %s (tentativo %d/%d)", url, exc, attempt, MAX_RETRIES)
                polite_sleep(self.delay_min, self.delay_max)
                continue

            polite_sleep(1.0, 2.0)  # piccolo respiro per il rendering/JS
            html = self.page.content()

            if is_blocked(html):
                if not self.headless:
                    logging.warning(
                        "DataDome richiede una verifica su %s: risolvila manualmente nella finestra "
                        "del browser. Attendo %ds...",
                        url, MANUAL_SOLVE_WAIT_S,
                    )
                    self.page.wait_for_timeout(MANUAL_SOLVE_WAIT_S * 1000)
                    html = self.page.content()
                    if not is_blocked(html):
                        return html
                logging.warning("Blocco DataDome confermato su %s (tentativo %d/%d).", url, attempt, MAX_RETRIES)
                polite_sleep(self.delay_min * 2, self.delay_max * 2)
                continue

            return html

        logging.error("Impossibile superare il blocco anti-bot per %s dopo %d tentativi.", url, MAX_RETRIES)
        return None

    # -- buildId & ricerca ------------------------------------------------------

    @staticmethod
    def build_search_url(city_slug: str, category: str, page: int, sort_recent: bool = False) -> str:
        base = f"{BASE_URL}/{category}/{city_slug}/"
        params = []
        if page > 1:
            params.append(f"pag={page}")
        if sort_recent:
            params.append(RECENT_SORT_QUERY)
        if params:
            return f"{base}?{'&'.join(params)}"
        return base

    @staticmethod
    def extract_listings_from_next_data(next_data: dict) -> List[dict]:
        """
        Estrae la lista di annunci dal JSON __NEXT_DATA__ della pagina di ricerca.
        Confermato dal vivo (settembre 2026): i risultati veri vivono sotto
        'dehydratedState.queries[].state.data.results', ma ogni elemento ha la
        forma {"realEstate": {...dati annuncio...}} — NON un 'id' diretto.
        La pagina contiene anche ALTRI elenchi chiamati "results" (es. opzioni di
        categoria in un filtro, tipo [{"id":141,"value":"Agenzia di viaggi"}]),
        quindi il controllo su "realEstate" (non solo "id") è essenziale per non
        prendere l'elenco sbagliato.
        """
        candidates = deep_find_all(next_data, "results")
        for cand in candidates:
            if isinstance(cand, list) and cand and isinstance(cand[0], dict) and "realEstate" in cand[0]:
                return [item["realEstate"] for item in cand if isinstance(item, dict) and isinstance(item.get("realEstate"), dict)]

        # Fallback resiliente a piccoli cambi di schema futuri.
        def scan(obj: Any) -> List[dict]:
            if isinstance(obj, list):
                if obj and all(isinstance(i, dict) and "realEstate" in i for i in obj):
                    return [i["realEstate"] for i in obj]
                found: List[dict] = []
                for item in obj:
                    found.extend(scan(item))
                return found
            if isinstance(obj, dict):
                found = []
                for v in obj.values():
                    found.extend(scan(v))
                return found
            return []

        return scan(next_data)

    def fetch_search_page(self, city_slug: str, category: str, page: int) -> Tuple[List[dict], bool, bool]:
        """Ritorna (lista_annunci, has_results, bloccato). `bloccato` distingue un
        blocco anti-bot confermato (self.goto() ha esaurito i retry) da una
        pagina genuinamente vuota/senza risultati, cosa che una singola `bool`
        non permetteva di fare (serve al circuit breaker in run())."""
        url = self.build_search_url(city_slug, category, page, sort_recent=self.recent_only)
        html = self.goto(url)
        if html is None:
            return [], False, True

        match = NEXT_DATA_RE.search(html)
        if not match:
            logging.warning("Nessun __NEXT_DATA__ trovato su %s (possibile pagina vuota/bloccata).", url)
            return [], False, False

        try:
            next_data = json.loads(match.group(1))
        except json.JSONDecodeError:
            logging.warning("JSON __NEXT_DATA__ non valido su %s", url)
            return [], False, False

        # Il buildId serve solo per l'endpoint di arricchimento _next/data: lo si
        # ricava dalla prima pagina di ricerca valida invece che da una visita
        # separata alla homepage (che in alcuni casi non espone __NEXT_DATA__).
        if not self.build_id:
            self.build_id = next_data.get("buildId")
            if self.build_id:
                logging.info("buildId trovato: %s", self.build_id)

        listings = self.extract_listings_from_next_data(next_data)
        return listings, len(listings) > 0, False

    # -- Arricchimento (dettaglio annuncio) ------------------------------------

    def enrich_listing(self, listing_id: str) -> Optional[dict]:
        """
        Richiede l'endpoint dati interno di Next.js per un annuncio, eseguendo
        il fetch DENTRO il browser (page.evaluate) così da condividere sempre
        la stessa identica sessione/cookie/impronta TLS del browser che ha
        superato la challenge DataDome, evitando il problema del "cookie
        legato a un altro device" che si avrebbe con un client HTTP esterno.
        """
        url = f"{BASE_URL}/_next/data/{self.build_id}/annunci/{listing_id}.json?id={listing_id}"

        try:
            result = self.page.evaluate(FETCH_JSON_JS, url)
        except Exception as exc:
            logging.warning("Errore fetch di arricchimento per l'annuncio %s: %s", listing_id, exc)
            return None

        status = result.get("status") if isinstance(result, dict) else 0
        body = result.get("body") if isinstance(result, dict) else None

        if status != 200 or body is None:
            logging.warning("HTTP %s (o risposta non-JSON) per l'annuncio %s", status, listing_id)
            return None

        return body

    @staticmethod
    def stringify_description(description: Any) -> Optional[str]:
        """
        Il campo 'description' nel JSON di dettaglio può essere una stringa
        diretta oppure un oggetto con sottocampi (es. {'text': ...} o
        {'html': ...}); questa funzione normalizza in ogni caso a testo semplice.
        """
        if description is None:
            return None
        if isinstance(description, str):
            return description
        if isinstance(description, dict):
            for sub_key in ("text", "html", "description", "value"):
                if isinstance(description.get(sub_key), str):
                    text = description[sub_key]
                    # Rimuove eventuali tag HTML residui, se il campo è in formato html.
                    return re.sub(r"<[^>]+>", " ", text).strip()
        return str(description)

    @staticmethod
    def extract_enrichment_fields(detail_json: dict) -> dict:
        """
        Estrae in modo best-effort i campi di interesse (descrizione completa,
        energia, costi) dal JSON di dettaglio. Il JSON grezzo completo viene
        comunque sempre conservato dal chiamante in 'raw_detail'.
        """
        description = deep_find_first(detail_json, "description")
        descrizione_completa = ImmobiliareScraper.stringify_description(description)
        energy = deep_find_first(detail_json, "energy")
        costs = deep_find_first(detail_json, "costs")
        surface = deep_find_first(detail_json, "surface")
        typology = deep_find_first(detail_json, "typology")
        contacts = deep_find_first(detail_json, "contacts")

        return {
            "descrizione_completa": descrizione_completa,
            "energy": energy,
            "costs": costs,
            "surface": surface,
            "typology": typology,
            "contacts": contacts,
        }

    @staticmethod
    def extract_updated_at(detail_json: Optional[dict]) -> Optional[datetime]:
        """
        Estrae la data di aggiornamento reale dell'annuncio (mostrata sul sito
        come "Annuncio aggiornato il DD/MM/YYYY") dal JSON di dettaglio.
        Percorso noto: pageProps.detailData.realEstate.updatedAt (unix timestamp,
        confermato dal vivo settembre 2026). Fallback a deep_find_first se lo
        schema cambia.
        """
        if not isinstance(detail_json, dict):
            return None
        ts = None
        try:
            ts = detail_json["pageProps"]["detailData"]["realEstate"].get("updatedAt")
        except (KeyError, TypeError):
            ts = None
        if ts is None:
            ts = deep_find_first(detail_json, "updatedAt")
        if not isinstance(ts, (int, float)):
            return None
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None

    # -- Orchestrazione ---------------------------------------------------------

    def scrape_city_category(self, city: str, category: str) -> int:
        city_slug = slugify(city)
        logging.info("=== Città: %s | Categoria: %s (slug: %s) ===", city, category, city_slug)

        new_count = 0
        page = 1
        # Contatore di annunci "vecchi" consecutivi (differenza > staleness_days):
        # alcuni annunci a pagamento (es. "supervetrina") possono comparire fuori
        # posto rispetto all'ordinamento per data, quindi ci si ferma solo dopo
        # self.stale_streak annunci vecchi di fila, non al primo isolato.
        consecutive_stale = 0
        self._pagina1_bloccata = False
        while page <= self.max_pages:
            listings, has_results, bloccato = self.fetch_search_page(city_slug, category, page)
            if not has_results:
                if page == 1:
                    if bloccato:
                        # Bloccati fin dalla prima pagina: segnale forte che il
                        # blocco non è isolato a questa città (vedi circuit
                        # breaker in run()).
                        self._pagina1_bloccata = True
                    logging.info("Nessun annuncio trovato per %s / %s.", city, category)
                else:
                    logging.info("Fine paginazione per %s / %s a pagina %d.", city, category, page)
                break

            logging.info("Pagina %d: %d annunci trovati.", page, len(listings))

            for raw_item in listings:
                listing_id = str(raw_item.get("id") or deep_find_first(raw_item, "id") or "")
                if not listing_id:
                    continue

                key = f"immobiliare_{listing_id}"
                existing = self.dataset["listings"].get(key)
                if self.resume and existing and existing.get("enriched"):
                    continue  # già scaricato in una run precedente

                seo = raw_item.get("seo") if isinstance(raw_item.get("seo"), dict) else {}
                listing_url = seo.get("url") or raw_item.get("url") or f"{BASE_URL}/annunci/{listing_id}/"

                # I risultati di ricerca contengono già dati ricchi (confermato dal vivo,
                # settembre 2026): descrizione COMPLETA, superficie, tipologia, prezzo,
                # localizzazione precisa. Si usano come base affidabile indipendentemente
                # dall'esito dell'arricchimento per annuncio (che aggiunge solo dati extra
                # come energia/costi, non sempre presenti).
                prop = (raw_item.get("properties") or [{}])[0] if isinstance(raw_item.get("properties"), list) else {}
                price_info = raw_item.get("price") if isinstance(raw_item.get("price"), dict) else {}
                location = prop.get("location") if isinstance(prop.get("location"), dict) else {}
                typology = prop.get("typology") if isinstance(prop.get("typology"), dict) else {}
                category_info = prop.get("category") if isinstance(prop.get("category"), dict) else {}

                search_fields = {
                    "titolo": raw_item.get("title"),
                    "prezzo": price_info.get("formattedValue"),
                    "prezzo_valore": price_info.get("value"),
                    "superficie_mq": prop.get("surface"),
                    "tipologia": typology.get("name"),
                    "categoria": category_info.get("name"),
                    "indirizzo": location.get("address"),
                    "comune": location.get("city"),
                    "provincia": location.get("province"),
                    "zona": location.get("macrozone") or location.get("microzone"),
                    "descrizione_completa": prop.get("description"),
                }

                # Delay ridotto: l'arricchimento è un fetch() eseguito dentro la pagina già
                # caricata (nessuna nuova navigazione/rendering), quindi ha un'impronta molto
                # più leggera di un page.goto() completo e può permettersi un ritmo più veloce.
                polite_sleep(self.enrich_delay_min, self.enrich_delay_max)
                detail_json = self.enrich_listing(listing_id)
                enrichment = self.extract_enrichment_fields(detail_json) if detail_json else {}
                # L'arricchimento può avere una descrizione ancora più estesa: si usa solo
                # se effettivamente più lunga di quella già presa dai risultati di ricerca.
                enriched_desc = enrichment.get("descrizione_completa")
                if enriched_desc and len(enriched_desc) > len(search_fields["descrizione_completa"] or ""):
                    search_fields["descrizione_completa"] = enriched_desc
                enrichment.pop("descrizione_completa", None)

                listing_record = {
                    "source": "immobiliare.it",
                    "id": listing_id,
                    "city": city,
                    "category": category,
                    "url": listing_url,
                    "enriched": detail_json is not None,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    **search_fields,
                    **enrichment,
                    "raw_search": raw_item,
                    "raw_detail": detail_json,
                }

                stop_here = False
                if self.recent_only:
                    updated_dt = self.extract_updated_at(detail_json)
                    listing_record["updated_at"] = updated_dt.isoformat() if updated_dt else None

                    if updated_dt is not None:
                        # Diagnostica non bloccante sull'ordinamento del sito.
                        if self._last_updated_dt is not None and updated_dt > self._last_updated_dt and not self._sort_order_warning_logged:
                            logging.warning(
                                "L'ordine dei risultati non sembra decrescente per data di aggiornamento "
                                "(annuncio %s aggiornato %s, dopo un annuncio aggiornato %s): il parametro "
                                "di ordinamento (%s) potrebbe non funzionare come previsto.",
                                listing_id, updated_dt.isoformat(), self._last_updated_dt.isoformat(), RECENT_SORT_QUERY,
                            )
                            self._sort_order_warning_logged = True
                        self._last_updated_dt = updated_dt

                        baseline_dt = self.baseline_map.get(key)
                        listing_record["baseline_scraped_at"] = baseline_dt.isoformat() if baseline_dt else None
                        if baseline_dt is not None:
                            diff_days = abs((baseline_dt - updated_dt).total_seconds()) / 86400
                            listing_record["staleness_diff_days"] = diff_days
                            if diff_days > self.staleness_days:
                                consecutive_stale += 1
                                listing_record["stale_streak_at_this_point"] = consecutive_stale
                                if consecutive_stale >= self.stale_streak:
                                    logging.info(
                                        "STOP %s/%s: annuncio %s aggiornato il %s, differenza dallo scraped_at "
                                        "precedente (%s) di %.1f giorni > soglia %d giorni (streak di %d annunci "
                                        "vecchi consecutivi raggiunta).",
                                        city, category, listing_id, updated_dt.isoformat(),
                                        baseline_dt.isoformat(), diff_days, self.staleness_days, consecutive_stale,
                                    )
                                    stop_here = True
                                else:
                                    logging.info(
                                        "Annuncio %s oltre soglia (%.1f gg > %d gg) ma streak solo %d/%d: "
                                        "proseguo (probabile annuncio promosso fuori ordine).",
                                        listing_id, diff_days, self.staleness_days, consecutive_stale, self.stale_streak,
                                    )
                            else:
                                consecutive_stale = 0
                        else:
                            consecutive_stale = 0  # annuncio nuovo, mai visto: tratta come segnale "fresco"
                    else:
                        logging.warning(
                            "Impossibile leggere la data di aggiornamento per l'annuncio %s: "
                            "proseguo senza applicare il controllo di stop per questo annuncio.",
                            listing_id,
                        )

                self.dataset["listings"][key] = listing_record
                new_count += 1

                if stop_here:
                    return new_count

            page += 1
            polite_sleep(self.delay_min, self.delay_max)

        return new_count

    def save(self) -> None:
        self.dataset["generated_at"] = datetime.now(timezone.utc).isoformat()
        self.dataset["total"] = len(self.dataset["listings"])
        atomic_save_json(self.dataset, self.output_path)
        logging.info("Dataset salvato in %s (%d annunci totali).", self.output_path, self.dataset["total"])

    def run(self, cities: List[str]) -> None:
        self.start()
        # Interruttore automatico: se il sito ci blocca fin dalla prima pagina
        # per troppe città/categorie DI FILA, è un blocco di sessione (IP o
        # profilo), non un caso isolato — continuare a martellare le città
        # restanti peggiora solo la situazione. Meglio fermarsi subito e
        # lasciare "raffreddare" la sessione (stesso principio applicato a
        # scraper_idealista_master.py).
        blocchi_consecutivi = 0
        try:
            # Il buildId viene ricavato automaticamente dalla prima pagina di
            # ricerca valida (vedi fetch_search_page), non da una visita separata
            # alla homepage.
            for city in cities:
                fermato = False
                for category in CATEGORIES:
                    try:
                        added = self.scrape_city_category(city, category)
                        logging.info("-> %d nuovi annunci per %s / %s.", added, city, category)
                        if self._pagina1_bloccata:
                            blocchi_consecutivi += 1
                        else:
                            blocchi_consecutivi = 0
                    except Exception:
                        logging.exception("Errore durante lo scraping di %s / %s", city, category)
                    finally:
                        # Salvataggio incrementale: non si perde il lavoro già fatto in caso di crash.
                        self.save()

                    if blocchi_consecutivi >= SOGLIA_BLOCCHI_CONSECUTIVI:
                        logging.error(
                            "STOP: bloccati fin dalla prima pagina per %d città/categorie consecutive. "
                            "Sessione probabilmente compromessa: interrompo qui invece di continuare a "
                            "martellare le città restanti (rischia di aggravare il blocco). Riprova più "
                            "tardi, magari con --headless=false per risolvere un eventuale controllo a mano.",
                            blocchi_consecutivi,
                        )
                        fermato = True
                        break
                if fermato:
                    break

            logging.info("Scraping completato. Totale annunci nel dataset: %d", len(self.dataset["listings"]))
        finally:
            self.stop()


# ============================================================================
# CLI
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scraper stealth per Capannoni/Magazzini in vendita su Immobiliare.it (36 città)."
    )
    parser.add_argument(
        "--cities", type=str, default=None,
        help="Lista di città separate da virgola per limitare la run (default: tutte e 36).",
    )
    parser.add_argument(
        "--headless", type=str2bool, default=True,
        help="true/false: esecuzione in background o con finestra Chrome visibile "
             "(utile per risolvere manualmente un eventuale controllo DataDome).",
    )
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Numero massimo di pagine per città/categoria.")
    parser.add_argument("--delay-min", type=float, default=1.5, help="Delay minimo (secondi) tra le pagine di ricerca (navigazioni complete).")
    parser.add_argument("--delay-max", type=float, default=3.5, help="Delay massimo (secondi) tra le pagine di ricerca (navigazioni complete).")
    parser.add_argument(
        "--enrich-delay-min", type=float, default=0.4,
        help="Delay minimo (secondi) tra le chiamate di arricchimento per annuncio (fetch in background, nessuna navigazione).",
    )
    parser.add_argument(
        "--enrich-delay-max", type=float, default=1.0,
        help="Delay massimo (secondi) tra le chiamate di arricchimento per annuncio (fetch in background, nessuna navigazione).",
    )
    parser.add_argument("--no-resume", action="store_true", help="Ignora il dataset esistente e riparte da zero.")
    parser.add_argument("--output", type=str, default=None, help="Percorso del file JSON di output (default: dataset_immobiliare_completo.json, o dataset_immobiliare_recenti.json con --recent-only).")
    parser.add_argument("--log-level", type=str, default="INFO", help="Livello di log (DEBUG, INFO, WARNING, ERROR).")
    parser.add_argument(
        "--recent-only", action="store_true",
        help="Ordina i risultati per data di aggiornamento decrescente e si ferma, per ogni città/categoria, "
             "al primo annuncio la cui differenza tra data di aggiornamento e lo scraped_at del dataset di "
             "baseline supera --staleness-days. Salva SEMPRE in un file separato (mai nel dataset completo).",
    )
    parser.add_argument(
        "--staleness-days", type=int, default=DEFAULT_STALENESS_DAYS,
        help="Soglia in giorni per --recent-only (default: 14, cioè due settimane).",
    )
    parser.add_argument(
        "--stale-streak", type=int, default=DEFAULT_STALE_STREAK,
        help="Numero di annunci vecchi CONSECUTIVI (oltre --staleness-days) richiesti prima di fermarsi "
             "in modalità --recent-only (default: 3). Assorbe annunci sponsorizzati fuori ordine.",
    )
    parser.add_argument(
        "--baseline", type=str, default=str(OUTPUT_FILE),
        help="Dataset da usare come baseline per gli scraped_at in modalità --recent-only "
             "(default: dataset_immobiliare_completo.json).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    cities = CITIES
    if args.cities:
        requested = {c.strip() for c in args.cities.split(",")}
        cities = [c for c in CITIES if c in requested]
        unknown = requested - set(cities)
        if unknown:
            logging.warning("Città non riconosciute e ignorate: %s", ", ".join(unknown))

    if args.output:
        output_path = Path(args.output)
    elif args.recent_only:
        output_path = RECENT_OUTPUT_FILE
    else:
        output_path = OUTPUT_FILE

    if args.recent_only and output_path == OUTPUT_FILE:
        logging.error(
            "--recent-only con --output uguale al dataset completo (%s) non è permesso: "
            "sovrascriverebbe il dataset esistente. Specifica un --output diverso.",
            OUTPUT_FILE,
        )
        sys.exit(1)

    scraper = ImmobiliareScraper(
        headless=args.headless,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        enrich_delay_min=args.enrich_delay_min,
        enrich_delay_max=args.enrich_delay_max,
        max_pages=args.max_pages,
        resume=not args.no_resume,
        output_path=output_path,
        recent_only=args.recent_only,
        staleness_days=args.staleness_days,
        stale_streak=args.stale_streak,
        baseline_path=Path(args.baseline),
    )

    try:
        scraper.run(cities)
    except KeyboardInterrupt:
        logging.warning("Interruzione manuale ricevuta: salvataggio dello stato parziale...")
        scraper.save()
    except Exception:
        logging.exception("Errore fatale durante lo scraping.")
        scraper.save()
        sys.exit(1)


if __name__ == "__main__":
    main()
