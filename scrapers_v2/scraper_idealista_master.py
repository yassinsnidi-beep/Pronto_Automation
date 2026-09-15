#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_idealista_master.py
=============================

Scraper "stealth" per Idealista.it — estrae annunci di Capannoni/Magazzini in
vendita (categoria "vendita-negozi", etichettata sul sito "Locali o
capannoni") su 36 città italiane target.

NOTA (settembre 2026): Idealista.it NON ha, a differenza di Immobiliare.it,
categorie separate "capannoni" e "magazzini". Dal menu ufficiale "Tipologia"
del sito risulta un'unica categoria che le include entrambe: "Locali o
capannoni" -> /vendita-negozi/ (attributo interno data-filter-href="warehouse").
Le categorie usate in precedenza ("vendita-edifici" = edifici/palazzi generici,
"vendita-garage" = garage/posti auto) erano quindi sbagliate: producevano
soprattutto box auto, scartati a valle. "vendita-negozi" include anche normali
negozi/locali commerciali non pertinenti (nessuna categoria più granulare
esiste sul sito), ma è la categoria corretta più vicina a "capannoni".

Tecnologia
----------
- Playwright (async) con `launch_persistent_context` e `channel="chrome"`:
  usa un vero binario Chrome installato (non il Chromium "vanilla" di
  Playwright), riducendo drasticamente il fingerprint di automazione
  rilevabile da DataDome.
- Profilo Chrome persistente in "./profilo_chrome_idealista": i cookie di
  sessione DataDome (e l'eventuale consenso cookie) vengono salvati su disco
  e riutilizzati tra un'esecuzione e l'altra, evitando di dover "riscaldare"
  una nuova sessione ad ogni run.
- Supporto sia a modalità headless (background, CI/server) sia headful
  (utile la prima volta, per risolvere manualmente un eventuale captcha
  DataDome e "salvare" la sessione valida nel profilo persistente).
- NIENTE navigazione scheda-per-scheda: tutti i campi (titolo, prezzo, m²,
  prezzo/m², zona, agenzia, descrizione) vengono estratti direttamente dalle
  pagine di lista (30 annunci a pagina), sia dal markup delle card sia dagli
  eventuali blocchi JSON-LD incorporati nella pagina per la SEO. Questo evita
  di aprire una pagina per ogni singolo annuncio, che era il vero collo di
  bottiglia (ore anziché minuti). Il testo di descrizione recuperato così è il
  massimo ottenibile senza visitare la scheda del singolo annuncio: Idealista
  mostra in lista spesso un estratto (eventualmente troncato) della
  descrizione completa, non garantita al 100% identica al testo integrale
  visibile solo aprendo l'annuncio.

Installazione
--------------
    pip install playwright beautifulsoup4 lxml
    playwright install chrome

Esecuzione
----------
    # Prima esecuzione: consigliato in modalità visibile per superare
    # un eventuale challenge DataDome e salvare i cookie nel profilo.
    python scraper_idealista_master.py --headless=false

    # Esecuzioni successive, in background:
    python scraper_idealista_master.py --headless=true

    # Verifica rapida degli slug URL delle città prima di una run completa:
    python scraper_idealista_master.py --validate-urls

Output
------
    scrapers_v2/dataset_idealista_completo.json

Note importanti sugli slug URL
-------------------------------
Idealista.it costruisce le URL di ricerca per città con la convenzione
"<comune>-<provincia>" (es. "milano-milano", "sesto-san-giovanni-milano").
La mappa CITY_SLUGS qui sotto è una migliore stima basata sulla geografia
amministrativa italiana, ma il sito può cambiare/aggiornare gli slug nel
tempo: usa il flag --validate-urls per un controllo rapido prima di una
run massiva, e correggi manualmente CITY_SLUGS se una città risulta 404
o rimanda alla home.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PWTimeoutError
except ImportError:
    print(
        "ERRORE: la libreria 'playwright' non è installata.\n"
        "Installala con:  pip install playwright\n"
        "e poi:            playwright install chrome",
        file=sys.stderr,
    )
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

BASE_URL = "https://www.idealista.it"

CITIES: List[str] = [
    "Bolzano", "Bologna", "Trento", "Padova", "Milano", "Parma", "Modena",
    "Reggio Emilia", "Brescia", "Monza", "Bergamo", "Verona", "Vicenza",
    "Firenze", "Pavia", "Como", "Piacenza", "Treviso", "Novara", "Cremona",
    "Cesena", "Forlì", "Pisa", "Udine", "Varese", "Ravenna", "Ancona",
    "Sesto San Giovanni", "Cinisello Balsamo", "Busto Arsizio", "Roma",
    "Legnano", "Prato", "Cagliari", "Pistoia", "Lucca",
]

# Categoria corretta per capannoni/magazzini su Idealista.it (vedi nota nel
# docstring del modulo): "Locali o capannoni" -> vendita-negozi. Non esiste
# una categoria "vendita-magazzini" o "vendita-capannoni" separata sul sito.
CATEGORIES: List[str] = ["vendita-negozi"]

# Mappa comune -> provincia, usata per costruire lo slug "<comune>-<provincia>"
# tipico delle URL di ricerca per città di Idealista.it.
CITY_PROVINCE: Dict[str, str] = {
    "Bolzano": "Bolzano",
    "Bologna": "Bologna",
    "Trento": "Trento",
    "Padova": "Padova",
    "Milano": "Milano",
    "Parma": "Parma",
    "Modena": "Modena",
    "Reggio Emilia": "Reggio Emilia",
    "Brescia": "Brescia",
    "Monza": "Monza e della Brianza",
    "Bergamo": "Bergamo",
    "Verona": "Verona",
    "Vicenza": "Vicenza",
    "Firenze": "Firenze",
    "Pavia": "Pavia",
    "Como": "Como",
    "Piacenza": "Piacenza",
    "Treviso": "Treviso",
    "Novara": "Novara",
    "Cremona": "Cremona",
    "Cesena": "Forlì-Cesena",
    "Forlì": "Forlì-Cesena",
    "Pisa": "Pisa",
    "Udine": "Udine",
    "Varese": "Varese",
    "Ravenna": "Ravenna",
    "Ancona": "Ancona",
    "Sesto San Giovanni": "Milano",
    "Cinisello Balsamo": "Milano",
    "Busto Arsizio": "Varese",
    "Roma": "Roma",
    "Legnano": "Milano",
    "Prato": "Prato",
    "Cagliari": "Cagliari",
    "Pistoia": "Pistoia",
    "Lucca": "Lucca",
}

# Override manuali: se per una città lo slug generato automaticamente non
# funziona (--validate-urls lo segnala), aggiungi qui lo slug corretto
# copiato manualmente dalla barra indirizzi di idealista.it.
CITY_SLUG_OVERRIDES: Dict[str, str] = {
    # "Forlì": "forli-forli-cesena",
}

OUTPUT_FILE = Path(__file__).resolve().parent / "dataset_idealista_completo.json"
RECENT_OUTPUT_FILE = Path(__file__).resolve().parent / "dataset_idealista_recenti.json"
PROFILE_DIR = Path(__file__).resolve().parent / "profilo_chrome_idealista"

NAV_TIMEOUT_MS = 45_000
DEFAULT_MAX_PAGES = 25
SOGLIA_BLOCCHI_CONSECUTIVI = 3  # città/categorie bloccate fin dalla 1a pagina, di fila, prima di fermare l'intera run
DEFAULT_STALENESS_DAYS = 14  # soglia "due settimane" per la modalità --recent-only
DEFAULT_STALE_STREAK = 3  # annunci vecchi consecutivi richiesti prima di fermarsi (assorbe outlier promossi)

# Query di ordinamento "più recenti" (per data di pubblicazione/rinnovo annuncio),
# confermata manualmente su idealista.it dall'utente.
RECENT_SORT_QUERY = "ordine=pubblicazione-desc"

COOKIE_BANNER_SELECTORS = [
    "#didomi-notice-agree-button",
    "button#onetrust-accept-btn-handler",
    "button[data-testid='cookie-accept']",
    "text=Accetta",
    "text=Accetto",
]

BLOCKED_MARKERS = [
    "datadome",
    "captcha-delivery.com",
    "unusual traffic",
    "traffico insolito",
    "accesso negato",
]


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
                Path(__file__).resolve().parent / "scraper_idealista.log",
                encoding="utf-8",
            ),
        ],
    )


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_str = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_str = ascii_str.lower().strip()
    ascii_str = re.sub(r"[^a-z0-9]+", "-", ascii_str)
    return ascii_str.strip("-")


