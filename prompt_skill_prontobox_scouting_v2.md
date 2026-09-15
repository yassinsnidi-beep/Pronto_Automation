# Prompt per la generazione della skill — `prontobox-scouting` (v2, cartella Pronto_Automation)

> Da incollare a Claude (usando la skill `skill-creator`) per generare la skill.
> Allegare contestualmente: la cartella `opportunities/INPUT/` (o almeno un file per fonte) e il deck `Self-Storage-in-Italia-2026.pptx`.
> Rispetto alla v1 cambiano: input (10 file eterogenei invece di un `aste.json` normalizzato), selezione settimanale (top 10 con controllo sullo storico), memoria (un solo `summary.json` al posto di dossier + indice) e avvio (un pannello con un pulsante che lancia scraping e valutazione).

---

Crea una skill chiamata **`prontobox-scouting`** che, una volta a settimana, valuta tutti gli immobili raccolti dagli scraper di `Pronto_Automation`, li mette in un'unica classifica e segnala le **10 migliori opportunità che hanno qualcosa di nuovo da dire**: mai segnalate prima, oppure già segnalate ma migliorate.

Insieme alla skill crea un **pannello settimanale** (§7): l'utente lo apre con un doppio clic, preme **Avvia** e parte tutto in automatico, prima lo scraping e poi la valutazione. Alla fine il pannello mostra le 10 opportunità della settimana.

Cosa consegnare:

1. la skill (`SKILL.md`, `scout.py`, `config.yaml`, `reference/`, test);
2. il pannello (`pannello/`);
3. il file di avvio `Avvia Prontobox Scouting.bat`;
4. un `README.md` in italiano che spieghi installazione e uso settimanale a una persona non tecnica.

## 1. Contesto di business (leggi prima di progettare)

Prontobox apre sedi self-storage nel Nord e nel Centro-Nord Italia. Il modello economico ha tre caratteristiche che determinano l'intera logica di valutazione:

1. **Ogni immobile viene ristrutturato integralmente**, sempre. L'antincendio per attività 70 (DPR 151/2011) impone un retrofit completo a prescindere dallo stato. Quindi **lo stato di conservazione NON è un criterio di qualità**: è solo un delta di capex. Un rudere non è peggio di un immobile nuovo — costa meno e non fa pagare finiture destinate alla demolizione.
2. **Il ricavo si genera sulla superficie locabile netta (SLN)**, non sulla superficie lorda. Il prezzo al m² lordo è un indicatore ingannevole: l'altezza utile (soppalcabilità) e la regolarità della pianta possono far variare il costo effettivo per m² SLN del 60% a parità di €/m² d'acquisto.
3. **Le irregolarità urbanistiche sono leva negoziale, non motivo di scarto.** Difformità edilizie, categoria catastale incoerente, destinazione d'uso da mutare: quasi tutto è sanabile. Vanno prezzate (costo di sanatoria + valore del tempo perso), non usate come filtro binario. Solo i vincoli oggettivamente insanabili escludono.

**Errore da non commettere**: la skill non deve mai scartare un immobile perché un dato manca. Deve distinguere "non idoneo" da "non valutabile".

## 2. Input

### 2.1 Struttura della cartella

```
Pronto_Automation/
├── Avvia Prontobox Scouting.bat                      # NUOVO: doppio clic → apre il pannello
├── .claude/skills/prontobox-scouting/                # NUOVO: la skill (SKILL.md, scout.py, config.yaml, reference/, tests/)
├── pannello/                                         # NUOVO: interfaccia settimanale (§7)
├── opportunities/
│   ├── INPUT/
│   │   ├── ASTE/
│   │   │   └── dataset_<fonte>_completo.json        # 8 file, uno per portale
│   │   └── IMM_ID/
│   │       ├── current_week/
│   │       │   ├── dataset_idealista_recenti.json
│   │       │   └── dataset_immobiliare_recenti.json
│   │       └── past_week/                            # NON leggere: la gestisce run_opportunities_immid.py
│   └── OUTPUT/                                       # scritta SOLO dalla skill e dal pannello
│       ├── summary.json
│       ├── report_AAAA_MM_GG.json                    # uno per settimana
│       └── logs/                                     # log di ogni run
└── scrapers_v2/                                      # NON modificare il codice: il pannello si limita a lanciarlo
```

- **Canale `ASTE`** = tutti i file in `INPUT/ASTE/`. Oggi sono inventari **cumulativi**: gli scraper girano in modalità *resume*, saltano gli annunci già salvati e non li aggiornano mai. Il pannello li rigenera da zero ogni settimana (§7.2), ma la skill deve funzionare in entrambi i casi. In ogni caso le stesse aste ricompaiono di settimana in settimana: è il motivo per cui serve il controllo sullo storico (§4).
- **Canale `IMM_ID`** = solo i file in `INPUT/IMM_ID/current_week/`. Contengono già solo gli annunci nuovi o aggiornati negli ultimi 7 giorni (filtro fatto a monte da `run_opportunities_immid.py`). `past_week/` non va letta.
- La skill non modifica mai `INPUT/` né `scrapers_v2/`. Scrive solo in `OUTPUT/`.

Tutti i file hanno lo stesso involucro, **senza `schema_version`**:

```json
{
  "generated_at": "2026-09-16T14:31:08+00:00",
  "total": 35,
  "listings": {
    "<fonte>_<id>": { ...campi diversi per ogni fonte... }
  }
}
```

La chiave (`asteannunci_4538705`, `vgi_4623360`, `immobiliare_132452472`…) è l'**ID opportunità** usato in tutto il resto della skill. `total` può essere 0 e `listings` vuoto (oggi `dataset_astalegale_completo.json`): nessun errore.

### 2.2 Mappa dei campi per fonte

**Non esiste uno schema comune.** Il primo compito della skill è un adattatore per fonte che produce il record canonico di §3.0. Stato reale dei file al 16/09/2026:

| Fonte (prefisso chiave) | Canale | Tipo vendita | Prezzo | Superficie | Località | Scadenza | Tribunale / procedura |
|---|---|---|---|---|---|---|---|
| `asteannunci_` | ASTE | asta | `prezzo_base` `"176.000,00"` | assente | `indirizzo` `"Gorgonzola (MI), Via …"` | `data_asta` `"15/09/2026 - 15:30"` | `tribunale` `"Tribunale di Milano "` + `procedura` `"Proc. 36/2024"` |
| `asteflorio_` | ASTE | asta | `prezzo` `"31800.00"` (**punto decimale**) | assente | `titolo` `"Magazzino a Milano (MI)"`, `city` | assente | assente |
| `caseasta_` | ASTE | asta | `prezzo` `"€ 125.250"` | solo nel testo (`"TOTALI 165MQ"`) | `indirizzo` `"Milano (MI) - Via Neera 14"` | assente | assente |
| `fallcoaste_` | ASTE | asta | `prezzo` `"229.457,22"` | `superficie_mq` (3/7, poco affidabile) | nel `titolo` (`"… sito in Binasco (MI) …"`) | `data_termine_asta` `"17/09/2026 h 12:00"` | `tribunale` (spazi finali) + `procedura` `"Procedura n.42/2025"` |
| `medianord_` | ASTE | **vendita** (agenzia) | `prezzo` `"895.000"` o `null` | `superficie_mq` `"12.000"` | `comune` | — | — |
| `vgi_` | ASTE | asta | `prezzo_base` + `offerta_minima` | solo nel testo | `citta_completa` `"Toscana > Pisa > San Giuliano Terme"` (ultimo pezzo = comune), `regione` | `termine_presentazione_offerte` (ISO) — **è questa la scadenza vera**, non `data_vendita` | `tribunale` + `numero_procedura` + `anno_procedura` |
| `worldcapital_` | ASTE | **vendita** | `prezzo` `"4.875.000"` o `null` | `superficie_mq` `"10.500"` | `citta` | — | — |
| `astalegale_` | ASTE | asta | file vuoto oggi | — | — | — | — |
| `idealista_` | IMM_ID | vendita | `prezzo` `"6.000.000€"` | `superficie_mq` `"7.500 m²"` o dentro `details[]` | `titolo` `"Edificio a Isola, Milano"`, `city` | — | — |
| `immobiliare_` | IMM_ID | vendita, **ma asta se esiste `P.auction`** | `prezzo_valore` (intero, `null` = prezzo su richiesta) | `superficie_mq` `"733 m²"` + `P.surfaceConstitution` | `comune`, `provincia` (nome esteso), `zona`, `indirizzo`, `P.location.latitude/longitude` | `P.auction.saleDate` `"22/10/2026, 15:30"` | assente |

`P` = `raw_detail.pageProps.detailData.realEstate.properties[0]`.

