# Verifiche sugli scraper prima di generare la skill `prontobox-scouting`

La skill e il pannello non riscrivono gli scraper: li lanciano e leggono i file che producono. Funzionano bene solo se gli scraper rispettano un "contratto" preciso: stesse opzioni da riga di comando, stesso formato dei file, campi completi, copertura di tutte le zone.

Questo documento ha due parti:

1. la **checklist** di ciò che deve risultare vero, con quello che ho già trovato nel codice il 16/09/2026;
2. il **prompt da incollare in Claude Code** per fare verifiche e correzioni.

---

## Parte 1 — Checklist

Priorità: **ALTA** = senza questo la skill lavora su dati sbagliati o incompleti · **MEDIA** = migliora molto la qualità · **BASSA** = comodità.

### A. Contratto comune a tutti i 10 script

| # | Verifica | Stato attuale | Priorità |
|---|---|---|---|
| A1 | Gli 8 scraper aste scrivono in `opportunities/INPUT/ASTE/dataset_<fonte>_completo.json`; idealista e immobiliare completi in `scrapers_v2/dataset_<fonte>_completo.json` | OK nei default attuali | ALTA |
| A2 | Ogni file ha la forma `{"generated_at", "total", "listings": {"<fonte>_<id>": {...}}}` | OK | ALTA |
| A3 | **Nomi dei campi invariati.** Si possono aggiungere campi nuovi; rinominare o togliere quelli esistenti rompe la skill (la mappa campi è nel §2.2 del prompt della skill) | Da tenere d'occhio nelle modifiche | ALTA |
| A4 | Tutti accettano `--output` e `--no-resume`. Le aste accettano anche `--cities`; idealista e immobiliare anche `--headless`, `--max-pages` e `--recent-only` | OK, da ricontrollare dopo le modifiche | ALTA |
| A5 | Uscita con codice **1** in caso di errore grave e **0** a fine lavoro. L'errore su una singola città finisce nel log come `[ERROR]` e lo script prosegue | Sembra OK, da provare | ALTA |
| A6 | L'ultima riga del log è sempre `Scraping completato. Totale annunci nel dataset: N` (il pannello la legge per il conteggio) | OK negli script visti | MEDIA |
| A7 | Salvataggio atomico (file temporaneo + rename) | OK | MEDIA |
| A8 | `requirements.txt` contiene **tutte** le dipendenze | **Manca `requests`**, che usano gli 8 scraper aste. C'è la nota su `playwright install chrome` | MEDIA |

### B. Copertura: trovano tutto quello che serve?

| # | Verifica | Stato attuale | Priorità |
|---|---|---|---|
| B1 | **Stesso criterio geografico per tutti: le province delle 36 città**, non solo il comune capoluogo. Il filtro fine (raggio, comuni esclusi) lo fa la skill | **Misto.** asteannunci, fallcoaste e venditegiudiziarie filtrano per provincia. astalegale cerca il nome della città nell'URL, asteflorio e caseasta usano la pagina del comune: così si perdono i comuni dell'hinterland (es. Gorgonzola, San Donato Milanese) | ALTA |
| B2 | Categorie: capannoni, magazzini, depositi, laboratori, opifici | idealista è appena passato a `vendita-negozi` ("Locali o capannoni"): include anche i negozi, e la skill li filtra. asteannunci esclude `locali-commerciali` (scelta voluta) | ALTA |
| B3 | **La paginazione arriva fino all'ultima pagina** | **astalegale**: si ferma a pagina 15-16 (~170 annunci per tutta Italia, pochi per quel portale) e oggi trova 0 annunci pertinenti. **caseasta**: il codice stesso dice che oltre ~25 annunci per comune potrebbe perderne. **fallcoaste**: corretto oggi. idealista e immobiliare: verificare che `--max-pages 60` basti, con `vendita-negozi` gli annunci sono molti di più | ALTA |
| B4 | **Confronto con il sito**: per 2 città (es. Milano e Bergamo) il numero di annunci trovati è uguale a quello mostrato sul sito con gli stessi filtri | Da fare | ALTA |
| B5 | Lo stesso annuncio ha **lo stesso ID** tutte le settimane | Da verificare (serve al confronto con lo storico) | ALTA |