def city_slug(city: str) -> str:
    """
    Costruisce lo slug URL "<comune>-<provincia>" usato da Idealista.it,
    con possibilità di override manuale per città problematiche.
    """
    if city in CITY_SLUG_OVERRIDES:
        return CITY_SLUG_OVERRIDES[city]

    comune = slugify(city)
    provincia = slugify(CITY_PROVINCE.get(city, city))
    return f"{comune}-{provincia}"


async def polite_sleep(delay_min: float, delay_max: float) -> None:
    await asyncio.sleep(random.uniform(delay_min, delay_max))


def atomic_save_json(data: Any, path: Path) -> None:
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
    Legge un dataset già generato in precedenza (es. dataset_idealista_completo.json)
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


# Parsing di frasi italiane relative del tipo mostrato sulla pagina di dettaglio
# di Idealista.it ("Annuncio aggiornato più di 3 mesi fa", "Annuncio aggiornato
# oggi", "Annuncio aggiornato 2 settimane fa", ...). Idealista mostra un tempo
# RELATIVO, non una data esatta: il numero di giorni qui è quindi una STIMA
# (per le forme "più di X", si usa il limite inferiore X, che è conservativo
# per la soglia "> N giorni": se la stima è già oltre soglia, il valore reale,
# più alto, lo è a maggior ragione).
_RELATIVE_TIME_PATTERNS = [
    (re.compile(r"\boggi\b", re.IGNORECASE), 0),
    (re.compile(r"\bieri\b", re.IGNORECASE), 1),
    (re.compile(r"\bun'?\s*ora\b|\bpoco\s+fa\b", re.IGNORECASE), 0),
    (re.compile(r"\bun\s+giorno\b", re.IGNORECASE), 1),
    (re.compile(r"(\d+)\s*giorn", re.IGNORECASE), None),
    (re.compile(r"\buna\s+settimana\b", re.IGNORECASE), 7),
    (re.compile(r"(\d+)\s*settiman", re.IGNORECASE), None),
    (re.compile(r"\bun\s+mese\b", re.IGNORECASE), 30),
    (re.compile(r"(\d+)\s*mes", re.IGNORECASE), None),
    (re.compile(r"\bun\s+anno\b", re.IGNORECASE), 365),
    (re.compile(r"(\d+)\s*ann", re.IGNORECASE), None),
]
_UNIT_MULTIPLIER = {
    "giorn": 1, "settiman": 7, "mes": 30, "ann": 365,
}