Note che cambiano il comportamento:

- **`city` non è il comune**: è la città usata dallo scraper per la ricerca. Su case-asta il comune reale (da `indirizzo`) è diverso da `city` in 55 record su 102 (es. Gorle con `city: "Bergamo"`). Usare `city` solo come ultimo ripiego, con confidenza bassa.
- **vgi e asteannunci condividono gli ID** (stessa rete astegiudiziarie): `vgi_4614163` e `asteannunci_4614163` sono lo stesso lotto.
- **Tribunale + procedura non basta a identificare un lotto**: la procedura Ancona 60/2024 ha 5 lotti diversi su asteannunci.
- **`vgi_`**: `categoria: "MOBILI"` sono macchinari, non immobili. `sottocategoria` distingue industriale, commerciale, residenziale e "altra categoria". `descrizione_completa` (fino a ~3.900 caratteri) contiene il diritto (`"piena proprietà di…"`).
- **`idealista_`**: 96 annunci su 103 hanno `category: "vendita-garage"` (box e posti auto, `details` tipo `"Posto per macchina grande"`). Non hanno coordinate. La descrizione è troncata a ~370 caratteri.
- **`immobiliare_`** è la fonte più ricca. Da `P` leggere solo:
  - altezza: `industrial.underBeamHeight` e `industrial.barnHeight` (es. `"7,1 m"`). Ci sono errori di inserimento (`"650 m"`): i valori fuori da 2–20 m vanno scartati come non plausibili;
  - pianta: `industrial.bayes` (campate), `industrial.numberLoadingDock` (banchine), `surfaceConstitution` (superficie per piano), `floor.value` (interrato / seminterrato / piano terra / 12°), `floors`;
  - accessi e dotazioni: `features` (`"Passo carrabile"`, `"Recintato"`, `"Reception"`);
  - stato e dati catastali: `availability` (libero/occupato), `buildingYear`, `condition`, `cadastrals`;
  - se è un'asta: `auction` (data, custode, delegato).
- **`raw_detail` è pesante** (~12 MB per 35 annunci). Leggerlo con codice, estraendo solo i percorsi sopra, e **non caricarlo mai intero nel contesto del modello**.
- **`asteflorio_`** ha la descrizione uguale al titolo (~22 caratteri), 24 lotti su 41 a Roma, e prezzi molto bassi (29 su 41 sotto 10.000 €). Probabilmente sono cantine, posti auto o quote. Serve il fetch della pagina; un prezzo sotto `soglia_prezzo_anomalo` (default 10.000 €) va segnalato con il flag `PREZZO_ANOMALO`.

### 2.3 Qualità dei dati misurata (campione del 16/09/2026: 218 aste + 138 IMM_ID)

| Fonte | N | Superficie ricavabile | Scadenza | Coordinate | Altro |
|---|---|---|---|---|---|
| asteannunci | 27 | **0** (né campo né testo) | 27 | 0 | 1 asta già passata, 8 entro 1 giorno |
| asteflorio | 41 | **0** | 0 | 0 | descrizione = titolo |
| caseasta | 102 | 68 da testo | 0 | 0 | descrizione troncata a ~103 caratteri con `"..."` |
| fallcoaste | 7 | 3 da campo | 7 | 0 | contiene quote (`"Quota di 2/6"`) e garage fuori zona |
| medianord | 4 | 4 | — | 0 | prezzo `null` in 3 su 4 |
| venditegiudiziarie | 34 | 8 da testo | 34 | 0 | 21 a Pisa, 2 MOBILI, 9 residenziali |
| worldcapital | 3 | 3 | — | 0 | prezzo `null` in 1 su 3 |
| astalegale | 0 | — | — | — | file vuoto |
| idealista | 103 | 7 | — | 0 | 96 garage |
| immobiliare | 35 | 35 | 6 (aste) | 35 | altezza in 6 (1 non plausibile) |

Conseguenze di progetto:

- Per la maggior parte delle aste la superficie non c'è: **`DA APPROFONDIRE` sarà l'esito più frequente**, e il fetch della pagina o della perizia è necessario.
- IMM_ID oggi contiene solo Milano (run di test degli scraper). La skill non deve darlo per scontato.
- Stima sul campione, per i soli immobili nelle province target (esclusi garage, beni mobili, quote e aste troppo vicine): 14 hanno una superficie tra 1.000 e 3.000 m², 63 hanno superficie ignota (entrano in classifica come `DA APPROFONDIRE`), 86 sono sotto i 1.000 m². I candidati bastano per la top 10, ma molti richiederanno una verifica della superficie. Settimane con meno di 10 opportunità idonee restano possibili (§4.4).

## 3. Pipeline di valutazione

### Stadio 0 — Normalizzazione e arricchimento

1. **Adattatori per fonte.** Ogni record diventa un record canonico:

   ```
   id                  "<fonte>_<id>" (chiave originale)
   alias_ids           altri ID dello stesso immobile (da dedup)
   canale              "ASTE" | "IMM_ID"          # dalla cartella di provenienza, OBBLIGATORIO
   tipo_vendita        "asta" | "vendita"         # dal contenuto (medianord/worldcapital = vendita; immobiliare con auction = asta)
   fonte, file_origine, url, urls_altre_fonti
   titolo, descrizione
   prezzo_eur, offerta_minima_eur
   superficie_mq, superfici_etichettate[]
   comune, provincia (sigla), lat, lon
   scadenza_asta                                  # data entro cui bisogna agire
   tribunale, procedura ("N/AAAA"), lotto
   categoria           capannone | deposito | magazzino | opificio | laboratorio | garage | residenziale | mobili | altro
   altezza_m, piano, n_piani, stato_occupazione, anno_costruzione, diritto
   ```

2. **Parser dei prezzi.**
   - `"176.000,00"` → 176000; `"€ 125.250"` → 125250; `"6.000.000€"` → 6000000; `"€ 20.800,00"` → 20800; `"895.000"` → 895000.
   - `"31800.00"` (asteflorio: esattamente due decimali dopo il punto) → 31800.
   - `null`, `""`, `"prezzo su richiesta"` → `null`. Un prezzo `null` non è mai uno scarto: la valutazione diventa `DA APPROFONDIRE` con l'azione "chiedere il prezzo all'agenzia".

3. **Parser delle date.** Formati da gestire: `"15/09/2026 - 15:30"`, `"17/09/2026 h 12:00"`, `"22/10/2026, 15:30"` e ISO 8601. Fuso Europe/Rome.

4. **Comune e provincia**, in ordine di affidabilità:
   1. campo `comune`;
   2. pattern `"<Comune> (<PR>)"` in `indirizzo` o `titolo`;
   3. ultimo segmento di `citta_completa`;
   4. `"… a <Zona>, <Comune>"` nel titolo idealista;
   5. `city` / `citta` (confidenza bassa).

   Se il comune resta ignoto → `DATI_INSUFFICIENTI`, non scarto.

5. **Superficie.** Cercare in `titolo` + `descrizione_completa` + `details[]` pattern come `1.250 mq`, `mq 1250`, `1250MQ`, `m² 1.250`, `233,5 m²`, `superficie di 1250 metri quadrati`, `sup. commerciale`, `sup. catastale`. Se compaiono più superfici, tenerle tutte con etichetta (coperta / scoperta / commerciale / catastale) e usare la coperta per i calcoli. Valori sotto 5 m² o sopra 100.000 m² → confidenza bassa.

6. **Fetch condizionale.** Se superficie, scadenza o descrizione mancano (o la descrizione è troncata) **e** l'immobile ha superato il filtro geografico, fetchare `url`. Per le aste, cercare il link alla perizia/CTU sul PVP. Rate limit, cache in `OUTPUT/.cache/` (in `.gitignore`), un fetch fallito → `DATI_INSUFFICIENTI`, mai un crash.

7. **Estrazione di attributi dal testo libero**, solo se presenti in modo esplicito e mai per inferenza: altezza sotto trave, numero di piani, quota uffici, area esterna/piazzale, accesso carrabile, banchina, destinazione urbanistica, difformità/abusi, amianto, occupazione, diritto (piena proprietà / nuda proprietà / quota indivisa `"Quota di 2/6"`).

8. **Dedup e identità** (servono anche al controllo sullo storico di §4):
   1. stesso ID numerico tra `vgi_` e `asteannunci_` → stesso lotto;
   2. stesso `tribunale|procedura|lotto` normalizzati. Se il lotto non è ricavabile, usare `tribunale|procedura` + stesso comune + superficie ±5%. **Mai il prezzo**, perché cambia con i ribassi;
   3. solo per record con coordinate: distanza < 100 m + stessa categoria;
   4. idealista ↔ immobiliare: stesso comune + superficie ±3% + prezzo ±2% → unione con `confidenza: media`, dichiarata nella valutazione testuale.

   In caso di unione vince il record più ricco di dati. Gli altri ID vanno in `alias_ids`, i loro URL in `urls_altre_fonti`. Mai unire in silenzio due lotti con procedura uguale ma lotto diverso.