### C. Dati: ci sono i campi che servono alla valutazione?

Per ogni annuncio pertinente servono:

- prezzo (per le aste: prezzo base **e** offerta minima);
- superficie;
- comune **e** sigla della provincia;
- indirizzo;
- per le aste: data dell'asta e termine per le offerte, tribunale, numero di procedura e lotto;
- descrizione **completa** (non troncata);
- tipologia;
- url.

La regola: se la pagina elenco non li ha, lo scraper apre la **pagina di dettaglio**, ma **solo per gli annunci già filtrati** (sono poche decine a settimana, quindi è veloce).

| # | Fonte | Cosa manca oggi | Priorità |
|---|---|---|---|
| C1 | asteannunci | Superficie (0 su 27) e descrizione vera: oggi `descrizione_completa` è solo il testo della card. Legge solo le card, mai il dettaglio | ALTA |
| C2 | asteflorio | Descrizione (è uguale al titolo), data dell'asta, tribunale e superficie. **Prezzi sospetti**: 29 su 41 sotto 10.000 € (es. "Magazzino a Roma" a 2.221,50 €). Verificare sul sito a cosa corrisponde quel prezzo: base d'asta, cauzione, rata? | ALTA |
| C3 | caseasta | Descrizione troncata a ~103 caratteri con "...", data dell'asta, tribunale e procedura. La superficie c'è solo a volte nel titolo | ALTA |
| C4 | venditegiudiziarie | L'API Typesense restituisce custode, delegato, dati catastali e dettaglio dei beni, **ma non vengono salvati**. Salvarli, e ricavare la superficie dai beni se presente (oggi 8 su 34 solo dal testo) | MEDIA |
| C5 | fallcoaste | Superficie (3 su 7, e a volte poco credibile: "45" per un immobile da 360.000 €) | MEDIA |
| C6 | worldcapital | Prezzo `null` su parte degli annunci (il codice lo dice: in elenco non c'è) | MEDIA |
| C7 | medianord | Prezzo `null` in 3 su 4 (trattativa riservata?). Verificare sul dettaglio | BASSA |
| C8 | idealista | Superficie presente solo in 7 su 103 annunci (con le vecchie categorie): ricontrollare con `vendita-negozi` | ALTA |
| C9 | immobiliare | Completo: OK | — |

### D. Run settimanale IMM_ID (idealista + immobiliare)

| # | Verifica | Stato attuale | Priorità |
|---|---|---|---|
| D1 | **Confronto "annuncio recente" in una sola direzione.** Un annuncio è nuovo o cambiato se `updated_at` è **successivo** all'ultima volta che era stato scaricato | **Usa `abs(...)` in 3 punti**: `scraper_immobiliare_master.py` riga 681, `scraper_idealista_master.py` riga 806, `run_opportunities_immid.py` riga 148. Effetti: un annuncio aggiornato dopo l'ultimo scaricamento viene trattato come "vecchio" e lo scraper si ferma troppo presto; il filtro settimanale tiene annunci già visti e può scartare quelli aggiornati ieri | ALTA |
| D2 | **Il riferimento (archivio completo) si aggiorna ogni settimana** | In modalità resume gli annunci già scaricati vengono saltati e il loro `scraped_at` resta quello della prima volta, quindi il riferimento invecchia. Decidere come aggiornarlo, es. aggiornare `scraped_at` e `updated_at` anche per gli annunci già presenti, riscaricando il dettaglio solo se l'annuncio è cambiato | ALTA |
| D3 | **Funziona in headless** | Tutte le run di oggi usano `--headless false`. Il pannello lancia in headless: verificare che DataDome lasci passare. Se non passa, il pannello deve lanciarli con la finestra visibile (si imposta nel `config.yaml` della skill) | ALTA |
| D4 | **Archivio immobiliare più leggero** | `dataset_immobiliare_completo.json` pesa 1,2 GB perché `raw_detail` contiene tutta la pagina (traduzioni, configurazioni del sito): il 60% del peso di ogni annuncio. Salvare solo `raw_detail.pageProps.detailData.realEstate`, così file e salvataggi sono molto più veloci. **Devono restare** `properties[0].industrial`, `location`, `auction`, `surfaceConstitution`, `floor`, `floors`, `features`, `availability`, `buildingYear`, `cadastrals`, `condition` e `price` | MEDIA |
| D5 | Idealista: la data di aggiornamento è una **stima** ("più di 3 mesi fa") | Accettabile, ma verificare che non faccia fermare lo scraper troppo presto | MEDIA |

### E. Prova finale prima di generare la skill

| # | Verifica | Priorità |
|---|---|---|
| E1 | Uno script `scrapers_v2/verifica_dataset.py` che, per ogni file, stampa: numero di annunci, percentuale di riempimento dei campi della sezione C, formato delle chiavi, eventuali doppioni e data di `generated_at` | ALTA |
| E2 | **Durata** di ogni scraper su tutte le 36 città, misurata e annotata (serve per i timeout e le stime del pannello) | MEDIA |
| E3 | Una run completa di tutti gli scraper, poi `verifica_dataset.py`. Mandami il risultato: aggiorno la mappa dei campi e i numeri nel prompt della skill prima di generarla | ALTA |

---

## Parte 2 — Prompt da incollare in Claude Code

> Apri Claude Code nella cartella `Pronto_Automation` e incolla il testo sotto.

```
Lavori nella cartella Pronto_Automation. Negli scraper in scrapers_v2/ devi
verificare e, dove serve, correggere 10 script: gli 8 scraper aste
(scraper_<fonte>_master.py), scraper_idealista_master.py,
scraper_immobiliare_master.py e run_opportunities_immid.py.
Questi script saranno lanciati ogni settimana da un pannello automatico,
seguito da una skill di valutazione che legge i loro file JSON.

VINCOLI (non negoziabili)
- Non cambiare percorsi di output, struttura dei file
  ({generated_at, total, listings: {"<fonte>_<id>": {...}}}) e nomi dei campi
  esistenti. Aggiungere campi nuovi va bene.
- Mantieni le opzioni --output, --no-resume, --cities, --log-level
  (e per idealista/immobiliare --headless, --max-pages, --recent-only,
  --staleness-days, --stale-streak, --baseline).
- Codice di uscita 1 per errori gravi, 0 a fine lavoro; errori sulla singola
  città nel log come [ERROR]; ultima riga del log sempre
  "Scraping completato. Totale annunci nel dataset: N".
- Non lanciare run complete su tutte le 36 città senza chiedermelo:
  per le prove usa --cities Milano,Bergamo e --output su un file temporaneo
  in scrapers_v2/_prove/.
- Prima di ogni modifica spiegami cosa cambi e perché; alla fine dammi
  un riepilogo per file.

COSA VERIFICARE E CORREGGERE, IN QUEST'ORDINE

1. Direzione del confronto "annuncio recente" (priorità alta)
   scraper_immobiliare_master.py ~riga 681, scraper_idealista_master.py
   ~riga 806, run_opportunities_immid.py ~riga 148 usano
   abs(baseline - updated). Un annuncio va considerato "nuovo o cambiato" se
   updated_at è SUCCESSIVO allo scraped_at di riferimento, e "vecchio" se è
   precedente. Correggi in una sola direzione e spiegami l'effetto sullo stop
   (--stale-streak) e sul filtro settimanale.

2. Aggiornamento del riferimento (priorità alta)
   In modalità resume gli annunci già presenti vengono saltati e il loro
   scraped_at non si aggiorna mai, quindi il riferimento invecchia.
   Proponi e implementa un modo per aggiornare scraped_at/updated_at (e
   prezzo) degli annunci già presenti senza riscaricare il dettaglio quando
   non è cambiato.

3. Headless (priorità alta)
   Prova idealista e immobiliare con --headless true su Milano. Dimmi se
   DataDome li blocca. Non disattivare protezioni: se in headless non
   funzionano, dimmelo e basta.

4. Copertura geografica uniforme (priorità alta)
   Tutti gli scraper aste devono tenere gli annunci delle PROVINCE delle 36
   città (sigla provincia), non solo del comune capoluogo. Oggi:
   - astalegale filtra per nome città nell'URL,
   - asteflorio e caseasta usano la pagina del comune.
   Verifica sul sito come ottenere l'intera provincia e correggi.

5. Paginazione completa (priorità alta)
   - astalegale: si ferma a pagina 15-16 (~170 annunci per tutta Italia) e
     trova 0 pertinenti: verifica sul sito quanti annunci ci sono davvero e
     perché si ferma.
   - caseasta: il codice dice che oltre ~25 annunci per comune potrebbe
     perderne; verifica la paginazione.
   - idealista (categoria vendita-negozi) e immobiliare: verifica che
     --max-pages 60 basti per Milano; se no, dimmi quante pagine servono.
   Per Milano e Bergamo confronta il numero di annunci trovati con quello
   mostrato dal sito con gli stessi filtri.

6. Campi mancanti: pagina di dettaglio solo per gli annunci già filtrati
   Per ogni annuncio pertinente servono: prezzo (aste: prezzo base e
   offerta minima), superficie, comune e sigla provincia, indirizzo,
   data asta e termine offerte, tribunale, numero procedura e lotto,
   descrizione completa non troncata, tipologia, url.
   - asteannunci: legge solo le card, quindi manca la superficie e la
     descrizione è quella della card. Aggiungi la lettura del dettaglio.
   - asteflorio: la descrizione è uguale al titolo; mancano data asta,
     tribunale, superficie. Verifica anche cosa rappresenta il prezzo:
     29 lotti su 41 costano meno di 10.000 € (es. magazzini a Roma a
     2.221,50 €).
   - caseasta: descrizione troncata a ~103 caratteri; mancano data asta,
     tribunale, procedura.
   - venditegiudiziarie: l'API Typesense restituisce custode, delegato,
     dati catastali e beni del lotto ma non li salviamo: salvali e ricava
     la superficie dai beni se c'è.
   - fallcoaste: superficie solo in 3 annunci su 7 e a volte non credibile.
   - worldcapital e medianord: prezzo spesso null, verifica nel dettaglio.
   - idealista con vendita-negozi: verifica quanti annunci hanno superficie.
   Usa pause gentili tra le richieste e gestisci gli errori senza fermare
   lo script.

7. Archivio immobiliare più leggero (priorità media)
   dataset_immobiliare_completo.json pesa 1,2 GB perché raw_detail salva
   tutta la pagina. Salva solo raw_detail.pageProps.detailData.realEstate,
   mantenendo lo stesso percorso (raw_detail.pageProps.detailData.realEstate)
   così chi legge il file non deve cambiare. Devono restare in
   properties[0]: industrial, location, auction, surfaceConstitution, floor,
   floors, features, availability, buildingYear, cadastrals, condition,
   price. Non convertire il file esistente senza chiedermelo.

8. Dipendenze
   Aggiungi requests (e tutto ciò che serve agli scraper aste) a
   scrapers_v2/requirements.txt.

9. Script di controllo
   Crea scrapers_v2/verifica_dataset.py che, dato uno o più file dataset,
   stampa per ciascuno: numero annunci, generated_at, formato delle chiavi,
   doppioni, e una tabella con la percentuale di riempimento dei campi del
   punto 6 (considera "pieno" solo un valore non null e non vuoto).
   Eseguilo sui file prodotti dalle prove.

RISULTATO CHE MI SERVE ALLA FINE
- Elenco delle modifiche per file.
- La tabella di verifica_dataset.py sulle prove Milano+Bergamo, prima e dopo.
- Per ogni scraper: durata della prova e stima della durata su 36 città.
- La mappa aggiornata dei campi per fonte: nome del campo per prezzo,
  superficie, comune, provincia, data asta, tribunale, procedura, lotto,
  descrizione.
- Cosa non sei riuscito a sistemare e perché.
```

---

Quando Claude Code ha finito, mandami il riepilogo e l'output di `verifica_dataset.py`: aggiorno la mappa dei campi (§2.2) e la tabella della qualità dei dati (§2.3) nel prompt della skill, poi possiamo generarla.