def parse_italian_relative_days(text: Optional[str]) -> Optional[int]:
    """
    Converte una frase italiana relativa (es. 'più di 3 mesi fa', 'ieri',
    '2 settimane fa', 'oggi') in una stima di giorni trascorsi. Ritorna None
    se il testo non è riconosciuto.
    """
    if not text:
        return None
    lowered = text.lower()

    for pattern, fixed_value in _RELATIVE_TIME_PATTERNS:
        match = pattern.search(lowered)
        if not match:
            continue
        if fixed_value is not None:
            return fixed_value
        number = int(match.group(1))
        # Determina il moltiplicatore in base a quale parola d'unità è stata matchata.
        for unit_stem, multiplier in _UNIT_MULTIPLIER.items():
            if unit_stem in match.group(0).lower():
                return number * multiplier
    return None


# ============================================================================
# PARSING HTML (BeautifulSoup) — tutto estratto dalla pagina di LISTA, senza
# mai navigare verso la scheda del singolo annuncio.
# ============================================================================

def extract_jsonld_descriptions(soup: BeautifulSoup) -> Dict[str, str]:
    """
    Alcune pagine di lista incorporano blocchi <script type="application/ld+json">
    con dati strutturati per la SEO, che a volte contengono un testo di
    descrizione più esteso di quello mostrato nella card visibile. Ritorna una
    mappa {url_annuncio: descrizione}, quando disponibile. Best-effort: se lo
    schema cambia o non è presente, ritorna semplicemente un dizionario vuoto.
    """
    mapping: Dict[str, str] = {}
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            desc = item.get("description")
            if url and isinstance(desc, str) and desc.strip():
                mapping[url] = desc.strip()
    return mapping


def extract_from_details(details: List[str]) -> Dict[str, Optional[str]]:
    """Isola superficie (m²) e prezzo/m² dalla lista di 'span.item-detail' della card."""
    superficie_mq = None
    prezzo_mq = None
    for d in details:
        low = d.lower().replace("\xa0", " ")
        if ("m²" in low or "m2" in low) and "/" not in low and superficie_mq is None:
            superficie_mq = d
        elif ("/m²" in low or "/m2" in low) and prezzo_mq is None:
            prezzo_mq = d
    return {"superficie_mq": superficie_mq, "prezzo_mq": prezzo_mq}