Ogni campo derivato porta con sé `{valore, fonte, confidenza}`, con `fonte ∈ {json, regex_testo, raw_detail, fetch_pagina, fetch_perizia, geocoding, stima}` e `confidenza ∈ {alta, media, bassa}`. **Vietato produrre un valore senza fonte.**

### Stadio 1 — Hard filter (scarto automatico)

Si applica **solo** su dati certi. Ogni filtro produce un motivo di scarto esplicito.

| # | Filtro | Regola | Note |
|---|---|---|---|
| 1 | Perimetro geografico | Comune in `comuni_target` o entro `raggio_km` da uno di essi, e non in `comuni_esclusi` | `comuni_target` = le 36 città degli scraper. Default di `comuni_esclusi`: Roma, Cagliari, Ancona (regola "escludi Roma e Centro-Sud/Adriatico") |
| 2 | Tipologia | Passano capannone, deposito, magazzino, opificio, laboratorio. Scarto: `garage` (idealista `vendita-garage`, "posto auto", "box"), `mobili` (vgi `MOBILI`), `residenziale` senza indizi industriali | `altro` e `commerciale` passano solo con indizi industriali o artigianali nel testo |
| 3 | Superficie | Si usa la **superficie dichiarata nell'annuncio** (lorda), non la SLN stimata. Fascia di riferimento: `superficie_min_mq` – `superficie_max_mq` (default **1.000 – 3.000 m²**). Sotto il minimo → scarto `SUPERFICIE_INSUFFICIENTE`. **Sopra il massimo → nessuno scarto**: resta in classifica con `penalita_sopra_max` nel voto (§3, Stadio 4) e il flag `FUORI_TAGLIA_GRANDE` ("verificare se si può dividere o affittare in parte"). Superficie ignota → **non scartare**, `DATI_INSUFFICIENTI` | Si scarta solo con superficie a confidenza alta o media: un valore preso dal testo con confidenza bassa (es. uno fra più numeri) porta a `DA APPROFONDIRE`. Entro `tolleranza_soglia_pct` (default 5%) sotto il minimo (950–999 m²) nessuno scarto, flag `AL_LIMITE_SOGLIA`. L'effetto dell'altezza non entra in questo filtro: lo pesano la scorecard e il gate economico (SLN) |
| 4 | Tempi dell'asta | Scadenza già passata → `MONITORA / ASTA_SCADUTA`. Scadenza − data della run < `giorni_minimi` (default 20) → `MONITORA / TEMPO_INSUFFICIENTE`. Tra 20 e 35 giorni → flag urgenza | Non è un difetto dell'immobile: se l'asta va deserta, il lotto tornerà ribassato |
| 5 | Diritto sull'immobile | Quota indivisa, nuda proprietà, diritto di superficie in scadenza → scarto | Esempio: `fallcoaste_1629594` "Quota di 2/6" |
| 6 | Vincolo insanabile | Inedificabilità assoluta, abuso sostanziale non sanabile, titolo originario non ricostruibile | **Unico knock-out urbanistico** |
| 7 | Prezzo fuori scala | `prezzo_eur / SLN_stimata` > `soglia_hard` (default 2.500 €/m² SLN) | Serve solo a tagliare i casi assurdi |

**Knock-out che NON si possono automatizzare.** Vanno messi nella checklist di due diligence e mai applicati come filtro:

- accesso carrabile esclusivo e spazio di manovra;
- amianto in copertura;
- rischio idraulico (PAI/PGRA);
- contaminazione del suolo su ex siti sensibili;
- umidità di risalita (da tenere d'occhio negli immobili immobiliare `interrato`/`seminterrato`);
- possibilità di mettere l'insegna;
- titolarità pulita.

A ciascuno si assegna un livello di allerta in base agli indizi nel testo, ad esempio:

- "fibrocemento" / "eternit" → allerta amianto;
- "ex galvanica" / "ex carrozzeria" / "ex distributore" → allerta bonifica;
- comune in zona alluvionata → allerta idraulica.

### Stadio 2 — Scorecard qualità (0-100)

Punteggio sul **potenziale di ricavo e sul rischio**, **indipendente dal prezzo**. Pesi in `config.yaml`, con questi default.

**MERCATO — 40**

| Criterio | Peso | Come calcolarlo |
|---|---|---|
| IQSS del comune | 12 | Lookup su `reference/iqss_comuni.csv` (36 comuni del deck). Comune assente ma in perimetro → valore interpolato sulla provincia, confidenza bassa |
| Saturazione del bacino a 10 minuti | 10 | Concorrenti entro 5 km da `lat`/`lon` in `reference/concorrenti.csv`. Metrica: m² di self-storage per abitante; allerta sopra 0,05. Senza coordinate → geocoding dell'indirizzo; se fallisce, range |
| Visibilità e traffico del fronte | 12 | **Non desumibile dai dati.** Proxy: tipo di strada dall'indirizzo e distanza dagli svincoli. Confidenza bassa → `DA_VERIFICARE_ON_SITE`, punteggio espresso come range |
| Qualità del bacino a 10 minuti | 6 | Barriere (ZTL, fiume, ferrovia); vicinanza a poli DIY/retail entro 2 km (`reference/poli_retail.csv`) |

**IMMOBILE — 38**

| Criterio | Peso | Come calcolarlo |
|---|---|---|
| Altezza utile e soppalcabilità | 13 | Scala: sotto 3,0 m = 0 · 3,0-4,4 m = 4 · 4,5-5,4 m = 8 · da 5,5 m = 13. Fonti: `P.industrial.underBeamHeight` (preferito) o `barnHeight`, altrimenti il testo. **Se ignota**, range per categoria: capannone/opificio [4, 13]; deposito/magazzino/laboratorio [0, 8]. Flag `ALTEZZA_DA_VERIFICARE`. Mai un valore puntuale inventato |
| Efficienza planimetrica | 11 | Campate (`P.industrial.bayes`), regolarità, numero di piani (`P.floors`, `surfaceConstitution`), quota uffici. Penalizzare uffici oltre il 20% e anche uffici a 0% (servono reception/showroom al 3-8%). Piano interrato, seminterrato o superiore al terra → forte penalità |
| Accessibilità operativa | 9 | Piazzale, accesso indipendente, banchine (`numberLoadingDock`), portoni. Da `P.features` (`"Passo carrabile"`, `"Recintato"`) o dal testo |
| Antincendio e struttura dell'involucro | 5 | Anno di costruzione (`P.buildingYear`: prefabbricato costruito prima del 2008 → allerta sismica), copertura, distanze dai confini, spazio per riserva idrica e locale pompe |

**RISCHIO E SVILUPPO — 22**

| Criterio | Peso | Scala |
|---|---|---|
| Percorso autorizzativo | 12 | Già compatibile e conforme = 12 · cambio d'uso nella stessa categoria funzionale senza opere = 10 · cambio d'uso con opere o permesso di costruire ordinario = 7 · difformità da sanare + cambio d'uso = 5 · permesso in deroga o variante puntuale = 2 |
| Espandibilità / apertura per fasi | 5 | Area adiacente libera, seconda campata, apertura in più fasi |
| Pulizia e tempi della transazione | 5 | Asta o libero mercato; immobile libero o occupato (`P.availability`); numero di aste già andate deserte; presenza del custode (`P.auction.auctionSubjects`) |

**Classi**: A da 75 · B 60-74 · C 45-59 · scarto sotto 45.

Quando un criterio è ignoto, calcolare **score minimo, atteso e massimo**. Se l'intervallo supera 15 punti, l'esito è `DA APPROFONDIRE` e non una classe.

### Stadio 3 — Gate economico

```
SLN = superficie_lorda × efficienza × (1 + fattore_soppalco)
  efficienza:       0,55-0,75   (default 0,68 se ignota)
  fattore_soppalco: 0 se h < 4,5 m | 0,4-0,6 se 4,5-5,4 m | 0,7-0,9 se ≥ 5,5 m

capex_totale = superficie_lorda × (
    baseline                    # 450-600 €/m², include antincendio 100-170
  + soppalco                    # 150-220 €/m² soppalcato, se applicabile
  + sismico                     # 80-200 €/m², se prefabbricato ante-2008 o intervento strutturale rilevante
  + strip_out                   # 30-80 €/m²
  + amianto                     # 35-60 €/m², se ci sono indizi
  − recuperi )                  # 0-60 €/m²

costo_all_in = prezzo + oneri_acquisto + costo_sanatoria + capex_totale
  oneri_acquisto: imposte, notaio, spese d'asta, commissioni (default 6%)
  costo_sanatoria: oblazione + tecnici, in base al percorso autorizzativo

EBITDA_annuo = SLN × tariffa_mq_anno × occupancy_regime × margine_ebitda
  default: tariffa 261 €/m²/anno · occupancy 0,82 · margine 0,58

YoC = EBITDA_annuo / costo_all_in
costo_per_mq_SLN = costo_all_in / SLN     # riferimento: ≤ 1.250 €/m² SLN per uno YoC del 10%
```

- **Scenari.** Tre scenari di tariffa (261 / 320 / 380 €/m²/anno). Se l'altezza è ignota, anche due scenari di altezza (senza soppalco / con soppalco). L'output è una matrice di YoC, non un numero singolo.
- **Prezzo di riferimento per le aste**: `prezzo_base`. Calcolare anche lo YoC su `offerta_minima` quando c'è.
- **Costo del tempo.** `mesi_al_go_live` dipende dal percorso autorizzativo (0 se conforme, 8-18 con irregolarità). Serve per lo sconto da chiedere in trattativa: `margine_mensile_mancato × mesi + oneri finanziari`.
- **Hurdle rate per classe**: A ≥ 9% · B ≥ 11% · C ≥ 14%.

### Stadio 4 — Esito e voto

| Esito | Condizione |
|---|---|
| **PROMUOVI** | Supera gli hard filter, ha una classe assegnata con confidenza sufficiente e YoC ≥ hurdle nello scenario base |
| **DA APPROFONDIRE** | Supera gli hard filter, ma un dato decisivo è ignoto (superficie, altezza, prezzo, destinazione) o l'intervallo di score è troppo ampio. **Deve dire quale dato sblocca la decisione e dove trovarlo** (perizia CTU, visura, sopralluogo, telefonata al custode o all'agenzia) |
| **MONITORA** | Scartato solo per tempi dell'asta (`TEMPO_INSUFFICIENTE`, `ASTA_SCADUTA`) o per prezzo ancora troppo alto |
| **SCARTA** | Hard filter violato, oppure YoC sotto l'hurdle in tutti gli scenari. Motivo sempre esplicito |