def parse_listing_cards(html: str) -> List[dict]:
    """
    Estrae dalla pagina di risultati di ricerca (30 annunci/pagina) TUTTI i campi
    richiesti senza mai visitare la scheda del singolo annuncio: titolo, prezzo,
    superficie, prezzo/m², zona, agenzia e descrizione (estratto di lista,
    eventualmente arricchito con il testo più esteso trovato nei blocchi JSON-LD
    della stessa pagina). Idealista.it usa tipicamente
    <article class="item" data-element-id="..."> con link in
    <a class="item-link" href="/immobile/<id>/">. Il parsing è difensivo: se le
    classi CSS cambiano, prova un fallback basato sul pattern dell'URL.
    """
    soup = BeautifulSoup(html, "lxml")
    jsonld_desc = extract_jsonld_descriptions(soup)
    listings: List[dict] = []

    articles = soup.select("article.item") or soup.select("article[data-element-id]")

    if not articles:
        # Fallback: cerca direttamente i link "/immobile/<id>/" nella pagina.
        seen_ids = set()
        for a in soup.select("a[href*='/immobile/']"):
            href = a.get("href", "")
            match = re.search(r"/immobile/(\d+)/", href)
            if not match:
                continue
            listing_id = match.group(1)
            if listing_id in seen_ids:
                continue
            seen_ids.add(listing_id)
            full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
            listings.append({
                "id": listing_id,
                "url": full_url,
                "titolo": a.get_text(strip=True) or None,
                "prezzo": None,
                "superficie_mq": None,
                "prezzo_mq": None,
                "zona": None,
                "agenzia": None,
                "descrizione_completa": jsonld_desc.get(full_url),
                "details": [],
            })
        return listings

    for art in articles:
        listing_id = art.get("data-element-id")
        link = art.select_one("a.item-link") or art.select_one("a[href*='/immobile/']")
        href = link.get("href", "") if link else ""
        if not listing_id:
            match = re.search(r"/immobile/(\d+)/", href)
            listing_id = match.group(1) if match else None
        if not listing_id:
            continue

        full_url = href if href.startswith("http") else f"{BASE_URL}{href}"

        titolo = link.get_text(strip=True) if link else None

        price_el = art.select_one("span.item-price") or art.select_one(".price-row")
        prezzo = price_el.get_text(strip=True) if price_el else None

        detail_els = art.select("span.item-detail")
        details = [d.get_text(strip=True) for d in detail_els]
        mq_fields = extract_from_details(details)

        location_el = art.select_one(".item-detail-location") or art.select_one(".listing-location")
        zona = location_el.get_text(strip=True) if location_el else None

        agenzia_el = art.select_one("picture.logo-branding img") or art.select_one(".logo-branding img")
        agenzia = agenzia_el.get("alt") if agenzia_el else None
        if not agenzia:
            agenzia_text_el = art.select_one(".item-agency-name") or art.select_one(".advertiser-name")
            agenzia = agenzia_text_el.get_text(strip=True) if agenzia_text_el else None

        # Estratto di descrizione visibile in card + eventuale testo più esteso
        # trovato nei dati strutturati JSON-LD della stessa pagina: si tiene il più lungo dei due.
        desc_el = art.select_one(".item-description") or art.select_one(".description")
        card_snippet = desc_el.get_text(" ", strip=True) if desc_el else None
        jsonld_text = jsonld_desc.get(full_url)
        if jsonld_text and (not card_snippet or len(jsonld_text) > len(card_snippet)):
            descrizione_completa = jsonld_text
        else:
            descrizione_completa = card_snippet

        listings.append({
            "id": listing_id,
            "url": full_url,
            "titolo": titolo,
            "prezzo": prezzo,
            "superficie_mq": mq_fields["superficie_mq"],
            "prezzo_mq": mq_fields["prezzo_mq"],
            "zona": zona,
            "agenzia": agenzia,
            "descrizione_completa": descrizione_completa,
            "details": details,
        })

    return listings


# ============================================================================
# SCRAPER
# ============================================================================

class IdealistaScraper:
    def __init__(
        self,
        headless: bool = True,
        delay_min: float = 1.5,
        delay_max: float = 3.5,
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
        self.max_pages = max_pages
        self.resume = resume
        self.output_path = output_path
        self.profile_dir = profile_dir

        self.recent_only = recent_only
        self.staleness_days = staleness_days
        self.stale_streak = stale_streak
        self.baseline_map: Dict[str, datetime] = (
            load_baseline_scraped_at(baseline_path or OUTPUT_FILE) if recent_only else {}
        )
        # Diagnostica non bloccante sull'ordinamento (vedi commento omologo in
        # scraper_immobiliare_master.py).
        self._last_updated_dt: Optional[datetime] = None
        self._sort_order_warning_logged = False
        self._blocco_rilevato = False
        # A differenza di _blocco_rilevato (per-città, controllato da run()), questo
        # conta i blocchi CONSECUTIVI sulle visite di dettaglio (--recent-only),
        # che avvengono molto più spesso (una per annuncio): serve a fermarsi
        # entro la città corrente, non solo a fine città come l'altro contatore.
        self._blocchi_dettaglio_consecutivi = 0

        self.dataset: Dict[str, Any] = (
            load_existing_dataset(output_path) if resume else {"generated_at": None, "total": 0, "listings": {}}
        )

        self._context: Optional[BrowserContext] = None

    # -- Setup browser --------------------------------------------------------

    async def start(self):
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()

        # Profilo Chrome persistente: mantiene i cookie di sessione DataDome
        # tra una run e l'altra, evitando di dover superare un nuovo
        # challenge anti-bot ad ogni esecuzione.
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            channel="chrome",  # usa il Chrome reale installato nel sistema
            headless=self.headless,
            locale="it-IT",
            timezone_id="Europe/Rome",
            viewport={"width": 1366, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )
        self._context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        logging.info("Browser Chrome persistente avviato (headless=%s, profilo=%s).", self.headless, self.profile_dir)

    async def stop(self):
        if self._context is not None:
            await self._context.close()
        if hasattr(self, "_playwright"):
            await self._playwright.stop()

    async def _new_page(self) -> Page:
        page = await self._context.new_page()
        return page

    async def handle_cookie_banner(self, page: Page) -> None:
        for selector in COOKIE_BANNER_SELECTORS:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=1500):
                    await el.click(timeout=1500)
                    logging.debug("Banner cookie chiuso con selettore: %s", selector)
                    return
            except PWTimeoutError:
                continue
            except Exception:
                continue

    async def goto_with_protection_check(self, page: Page, url: str) -> Optional[str]:
        """
        Naviga verso `url` e ritorna l'HTML della pagina, oppure None se il
        contenuto sembra un blocco DataDome/captcha dopo i tentativi di retry.
        """
        for attempt in range(1, 3 + 1):
            try:
                await page.goto(url, wait_until="domcontentloaded")
            except PWTimeoutError:
                logging.warning("Timeout di navigazione su %s (tentativo %d/3)", url, attempt)
                await polite_sleep(self.delay_min, self.delay_max)
                continue

            await self.handle_cookie_banner(page)
            await polite_sleep(1.0, 2.5)  # piccolo respiro per il rendering/JS

            html = await page.content()

            if is_blocked(html):
                if not self.headless:
                    logging.warning(
                        "Rilevato possibile blocco DataDome su %s. "
                        "Risolvi manualmente l'eventuale captcha nella finestra del browser: "
                        "attendo 20s prima di ricontrollare...",
                        url,
                    )
                    await asyncio.sleep(20)
                    html = await page.content()
                    if not is_blocked(html):
                        return html
                logging.warning("Blocco confermato su %s (tentativo %d/3).", url, attempt)
                await polite_sleep(self.delay_min * 2, self.delay_max * 2)
                continue

            return html

        logging.error("Impossibile superare il blocco anti-bot per %s dopo 3 tentativi.", url)
        return None

    async def fetch_update_recency_days(self, page: Page, url: str) -> Tuple[Optional[int], bool]:
        """
        Visita la pagina di DETTAGLIO di un annuncio (unica visita di questo tipo
        nello scraper: normalmente Idealista viene letto solo dalla lista) e
        legge il testo "Annuncio aggiornato ... fa" mostrato lì, convertendolo
        in giorni stimati con parse_italian_relative_days(). Ritorna
        (giorni_stimati, bloccato): `bloccato` distingue un blocco anti-bot
        confermato (serve al circuit breaker in run(), che con --recent-only
        deve monitorare anche QUESTE visite, non solo quelle alla lista) da un
        annuncio genuinamente senza quel testo (es. mai rinnovato)."""
        html = await self.goto_with_protection_check(page, url)
        if html is None:
            return None, True

        soup = BeautifulSoup(html, "lxml")
        el = soup.select_one(".time-since-last-modification") or soup.select_one("[class*='time-since-last-modification']")
        text = el.get_text(strip=True) if el else None

        if not text:
            # Fallback: cerca direttamente la frase nel testo grezzo della pagina.
            match = re.search(r"[Aa]nnuncio\s+aggiornat[oa][^<]{0,40}", html)
            text = match.group(0) if match else None

        if not text:
            # Diagnostica: capire se manca solo il selettore o se la pagina non
            # menziona affatto un aggiornamento (es. annuncio mai rinnovato dopo
            # la pubblicazione iniziale, quindi Idealista non mostra quel testo).
            if "aggiornat" in html.lower():
                logging.debug(
                    "fetch_update_recency_days: parola 'aggiornat' presente in pagina ma selettore/regex non l'hanno "
                    "catturata su %s (possibile markup diverso da quello atteso).", url,
                )
            else:
                pubblicato_match = re.search(r"[Pp]ubblicat[oa][^<]{0,40}", html)
                logging.debug(
                    "fetch_update_recency_days: nessuna menzione di 'aggiornat' su %s (probabile annuncio mai "
                    "rinnovato dopo la pubblicazione). Testo pubblicazione trovato: %s",
                    url, pubblicato_match.group(0) if pubblicato_match else "nessuno",
                )

        return parse_italian_relative_days(text), False

    # -- Costruzione URL --------------------------------------------------------

    @staticmethod
    def build_search_url(slug: str, category: str, page: int, sort_recent: bool = False) -> str:
        # Confermato manualmente su idealista.it: la pagina N (N>1) usa il
        # pattern "lista-N.htm", non "pagina-N.htm".
        base = f"{BASE_URL}/{category}/{slug}/"
        path = f"{base}lista-{page}.htm" if page > 1 else base
        if sort_recent:
            return f"{path}?{RECENT_SORT_QUERY}"
        return path

    async def validate_city_urls(self, cities: List[str]) -> None:
        """Controllo rapido: apre la pagina 1 di ogni città/categoria e segnala eventuali 404 o pagine vuote."""
        page = await self._new_page()
        try:
            for city in cities:
                slug = city_slug(city)
                for category in CATEGORIES:
                    url = self.build_search_url(slug, category, 1)
                    html = await self.goto_with_protection_check(page, url)
                    if html is None:
                        logging.warning("[VALIDAZIONE] %s (%s): richiesta fallita/bloccata -> %s", city, category, url)
                        continue
                    listings = parse_listing_cards(html)
                    status = "OK" if listings else "NESSUN ANNUNCIO O SLUG ERRATO"
                    logging.info("[VALIDAZIONE] %s (%s) -> %s | slug=%s | %d annunci | %s",
                                 city, category, status, slug, len(listings), url)
                    await polite_sleep(self.delay_min, self.delay_max)
        finally:
            await page.close()

    # -- Scraping ---------------------------------------------------------------
    # Modalità normale: nessuna visita alla scheda del singolo annuncio, tutti i
    # campi vengono estratti da parse_listing_cards() direttamente dalla pagina
    # di lista. In modalità --recent-only viene invece aperta anche la pagina di
    # dettaglio di ogni annuncio, per leggere la data di aggiornamento (vedi
    # fetch_update_recency_days).

    async def scrape_city_category(self, city: str, category: str) -> int:
        slug = city_slug(city)
        logging.info("=== Città: %s | Categoria: %s (slug: %s) ===", city, category, slug)

        search_page = await self._new_page()
        detail_page = await self._new_page() if self.recent_only else None
        new_count = 0
        # Contatore di annunci "vecchi" consecutivi (differenza > staleness_days):
        # alcuni annunci promossi possono comparire fuori posto rispetto
        # all'ordinamento per data, quindi ci si ferma solo dopo self.stale_streak
        # annunci vecchi di fila, non al primo isolato.
        consecutive_stale = 0
        # ID già visti in QUALSIASI pagina precedente di questa città/categoria
        # (non solo nell'ultima): oltre l'ultima pagina reale, Idealista può
        # "rimbalzare" mostrando di nuovo la pagina 1 invece di una pagina
        # vuota, e il rimbalzo può avvenire a qualunque numero di pagina.
        seen_ids: set = set()
        self._blocco_rilevato = False
        try:
            page_num = 1
            while page_num <= self.max_pages:
                url = self.build_search_url(slug, category, page_num, sort_recent=self.recent_only)
                html = await self.goto_with_protection_check(search_page, url)
                if html is None:
                    if page_num == 1:
                        # Bloccati fin dalla prima pagina: segnale forte che il
                        # blocco non è isolato a questa città, ma sta colpendo
                        # l'intera sessione (vedi circuit breaker in run()).
                        self._blocco_rilevato = True
                    break

                listings = parse_listing_cards(html)
                if not listings:
                    if page_num == 1:
                        logging.info("Nessun annuncio trovato per %s / %s (verifica slug con --validate-urls).", city, category)
                    else:
                        logging.info("Fine paginazione per %s / %s a pagina %d.", city, category, page_num)
                    break

                current_page_ids = {item.get("id") for item in listings if item.get("id")}
                new_ids_on_page = current_page_ids - seen_ids
                if not new_ids_on_page:
                    # Nessun annuncio nuovo rispetto a quanto già visto: quasi certamente
                    # un rimbalzo oltre l'ultima pagina reale (es. redirect alla pagina 1).
                    logging.info(
                        "Pagina %d non contiene annunci nuovi (fine paginazione probabile): "
                        "fine paginazione per %s / %s.",
                        page_num, city, category,
                    )
                    break
                seen_ids |= current_page_ids

                logging.info("Pagina %d: %d annunci trovati (%d nuovi).", page_num, len(listings), len(new_ids_on_page))

                for item in listings:
                    listing_id = item.get("id")
                    if not listing_id or listing_id not in new_ids_on_page:
                        continue  # non nuovo in questa pagina (già processato in una pagina precedente di questa run)
                    key = f"idealista_{listing_id}"

                    if self.resume and key in self.dataset["listings"]:
                        continue  # già scaricato in una run precedente

                    listing_record = {
                        "source": "idealista.it",
                        "id": listing_id,
                        "city": city,
                        "category": category,
                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                        **item,
                    }

                    stop_here = False
                    if self.recent_only:
                        await polite_sleep(self.delay_min, self.delay_max)
                        days_ago, bloccato = await self.fetch_update_recency_days(detail_page, item.get("url"))
                        if bloccato:
                            # Blocco sulla pagina di DETTAGLIO: con --recent-only
                            # questa visita avviene per OGNI annuncio, quindi è la
                            # fonte di blocchi più frequente di quella sulla lista,
                            # e va fermata SUBITO (non solo segnalata a fine città:
                            # continuare martellerebbe decine di annunci prima che
                            # run() se ne accorga).
                            self._blocco_rilevato = True
                            self._blocchi_dettaglio_consecutivi += 1
                            if self._blocchi_dettaglio_consecutivi >= SOGLIA_BLOCCHI_CONSECUTIVI:
                                logging.error(
                                    "STOP %s/%s: bloccati %d annunci di fila sulla pagina di dettaglio. "
                                    "Sessione probabilmente compromessa: interrompo qui invece di continuare a "
                                    "martellare gli annunci restanti.",
                                    city, category, self._blocchi_dettaglio_consecutivi,
                                )
                                self.dataset["listings"][key] = listing_record
                                return new_count
                        else:
                            self._blocchi_dettaglio_consecutivi = 0

                        if days_ago is None:
                            logging.warning(
                                "Impossibile leggere la data di aggiornamento per l'annuncio %s: "
                                "proseguo senza applicare il controllo di stop per questo annuncio.",
                                listing_id,
                            )
                            listing_record["updated_days_ago_estimate"] = None
                            listing_record["updated_at_estimate"] = None
                        else:
                            updated_dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
                            listing_record["updated_days_ago_estimate"] = days_ago
                            listing_record["updated_at_estimate"] = updated_dt.isoformat()

                            # Diagnostica non bloccante sull'ordinamento del sito.
                            if self._last_updated_dt is not None and updated_dt > self._last_updated_dt and not self._sort_order_warning_logged:
                                logging.warning(
                                    "L'ordine dei risultati non sembra decrescente per data di aggiornamento "
                                    "(annuncio %s stimato %s, dopo un annuncio stimato %s): il parametro "
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
                                            "STOP %s/%s: annuncio %s aggiornato ~%s (stima da 'più di X fa'), "
                                            "differenza dallo scraped_at precedente (%s) di %.1f giorni > soglia %d "
                                            "giorni (streak di %d annunci vecchi consecutivi raggiunta).",
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

                    self.dataset["listings"][key] = listing_record
                    new_count += 1

                    if stop_here:
                        return new_count

                page_num += 1
                await polite_sleep(self.delay_min, self.delay_max)
        finally:
            await search_page.close()
            if detail_page is not None:
                await detail_page.close()

        return new_count

    def save(self) -> None:
        self.dataset["generated_at"] = datetime.now(timezone.utc).isoformat()
        self.dataset["total"] = len(self.dataset["listings"])
        atomic_save_json(self.dataset, self.output_path)
        logging.info("Dataset salvato in %s (%d annunci totali).", self.output_path, self.dataset["total"])

    async def run(self, cities: List[str]) -> None:
        # Interruttore automatico: se il sito ci blocca fin dalla prima pagina
        # per troppe città/categorie DI FILA, è un blocco di sessione (IP o
        # profilo), non un caso isolato — continuare a martellare le città
        # restanti peggiora solo la situazione (verificato dal vivo il
        # 2026-09-17: bloccato su OGNI città per oltre 10 minuti di fila).
        # Meglio fermarsi subito e lasciare "raffreddare" la sessione.
        blocchi_consecutivi = 0

        for city in cities:
            for category in CATEGORIES:
                try:
                    added = await self.scrape_city_category(city, category)
                    logging.info("-> %d nuovi annunci per %s / %s.", added, city, category)
                    if self._blocco_rilevato:
                        blocchi_consecutivi += 1
                    else:
                        blocchi_consecutivi = 0
                except Exception:
                    logging.exception("Errore durante lo scraping di %s / %s", city, category)
                finally:
                    self.save()  # salvataggio incrementale

                if blocchi_consecutivi >= SOGLIA_BLOCCHI_CONSECUTIVI:
                    logging.error(
                        "STOP: bloccati fin dalla prima pagina per %d città/categorie consecutive. "
                        "Sessione probabilmente compromessa: interrompo qui invece di continuare a "
                        "martellare le città restanti (rischia di aggravare il blocco). Riprova più "
                        "tardi, magari con --headless=false per risolvere un eventuale controllo a mano.",
                        blocchi_consecutivi,
                    )
                    logging.info("Scraping interrotto per blocco persistente. Totale annunci nel dataset: %d", len(self.dataset["listings"]))
                    return

        logging.info("Scraping completato. Totale annunci nel dataset: %d", len(self.dataset["listings"]))


# ============================================================================
# CLI
# ============================================================================

def str2bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y", "si", "sì")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scraper stealth per Capannoni/Magazzini in vendita su Idealista.it (36 città)."
    )
    parser.add_argument("--cities", type=str, default=None, help="Lista di città separate da virgola (default: tutte e 36).")
    parser.add_argument("--headless", type=str2bool, default=True, help="true/false: esecuzione in background o con finestra visibile.")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Numero massimo di pagine per città/categoria.")
    parser.add_argument("--delay-min", type=float, default=1.5, help="Delay minimo (secondi) tra le pagine di lista.")
    parser.add_argument("--delay-max", type=float, default=3.5, help="Delay massimo (secondi) tra le pagine di lista.")
    parser.add_argument("--no-resume", action="store_true", help="Ignora il dataset esistente e riparte da zero.")
    parser.add_argument("--output", type=str, default=None, help="Percorso del file JSON di output (default: dataset_idealista_completo.json, o dataset_idealista_recenti.json con --recent-only).")
    parser.add_argument("--validate-urls", action="store_true", help="Controlla solo la validità degli slug URL per ogni città, senza estrarre dati.")
    parser.add_argument("--log-level", type=str, default="INFO", help="Livello di log (DEBUG, INFO, WARNING, ERROR).")
    parser.add_argument(
        "--recent-only", action="store_true",
        help="Ordina i risultati per data di pubblicazione/rinnovo decrescente e apre anche la pagina di "
             "dettaglio di ogni annuncio (nuova per Idealista, più richieste del normale) per leggerne la "
             "data di aggiornamento relativa. Si ferma, per ogni città/categoria, al primo annuncio la cui "
             "differenza stimata rispetto allo scraped_at del dataset di baseline supera --staleness-days. "
             "Salva SEMPRE in un file separato (mai nel dataset completo).",
    )
    parser.add_argument(
        "--staleness-days", type=int, default=DEFAULT_STALENESS_DAYS,
        help="Soglia in giorni per --recent-only (default: 14, cioè due settimane).",
    )
    parser.add_argument(
        "--stale-streak", type=int, default=DEFAULT_STALE_STREAK,
        help="Numero di annunci vecchi CONSECUTIVI (oltre --staleness-days) richiesti prima di fermarsi "
             "in modalità --recent-only (default: 3). Assorbe annunci promossi fuori ordine.",
    )
    parser.add_argument(
        "--baseline", type=str, default=str(OUTPUT_FILE),
        help="Dataset da usare come baseline per gli scraped_at in modalità --recent-only "
             "(default: dataset_idealista_completo.json).",
    )
    return parser.parse_args()


async def main_async() -> None:
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

    scraper = IdealistaScraper(
        headless=args.headless,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        max_pages=args.max_pages,
        resume=not args.no_resume,
        output_path=output_path,
        recent_only=args.recent_only,
        staleness_days=args.staleness_days,
        stale_streak=args.stale_streak,
        baseline_path=Path(args.baseline),
    )

    await scraper.start()
    try:
        if args.validate_urls:
            await scraper.validate_city_urls(cities)
        else:
            await scraper.run(cities)
    except KeyboardInterrupt:
        logging.warning("Interruzione manuale ricevuta: salvataggio dello stato parziale...")
        scraper.save()
    except Exception:
        logging.exception("Errore fatale durante lo scraping.")
        scraper.save()
        raise
    finally:
        await scraper.stop()


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