**Voto (0-100)** — è il valore su cui si costruisce la classifica:

```
voto = peso_qualita    × score_qualita_atteso          # default 0,70
     + peso_economico  × punteggio_economico           # default 0,30
     − coeff_incertezza × (score_max − score_min)      # default 0,20
     − penalita_sopra_max                              # solo se superficie > superficie_max_mq:
                                                       # 5 punti ogni 1.000 m² (o frazione) oltre il massimo, fino a 15

punteggio_economico (0-100), dallo YoC nello scenario base rispetto all'hurdle della classe:
    YoC ≤ hurdle − 5 pp → 0 · YoC = hurdle → 50 · YoC ≥ hurdle + 5 pp → 100 (lineare in mezzo)
    YoC non calcolabile (superficie o prezzo ignoti) → 50, neutro
```

Il voto si arrotonda all'intero. Tutti i coefficienti stanno in `config.yaml`.

## 4. Selezione settimanale delle 10 opportunità

È il cuore della skill. **Parsing, calcoli, classifica e confronto con lo storico sono codice deterministico.** Il modello interviene solo nella valutazione testuale e nei giudizi segnati come tali.

### 4.1 Classifica unica

```
candidati = opportunità con esito ∈ {PROMUOVI, DA APPROFONDIRE}     # ASTE e IMM_ID insieme
classifica = candidati ordinati per:
    1. voto (decrescente)
    2. YoC nello scenario base (decrescente)
    3. scadenza dell'asta (la più vicina prima; le vendite senza scadenza in fondo)
    4. id (ordine alfabetico, per rendere l'ordine stabile)
```

`MONITORA` e `SCARTA` non entrano mai in classifica. Ogni opportunità conserva il suo `canale` (`ASTE` o `IMM_ID`), che va riportato nell'output.

### 4.2 Controllo sullo storico e scorrimento

```
summary    = leggi OUTPUT/summary.json
data_run   = oggi (o --data)
file_run   = "report_" + data_run come AAAA_MM_GG + ".json"
summary_ok = righe del summary con file ≠ file_run          # una run ripetuta nello stesso giorno non si confronta con se stessa

segnalate = [] ; escluse_storico = []
per ogni opp nella classifica, in ordine:
    se len(segnalate) == n_segnalazioni (default 10): stop

    prec = segnalazione più recente in summary_ok per la stessa opportunità
           (match su id, poi su alias_ids, poi su identity_key)
           → se ci sono più righe, vince quella con la data più alta

    se prec è assente:
        opp.stato_segnalazione = "NUOVA"
        segnalate.append(opp); continua

    snapshot = apri OUTPUT/<prec.file>, cerca in opportunita[] l'elemento con id == prec.id
    se il file o l'elemento mancano:
        opp.stato_segnalazione = "NUOVA"
        opp.note += "segnalata il <prec.data> ma il file storico <prec.file> è illeggibile: confronto impossibile"
        segnalate.append(opp); continua

    confronto = confronta(snapshot, opp)                   # §4.3
    se confronto.esito == "MIGLIORATA":
        opp.stato_segnalazione = "RISEGNALATA"
        opp.motivi_risegnalazione = confronto.motivi       # frasi esplicite con valori prima → dopo
        segnalate.append(opp)
    altrimenti:                                            # "INVARIATA" o "PEGGIORATA"
        escluse_storico.append({opp, confronto})           # e si passa alla posizione successiva
```

Il risultato: se 3 delle prime 10 erano già state segnalate e non sono migliorate, il report contiene le altre 7 più l'11ª, la 12ª e la 13ª (o le successive, se anche queste sono escluse). Ognuna riporta la propria `posizione_nel_ranking` originale.

### 4.3 Regole di confronto

Si confronta lo `snapshot` salvato nel report precedente con il record attuale, sempre su grandezze omogenee: prezzo base con prezzo base, offerta minima con offerta minima.

**Cambi positivi** — basta uno solo per risegnalare, salvo quanto previsto sotto per i casi misti. Ognuno produce una frase in `motivi_risegnalazione`:

| Codice | Condizione | Esempio di motivo |
|---|---|---|
| `PREZZO_SCESO` | `(prezzo_ora − prezzo_prima) / prezzo_prima ≤ soglia_ribasso_pct` (default −3%) | "Prezzo base sceso da 180.000 € a 144.000 € (−20%); probabilmente l'asta del 12/09 è andata deserta" |
| `VOTO_SALITO` | `voto_ora − voto_prima ≥ soglia_delta_voto` (default +5) | "Voto salito da 61 a 70: è arrivata l'altezza sotto trave (7,1 m)" |
| `DATO_SBLOCCATO` | Un dato che prima mancava (superficie, altezza, prezzo, destinazione, scadenza) ora c'è **e** l'esito è migliorato (es. `DA APPROFONDIRE` → `PROMUOVI`) o l'intervallo di score si è ristretto di almeno 10 punti | "Ora c'è la superficie (1.850 m², dalla perizia): l'esito passa da DA APPROFONDIRE a PROMUOVI" |
| `ASTA_DI_NUOVO_AZIONABILE` | Nuova scadenza diversa dalla precedente e ≥ `giorni_minimi` dalla data della run | "Nuova asta il 28/11/2026: 73 giorni per preparare l'offerta" |

**Cambi negativi**, ciascuno con la sua frase:

- prezzo salito oltre il 3%;
- voto sceso di almeno 5 punti;
- esito peggiorato (es. `PROMUOVI` → `DA APPROFONDIRE`);
- dato nuovo sfavorevole (altezza bassa, immobile occupato, quota indivisa).

**Esito del confronto:**

- **MIGLIORATA** → almeno un cambio positivo **e** nessun peggioramento decisivo. Sono peggioramenti decisivi: esito peggiorato, oppure voto sceso di almeno 5 punti.
- **PEGGIORATA** → almeno un cambio negativo e nessun cambio positivo, oppure un cambio positivo accompagnato da un peggioramento decisivo. **Nei casi misti va riportato esplicitamente cosa è migliorato e cosa è peggiorato.**
- **INVARIATA** → nessun cambio oltre le soglie. Le variazioni sotto soglia (prezzo −1%, voto +2) sono rumore.

Casi particolari:

- **Ribasso anomalo** (`≤ soglia_ribasso_anomalo_pct`, default −50%): è più probabilmente un cambio del perimetro del lotto che un ribasso vero. Conta come `PREZZO_SCESO`, ma con il flag `VERIFICARE_PERIMETRO_LOTTO` nel motivo.
- **Configurazione cambiata** (`hash_config` diverso tra i due report): la differenza di voto può dipendere dai nuovi pesi e non dall'immobile. In questo caso `VOTO_SALITO` non basta da solo per risegnalare: servono cambi nei fatti (prezzo, dati, scadenza), e la cosa va scritta nel motivo.

### 4.4 Meno di 10 opportunità

Se la classifica finisce prima di arrivare a 10, il report contiene quelle che ci sono, con una nota esplicita in `meta` ("solo N opportunità idonee e con novità questa settimana"). **Non si riempiono mai i posti con opportunità `MONITORA` o `SCARTA`.** Se N = 0, si scrive comunque il report (vuoto, con statistiche e motivi), ma nessuna riga nel summary.

## 5. Output

### 5.1 `OUTPUT/report_AAAA_MM_GG.json`

Un file per run (es. `report_2026_09_16.json`), scritto in JSON UTF-8 con indentazione. È **il report settimanale**: non si producono file `.md` né `.xlsx`.

```json
{
  "meta": {
    "report_id": "2026_09_16",
    "file": "report_2026_09_16.json",
    "generato_il": "2026-09-16T18:02:11+02:00",
    "versione_skill": "2.0.0",
    "hash_config": "sha1 di config.yaml",
    "stato_summary": "ok | assente: prima run | corrotto: backup e ripartenza da zero",
    "input": [
      { "file": "INPUT/ASTE/dataset_caseasta_completo.json", "canale": "ASTE", "generated_at": "…", "n_listings": 102 }
    ],
    "statistiche": {
      "record_letti": 356,
      "duplicati_uniti": 3,
      "per_esito": { "PROMUOVI": 0, "DA APPROFONDIRE": 0, "MONITORA": 0, "SCARTA": 0 },
      "scarti_per_motivo": { "PERIMETRO_GEOGRAFICO": 0, "TIPOLOGIA": 0, "…": 0 },
      "in_classifica": 0,
      "posizioni_scorse": 0,
      "escluse_per_storico": { "INVARIATA": 0, "PEGGIORATA": 0 },
      "segnalate": { "totale": 10, "nuove": 0, "risegnalate": 0, "da_ASTE": 0, "da_IMM_ID": 0 }
    },
    "note": []
  },

  "opportunita": [
    {
      "posizione": 1,
      "posizione_nel_ranking": 3,
      "id": "caseasta_51067",
      "alias_ids": [],
      "identity_key": "…",
      "canale": "ASTE",
      "fonte": "case-asta.it",
      "tipo_vendita": "asta",
      "url": "https://…",
      "urls_altre_fonti": [],

      "stato_segnalazione": "NUOVA | RISEGNALATA",
      "motivi_risegnalazione": [],
      "segnalazione_precedente": null,

      "voto": 74,
      "esito": "PROMUOVI",
      "classe": "B",
      "score": { "min": 66, "atteso": 72, "max": 79, "breakdown": { "iqss": { "punti": 9, "max": 12, "fonte": "…", "confidenza": "alta" } } },
      "economico": {
        "sln_mq": 0, "costo_all_in_eur": 0, "costo_per_mq_sln": 0,
        "yoc": { "tariffa_261": { "senza_soppalco": 0.0, "con_soppalco": 0.0 }, "tariffa_320": {}, "tariffa_380": {} },
        "hurdle": 0.11, "mesi_al_go_live": 0, "sconto_da_chiedere_eur": 0
      },

      "snapshot": {
        "prezzo_eur": 0, "offerta_minima_eur": null, "superficie_mq": 0, "altezza_m": null,
        "comune": "…", "provincia": "…", "lat": null, "lon": null,
        "scadenza_asta": "2026-10-20", "tribunale": null, "procedura": null, "lotto": null,
        "categoria": "capannone", "stato_occupazione": null, "diritto": null,
        "dati_mancanti": ["altezza"]
      },
      "provenienza_campi": {
        "superficie_mq": { "valore": 1250, "fonte": "regex_testo", "confidenza": "media", "testo": "TOTALI 1.250MQ" }
      },
      "flag": ["ALTEZZA_DA_VERIFICARE"],
      "checklist_dd": [ { "voce": "amianto", "allerta": "media", "indizio": "copertura in fibrocemento" } ],
      "dati_mancanti": [ { "dato": "altezza sotto trave", "dove_trovarlo": "perizia CTU sul PVP", "azione": "scaricare la perizia dal link …" } ],

      "valutazione_testuale": "4-8 frasi in italiano: perché è in lista, punti di forza, rischi principali, cosa verificare per primo e, se RISEGNALATA, cosa è cambiato rispetto alla volta scorsa. I giudizi del modello vanno marcati con [giudizio]."
    }
  ],

  "escluse_per_storico": [
    {
      "id": "…", "canale": "IMM_ID", "posizione_nel_ranking": 2, "voto": 78,
      "esito_confronto": "INVARIATA | PEGGIORATA",
      "dettaglio": "Prezzo invariato (450.000 €), voto 78 → 79: nessun cambio sopra soglia",
      "segnalazione_precedente": { "data": "2026-09-09", "file": "report_2026_09_09.json" }
    }
  ],

  "scartate": { "PERIMETRO_GEOGRAFICO": ["asteflorio_84", "…"], "TIPOLOGIA": ["idealista_…"] },
  "monitora": [ { "id": "fallcoaste_1653419", "canale": "ASTE", "motivo": "TEMPO_INSUFFICIENTE", "scadenza_asta": "2026-09-17" } ]
}
```

Regole:

- **`snapshot` è obbligatorio**: la settimana dopo il confronto si fa leggendo proprio questo blocco. Deve contenere tutti i campi usati in §4.3.
- Per le opportunità `RISEGNALATA`, `segnalazione_precedente` = `{data, file, voto, esito, prezzo_eur}` e `motivi_risegnalazione` non può essere vuoto.
- `valutazione_testuale` non può essere vuota. Per una `RISEGNALATA` deve iniziare dal motivo della risegnalazione.
- `canale` è sempre presente e vale `ASTE` o `IMM_ID`.

### 5.2 `OUTPUT/summary.json`

È il registro di tutte le segnalazioni fatte. Si aggiunge una riga per ogni opportunità segnalata (nuova o risegnalata): **non si cancella e non si riscrive mai la storia**.

```json
{
  "meta": { "schema_version": 1, "aggiornato_il": "2026-09-16T18:02:12+02:00" },
  "segnalazioni": [
    {
      "id": "caseasta_51067",
      "data": "2026-09-16",
      "file": "report_2026_09_16.json",
      "canale": "ASTE",
      "identity_key": "…",
      "alias_ids": [],
      "stato_segnalazione": "NUOVA",
      "voto": 74
    }
  ]
}
```

`id`, `data` e `file` sono obbligatori. Gli altri campi servono solo a velocizzare la ricerca e a riconoscere le ripubblicazioni.

### 5.3 Ordine di scrittura e robustezza

1. Si valuta e si seleziona tutto in memoria. **Nessuna scrittura prima della fine.**
2. Si scrive `report_AAAA_MM_GG.json` in modo atomico (file temporaneo + rename).
3. Si fa il backup `summary.json.bak`, poi si scrive `summary.json` in modo atomico. Il summary si aggiorna sempre **dopo** il report, così non punta mai a un file che non esiste.
4. **Run ripetuta nello stesso giorno**: il report viene sovrascritto e dal summary si tolgono le righe con quel `file` prima di aggiungere le nuove. Stesso input + stessa data = stesso risultato (idempotenza).
5. **`summary.json` assente** → prima run: tutte le opportunità sono `NUOVA` e `meta.stato_summary = "assente: prima run"`.
6. **`summary.json` corrotto** → lo si rinomina in `summary.corrotto_<timestamp>.json`, si riparte da zero e lo si dichiara in `meta.stato_summary` e in `meta.note`.
7. **Campi nuovi o fonti nuove** in `INPUT/` (un nuovo `dataset_*.json`) → avviso in `meta.note`; si prosegue sui campi noti. Un file illeggibile si salta con un avviso, senza bloccare la run.

## 6. Configurazione esternalizzata

Tutto ciò che si può tarare sta in `config.yaml`, mai scritto nel codice. Generare `config.yaml` con i default sotto **e un commento per ogni parametro** che spieghi cosa succede se lo si cambia.

- **Percorsi**: `root` (cartella `Pronto_Automation`), `input_aste`, `input_immid`, `output_dir`, `formato_nome_report` (default `report_{AAAA}_{MM}_{GG}.json`).
- **Perimetro**: `comuni_target` (le 36 città degli scraper: Bolzano, Bologna, Trento, Padova, Milano, Parma, Modena, Reggio Emilia, Brescia, Monza, Bergamo, Verona, Vicenza, Firenze, Pavia, Como, Piacenza, Treviso, Novara, Cremona, Cesena, Forlì, Pisa, Udine, Varese, Ravenna, Ancona, Sesto San Giovanni, Cinisello Balsamo, Busto Arsizio, Roma, Legnano, Prato, Cagliari, Pistoia, Lucca), `comuni_esclusi` (default Roma, Cagliari, Ancona), `raggio_km`.
- **Filtri**: `superficie_min_mq` (1.000), `superficie_max_mq` (3.000), `tolleranza_soglia_pct` (5), `penalita_sopra_max` (5 punti per 1.000 m² oltre il massimo, massimo 15), `giorni_minimi` (20), `giorni_urgenza` (35), `soglia_hard` (2.500 €/m² SLN), `soglia_prezzo_anomalo` (10.000 €), `altezza_plausibile_m` ([2, 20]).
- **Scorecard**: pesi, soglie delle classi, hurdle per classe, soglia di saturazione del bacino.
- **Economia**: tariffe dei 3 scenari, occupancy, margine, voci di capex, oneri d'acquisto.
- **Voto**: `peso_qualita` (0,70), `peso_economico` (0,30), `coeff_incertezza` (0,20).
- **Selezione e storico**: `n_segnalazioni` (10), `soglia_ribasso_pct` (−3), `soglia_ribasso_anomalo_pct` (−50), `soglia_delta_voto` (5), `soglia_restringimento_range` (10).
- **Rete**: `fetch_abilitato`, `rate_limit_secondi`, `timeout_secondi`, `cache_giorni`.

Nel README va scritto chiaramente che **`tariffa_mq_anno = 261` è una media nazionale di settore e va sostituita con il ricavo per m² SLN realizzato da Prontobox**: è il dato che sposta di più tutte le soglie di prezzo (±30-50%).

File di riferimento da creare in `reference/`:

- `iqss_comuni.csv`: 36 comuni con IQSS, popolazione, reddito, quota di case in affitto e pressione competitiva, estratti dal deck allegato;
- `concorrenti.csv`: sedi self-storage note, con coordinate;
- `sedi_prontobox.csv`: per valutare la cannibalizzazione;
- `poli_retail.csv`: poli DIY e retail.

## 7. Pannello settimanale: un clic avvia tutto

L'utente non deve aprire terminali, lanciare script a mano o sapere quali file esistono. Una volta a settimana apre il pannello, preme un pulsante e, a fine run, trova le 10 opportunità.

### 7.1 Come lo usa l'utente

1. **Apre il pannello.** Doppio clic su `Avvia Prontobox Scouting.bat` (nella cartella `Pronto_Automation`, con un collegamento sul Desktop creato dal README). Parte un piccolo server locale e si apre il browser su `http://127.0.0.1:8765`.
2. **Vede a che punto è.** La home mostra l'ultimo report e da quanti giorni non si fa una run. Oltre `giorni_promemoria` (default 7) l'avviso è in evidenza.
3. **Preme Avvia ricerca settimanale.** Scraping e valutazione partono da soli, in sequenza, e il pannello mostra l'avanzamento.
4. **Legge i risultati.** A fine run il pannello passa da solo alla schermata con le 10 opportunità.

### 7.2 Cosa succede dopo il clic

Le fasi sono sempre queste, in quest'ordine, **senza nessuna scelta per l'utente**:

| Fase | Cosa lancia | Note |
|---|---|---|
| 1. Controlli | Verifica che `python` ci sia, che `claude` sia installato e con un account connesso, che ci sia spazio su disco e che non ci sia già una run in corso | Se manca qualcosa, messaggio chiaro con la soluzione **prima** di lanciare gli scraper, non dopo ore |
| 2a. Scraping aste | Gli 8 `scrapers_v2/scraper_<fonte>_master.py`, in parallelo, come processi figli | Sempre con `--no-resume` (vedi sotto). **Non** usare `run_tutti_gli_scraper.bat`: apre finestre separate con `start` (il pannello non saprebbe quando finiscono) e si ferma su `pause` |
| 2b. Scraping IMM_ID — **ogni settimana, completo + recenti** | Una catena **in sequenza**, un Chrome alla volta: **(1)** `scraper_idealista_master.py --headless=true --max-pages 60` → **(2)** `scraper_immobiliare_master.py --headless=true --max-pages 60` → **(3)** `run_opportunities_immid.py`, senza `--cities` (tutte le 36 città) e headless | Parte insieme alla 2a, come fa oggi il `.bat`: gli scraper aste non usano il browser. **(1)** e **(2)** aggiornano gli archivi completi `scrapers_v2/dataset_*_completo.json`, che **(3)** usa come riferimento per capire cosa è recente: per questo vengono prima. **(3)** gestisce da solo `current_week` e `past_week`. Se **(1)** o **(2)** falliscono, **(3)** parte comunque, con l'avviso "riferimento non aggiornato" |
| 3. Valutazione | Claude Code con la skill (§7.3) | Parte quando **tutti** i processi della fase 2 sono terminati, bene o male |
| 4. Controllo finale | Verifica che `report_AAAA_MM_GG.json` esista, sia JSON valido e rispetti lo schema di §5.1, e che `summary.json` sia aggiornato | La run è riuscita solo se lo dice il file, non se lo dice il messaggio finale di Claude |

**Perché `--no-resume` per le aste.** In modalità *resume* lo scraper salta gli annunci già presenti nel file (es. `scraper_caseasta_master.py`, riga 250), quindi:

- un ribasso di prezzo sullo stesso annuncio non arriva mai nel file, e la regola `PREZZO_SCESO` (§4.3) non può scattare;
- le aste chiuse o rimosse dai portali restano nel file per sempre.

Per evitare di perdere i dati se uno scraper si interrompe a metà, il pannello:

- lo fa scrivere su un file temporaneo con `--output` (oggi tutti gli 8 scraper aste supportano sia `--output` sia `--no-resume`);
- sostituisce il file in `INPUT/ASTE/` **solo** se lo scraper termina con codice 0;
- altrimenti tiene il file della settimana prima e lo segnala.

Il codice 0 però non basta a dire che tutto è andato bene: gli scraper registrano nel log gli errori sulla singola città e poi proseguono. Per questo il pannello:

- conta le righe `[ERROR]` e `[WARNING]` del log di ogni scraper e le mostra;
- se il nuovo file ha meno della metà degli annunci della settimana prima, lo usa comunque ma lo segnala in evidenza (possibile blocco del sito o cambio di layout).

**Errori e durata.**

- **Durata.** Su 36 città la run può durare diverse ore: idealista impiega circa 12 minuti per la sola Milano in modalità recenti, e lo scraping completo di idealista e immobiliare è molto più lungo (l'archivio immobiliare pesa già 1,2 GB). Il pannello deve reggere tempi lunghi:
  - la run gira in un **processo separato dal server**, così chiudere il browser o riaprire il pannello non la ferma;
  - riaprendo il pannello si vede la run in corso;
  - durante la run il PC non deve andare in sospensione (su Windows `SetThreadExecutionState`), e il pannello avvisa di non spegnerlo.
- **Una fonte fallisce**: le altre vanno avanti. La valutazione parte comunque con i dati disponibili, e il report indica in `meta.note` quali fonti non sono aggiornate e a quando risalgono i loro file.
- **La valutazione fallisce**: compare il pulsante **Riprova solo la valutazione**, che rifà le fasi 3-4 senza rifare lo scraping.
- **Interrompi**: termina tutti i processi figli, compresi i Chrome aperti dagli scraper, e lascia la run nello stato "interrotta".
- **Una sola run alla volta**: il lock è `OUTPUT/.run.lock` e contiene il PID. Un lock rimasto da un processo che non esiste più va riconosciuto e rimosso.
- **Report di oggi già presente**: il pulsante diventa **Rilancia** e chiede conferma.

### 7.3 Avvio di Claude Code

- **Installazione della skill**: come skill di progetto in `Pronto_Automation/.claude/skills/prontobox-scouting/`, così Claude Code la trova quando viene lanciato da quella cartella.
- **Account**: il pannello usa Claude Code installato sul PC, con l'account già connesso. Se non è connesso, lo dicono i controlli della fase 1 ("apri Claude Code ed effettua l'accesso").
- **Comando**: il pannello lancia Claude Code in modalità non interattiva, dalla cartella `Pronto_Automation`, con un prompt tipo *"Esegui la skill prontobox-scouting con data AAAA-MM-GG"*. Riferimento: `claude -p "…" --output-format stream-json --allowedTools "…"`. **Le opzioni vanno verificate con `claude --help` sulla versione installata** e messe in `config.yaml`, non scritte nel codice.
- **Permessi**: in modalità non interattiva Claude non può chiedere conferme, quindi va autorizzato in anticipo **solo** a:
  - eseguire `python scout.py`;
  - leggere `opportunities/INPUT/`;
  - scrivere in `opportunities/OUTPUT/`;
  - aprire le pagine dei portali delle fonti.

  Niente permessi generici e niente disattivazione totale dei controlli.
- **Divisione del lavoro**: la parte di Claude deve essere piccola e ben delimitata, e il codice fa tutto il resto.
  1. `scout.py prepara`: normalizza, applica i filtri e scrive un file di lavoro con i soli testi da interpretare (solo per gli immobili che hanno superato gli hard filter).
  2. Claude legge quel file e scrive i giudizi: percorso autorizzativo, indizi di rischio, attributi dal testo.
  3. `scout.py seleziona`: calcola score, voto e classifica, fa il controllo sullo storico e sceglie le 10.
  4. Claude scrive la `valutazione_testuale` delle 10 e ne verifica i `motivi_risegnalazione`.
  5. `scout.py scrivi`: valida e scrive report e summary (§5.3).
- **Limiti e log**: timeout `timeout_valutazione_min` (default 60). Tutto l'output di Claude va in `OUTPUT/logs/`.

### 7.4 Schermate

**Home**

- Titolo "Prontobox Scouting", data dell'ultima run e badge "Aggiornato" oppure "Da aggiornare — ultima run 9 giorni fa".
- Un pulsante grande **Avvia ricerca settimanale**. Sotto: la durata stimata (media delle ultime run) e il promemoria di lasciare il PC acceso.
- Anteprima dell'ultimo report: numeri chiave e prime 3 opportunità.

**Avanzamento**

- Barra a step con le fasi di §7.2.
- Una riga per ogni scraper: stato (in attesa / in corso / completato / errore), tempo trascorso, annunci trovati (dal log o dal file) e ultima riga di log.
- Tempo totale trascorso e tempo residuo stimato (se ci sono run precedenti).
- Pulsante **Interrompi** e log completi apribili.

**Risultati** — la schermata più curata

- **Numeri chiave**: annunci analizzati, idonei, segnalati (nuovi / risegnalati), quanti da ASTE e quanti da IMM_ID, fonti con errori.
- **10 schede, in ordine di posizione.** Ogni scheda mostra:
  - posizione e voto, grande e colorato per fascia;
  - badge del canale **ASTE** / **IMM_ID**, ben distinguibili;
  - badge **NUOVA** / **RISEGNALATA**, esito e classe;
  - comune e provincia, tipologia, superficie, prezzo, €/m² SLN, YoC (range);
  - scadenza dell'asta con conto alla rovescia (in rosso sotto `giorni_urgenza`);
  - link all'annuncio e alle altre fonti dello stesso immobile.
- **Per le RISEGNALATE**: un riquadro in evidenza "Perché torna" con i motivi e il confronto prima → dopo (prezzo, voto, esito, data dell'asta).
- **Dentro la scheda**:
  - la valutazione testuale;
  - "Dati da verificare", con l'azione concreta per ciascuno;
  - checklist di due diligence e matrice YoC, richiudibili.
- **Filtri e ordinamento**: filtri per canale, esito e nuove/risegnalate; ordinamento per voto, prezzo o scadenza.
- **Sezione richiusa "Già segnalate, non riproposte"**: le opportunità escluse dal controllo sullo storico, con il motivo.
- Pulsante **Stampa / Salva PDF**.

**Storico**

- Elenco dei report passati (da `summary.json` e `OUTPUT/`); ciascuno si apre nella stessa vista dei Risultati.
- Ricerca di un immobile (per ID o comune): le date in cui è stato segnalato e come sono cambiati prezzo e voto.

**Stile**

- Pulito e professionale, tutto in italiano, pensato per lo schermo di un portatile ma leggibile anche su tablet.
- Tema chiaro e scuro.
- Numeri in formato italiano: `1.250 m²`, `144.000 €`, `10,4%`, date `16/09/2026`.
- Funziona **offline**: CSS e JS inclusi nel progetto, nessun servizio esterno.

### 7.5 Vincoli tecnici del pannello

- **Tecnologia**: libreria standard di Python (`http.server`, `threading`, `subprocess`) o al massimo una dipendenza leggera, dichiarata. Pagine HTML/CSS/JS statiche, nessuna compilazione del frontend.
- **Sicurezza**: il server ascolta **solo su `127.0.0.1`**. Le azioni (avvia, interrompi, riprova, rilancia) sono richieste POST con un token generato all'avvio e inserito nella pagina, così nessun altro sito può attivarle.
- **Stato della run**: in `OUTPUT/.run_state.json` (fase, processi, tempi, esiti), scritto in modo atomico. La pagina lo legge ogni 2 secondi.
- **Nessuna logica di valutazione nel pannello**: mostra solo ciò che c'è in `report_*.json` e `summary.json`.
- **Porta**: configurabile. Se è occupata, il pannello usa la prima libera e apre il browser su quella.
- **Log**: un file per run, `OUTPUT/logs/run_AAAA_MM_GG.log`, con controlli, output degli scraper e output di Claude.
- **Modalità `--simula`**: sostituisce scraper e Claude con script finti e veloci che usano i file di `tests/fixtures/`. Serve a provare tutto il pannello in un minuto.
- **Parametri in `config.yaml`** (sezione `pannello`): `porta` (8765), `giorni_promemoria` (7), `timeout_valutazione_min` (60), `timeout_scraper_min` (per fonte), `scraper_aste` (elenco script e argomenti), `catena_immid` (i tre comandi della fase 2b, in ordine, con i loro argomenti: vanno modificati da qui, non nel codice, se cambiano gli scraper), `comando_claude` (comando e opzioni), `impedisci_sospensione` (true).

## 8. Vincoli implementativi

- **Python ≥ 3.11** (sul PC gira 3.14), dipendenze minime e dichiarate in `requirements.txt`.
- **Si lancia con un comando**: `python scout.py --root "C:\Users\...\Pronto_Automation" --config config.yaml [--data 2026-09-16] [--dry-run]`.
  - `--data` simula la data della run: serve per i test e per rifare una settimana.
  - `--dry-run` fa tutto senza scrivere in `OUTPUT/`.
  - `prepara`, `seleziona` e `scrivi` (§7.3) sono sottocomandi dello stesso script.
- **Il pannello si lancia** con `python pannello/pannello.py [--simula]`: è quello che fa il `.bat`, che deve anche trovare Python da solo e mostrare un messaggio chiaro se non c'è.
- **Compatibile con Windows**: `pathlib`, `encoding="utf-8"` esplicito in lettura e scrittura, nessun comando di shell, nomi di file senza `:`.
- **Deterministica dove possibile.** Parsing, calcoli economici, scoring, classifica e confronto con lo storico sono codice. Il modello interviene solo su: lettura del testo libero, classificazione del percorso autorizzativo, indizi di rischio e `valutazione_testuale`. Ogni giudizio del modello va marcato come tale.
- **Idempotente e riavviabile**: vedi §5.3.
- **Economa di rete**: fetch solo per i record che hanno superato il filtro geografico, con cache, rate limit e gestione dei fallimenti.
- **Robusta ai campi `null`** e ai tipi misti (`superficie_mq` a volte stringa, a volte `null`). Nessuna eccezione non gestita su input parziali.
- **Economa di memoria**: `raw_detail` di immobiliare va letto estraendo solo i percorsi di §2.2 e mai passato al modello.
- **Scrive solo in `OUTPUT/`**: la skill (sia `scout.py` sia Claude) non scrive altrove. Il pannello lancia gli scraper, che scrivono i propri file in `INPUT/` come fanno oggi. Né la skill né il pannello modificano il codice degli scraper.

## 9. Test da includere

Costruire i casi di test a partire dai file reali di `opportunities/INPUT/` (copiati in `tests/fixtures/`), con `--data 2026-09-16`.

**Normalizzazione e valutazione**

1. **Fuori perimetro**: un `asteflorio_*` a Roma → `SCARTA / PERIMETRO_GEOGRAFICO`.
2. **Garage**: un `idealista_*` con `category: vendita-garage` → `SCARTA / TIPOLOGIA`.
3. **Beni mobili**: un `vgi_*` con `categoria: MOBILI` → `SCARTA / TIPOLOGIA`.
4. **Quota indivisa**: `fallcoaste_1629594` ("Quota di 2/6") → `SCARTA / DIRITTO`.
5. **Superficie assente**: `asteannunci_4625557` (Busto Arsizio VA, asta a 42 giorni) → `DA APPROFONDIRE`, con "superficie assente: consultare la perizia CTU".
6. **Asta imminente**: `fallcoaste_1653419` (Binasco, asta il 17/09/2026) → `MONITORA / TEMPO_INSUFFICIENTE`, non segnalata.
7. **Asta già passata**: `asteannunci_4538705` (Gorgonzola MI, asta il 15/09/2026) → `MONITORA / ASTA_SCADUTA`.
8. **Dedup tra portali**: `vgi_4614163` + `asteannunci_4614163` → un solo record, entrambi gli URL conservati.
9. **Stessa procedura, lotti diversi**: i 5 lotti di Ancona 60/2024 su asteannunci **non** vengono uniti.
10. **Parser dei prezzi**:
    - `"176.000,00"` → 176000
    - `"31800.00"` → 31800
    - `"€ 125.250"` → 125250
    - `"6.000.000€"` → 6000000
    - `"€ 20.800,00"` → 20800
    - `null` → `null`
11. **Parser delle date**: `"15/09/2026 - 15:30"`, `"17/09/2026 h 12:00"`, `"22/10/2026, 15:30"` e ISO vengono tutti letti correttamente.
12. **Comune ≠ città di ricerca**: un `caseasta_*` di Gorle con `city: Bergamo` → comune = Gorle.
13. **Altezza da immobiliare**: `immobiliare_131398054` → altezza 7,1 m (`underBeamHeight`, confidenza alta); `immobiliare_130814250` (`"650 m"`) → valore scartato come non plausibile e flag `ALTEZZA_DA_VERIFICARE`.
14. **Immobiliare all'asta**: un `immobiliare_*` con `P.auction` → `tipo_vendita: asta`, `canale: IMM_ID`, scadenza letta da `saleDate`.
15. **Prezzo su richiesta**: `immobiliare_130966026` (`prezzo_valore: null`) o un medianord con `prezzo: null` → `DA APPROFONDIRE` con azione "chiedere il prezzo", mai scarto.
16. **File vuoto**: `dataset_astalegale_completo.json` vuoto → nessun errore.
17. **Fascia di superficie**:
    - `immobiliare_131138538` (1.200 m²) → supera il filtro;
    - `immobiliare_132452472` (733 m²) e `medianord_capannoni-industriali_v1420` (843 m²) → `SCARTA / SUPERFICIE_INSUFFICIENTE`;
    - `worldcapital_RH-260148` (10.500 m²) → **non scartato**: flag `FUORI_TAGLIA_GRANDE` e penalità di 15 punti nel voto;
    - un record a 970 m² → nessuno scarto, flag `AL_LIMITE_SOGLIA`;
    - una superficie da testo con confidenza bassa sotto i 1.000 m² → `DA APPROFONDIRE`, non scarto.

**Selezione e storico**

18. **Prima run**: con `OUTPUT/` vuota vengono creati `summary.json` e `report_2026_09_16.json`, tutte le opportunità sono `NUOVA` e `stato_summary = "assente: prima run"`.
19. **Invarianza**: seconda run con `--data 2026-09-23` e stesso input → nessuna delle 10 della prima run compare di nuovo. Tutte finiscono in `escluse_per_storico` come `INVARIATA` e il report contiene le posizioni successive della classifica.
20. **Stessa data due volte**: il report viene sovrascritto, il summary non contiene righe doppie e il risultato è identico.
21. **Ribasso**: lotto segnalato a 500.000 €, ora a 375.000 € → `RISEGNALATA`, motivo `PREZZO_SCESO` con "−25%" e i due prezzi.
22. **Voto salito**: +6 punti a parità di configurazione → `RISEGNALATA` con motivo `VOTO_SALITO` e la causa.
23. **Voto salito per nuova configurazione**: +6 punti ma `hash_config` diverso e fatti invariati → `INVARIATA`, con la spiegazione.
24. **Dato sbloccato**: la superficie prima mancava, ora c'è e l'esito passa da `DA APPROFONDIRE` a `PROMUOVI` → `RISEGNALATA` con motivo `DATO_SBLOCCATO`.
25. **Nuova asta**: nuova data a 60 giorni → `RISEGNALATA` con motivo `ASTA_DI_NUOVO_AZIONABILE`.
26. **Peggiorata**: prezzo +10% o voto −8 → esclusa, in `escluse_per_storico` come `PEGGIORATA` con il dettaglio.
27. **Caso misto**: prezzo −5% ma esito peggiorato → non risegnalata, dettaglio con entrambi i cambi.
28. **Scorrimento**: 4 delle prime 10 già segnalate e invariate → il report contiene 10 opportunità, prese dalle posizioni 1-14 al netto delle 4, con `posizione_nel_ranking` corretta.
29. **Meno di 10**: solo 6 opportunità idonee → report con 6, nota in `meta`, nessun riempimento con `MONITORA`/`SCARTA`.
30. **Più righe per lo stesso ID**: il confronto usa la più recente.
31. **File storico mancante**: il summary punta a un file cancellato → `NUOVA` con nota, nessun crash.
32. **Summary corrotto**: backup, ripartenza da zero, dichiarato in `meta`.
33. **Ripubblicazione**: stesso `tribunale|procedura|lotto` con un nuovo ID → riconosciuta come già segnalata.
34. **Alias**: segnalata come `asteannunci_4614163`, oggi il record vincente è `vgi_4614163` → riconosciuta tramite `alias_ids`.
35. **Validità del report**: ogni elemento di `opportunita[]` ha `canale ∈ {ASTE, IMM_ID}`, `voto` intero tra 0 e 100, `valutazione_testuale` non vuota e `snapshot` completo. Ogni `RISEGNALATA` ha `motivi_risegnalazione` non vuoto.

**Pannello** (con `--simula`, salvo dove indicato)

36. **Sequenza**: un clic su Avvia → controlli; poi, in parallelo, gli 8 scraper aste e la catena IMM_ID; poi la valutazione, solo quando è finito tutto; poi il controllo finale. Alla fine si apre la schermata Risultati. La catena IMM_ID gira sempre nell'ordine idealista completo → immobiliare completo → `run_opportunities_immid.py`, con un solo Chrome alla volta.
37. **Catena IMM_ID con errore**: se lo scraper completo di immobiliare fallisce, `run_opportunities_immid.py` parte comunque e il pannello mostra "riferimento non aggiornato".
37. **Una sola run**: un secondo clic durante una run viene rifiutato con un messaggio. Un lock rimasto da un processo morto viene rimosso.
38. **Scraper in errore**: uno scraper esce con codice ≠ 0 → gli altri proseguono, il file della settimana prima resta in `INPUT/ASTE/`, la riga è rossa nel pannello e `meta.note` del report lo dice.
39. **Scraper interrotto a metà**: il file in `INPUT/ASTE/` non cambia (scrittura su file temporaneo).
40. **Claude mancante o non connesso**: errore nella fase 1 con la soluzione; nessuno scraper viene lanciato.
41. **Valutazione fallita**: "Riprova solo la valutazione" rifà le fasi 3-4 e non rilancia gli scraper.
42. **Report non valido**: Claude termina "con successo" ma il report manca o non rispetta lo schema → run segnata come fallita.
43. **Browser chiuso**: chiuso e riaperto a metà run, il pannello mostra la run in corso con lo stato corretto.
44. **Interrompi**: tutti i processi figli terminati (verificare che non restino `python.exe` o `chrome.exe` della run), lock rimosso, stato "interrotta".
45. **Rilancio nello stesso giorno**: con il report di oggi già presente, il pulsante chiede conferma prima di ripartire.
46. **Sicurezza**: una POST senza token valido viene rifiutata; il server non risponde da un altro PC della rete.
47. **Schermata Risultati**: mostra 10 schede (o N < 10 con la nota), ognuna con badge del canale, voto, badge NUOVA/RISEGNALATA, "Perché torna" per le risegnalate e numeri in formato italiano. Funziona senza connessione internet.
48. **Storico**: con 3 report in `OUTPUT/`, lo storico li elenca tutti; cercando un ID segnalato due volte si vedono entrambe le date.

---

**Nota finale per chi genera la skill**: il valore di questa skill non sta nel classificare bene i pochi immobili idonei, ma nel non perdere quelli che sembrano brutti. Un capannone da svuotare, con difformità urbanistiche e in asta deserta al terzo ribasso, è esattamente il profilo che Prontobox vuole trovare e che tutti gli altri scartano. Il controllo sullo storico serve a una cosa sola: **ogni settimana, 10 opportunità che valga la pena guardare adesso**, senza ripetere quelle già viste e senza perdere quelle che nel frattempo sono migliorate. In caso di dubbio, ogni scelta di design deve andare verso "segnala e spiega cosa verificare", mai verso "scarta".
