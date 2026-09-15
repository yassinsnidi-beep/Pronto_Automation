---
name: prontobox-scouting
description: Valuta settimanalmente gli immobili raccolti in opportunities/INPUT/ (aste e vendite per il self-storage Prontobox), calcola una classifica e scrive le 10 migliori opportunità nuove o migliorate in opportunities/OUTPUT/. Usare quando l'utente chiede di "eseguire la skill prontobox-scouting", di "fare lo scouting settimanale" o di "valutare le opportunità immobiliari" in questo progetto, oppure quando lo chiama il pannello (pannello/pannello.py) lanciando Claude Code in modalità non interattiva.
---

# prontobox-scouting

Skill di scouting immobiliare per Prontobox (aperture di sedi self-storage nel
Nord e Centro-Nord Italia). Una volta a settimana normalizza tutti gli
annunci raccolti dagli scraper, li valuta con una pipeline in 4 stadi e
segnala le 10 migliori opportunità che hanno qualcosa di nuovo da dire:
mai segnalate prima, oppure già segnalate ma migliorate da allora.

**Leggi prima `/home/claude/prompt_skill_prontobox_scouting_v2.md` (o la
copia allegata al progetto) se disponibile: è la specifica completa da cui
questa skill è stata generata ed è la fonte di verità per ogni dettaglio non
coperto qui.**

## Il principio che conta più di ogni regola

> La skill non deve **mai** scartare un immobile perché un dato manca. Deve
> distinguere "non idoneo" (scarto, con un motivo verificabile) da
> "non valutabile" (si segnala comunque, dicendo cosa manca e dove trovarlo).

Ogni valore derivato — non solo quelli scritti dal modello — porta sempre
`{valore, fonte, confidenza}`. Non esiste un valore senza provenienza.

## Divisione del lavoro: cosa è codice, cosa è giudizio

**Quasi tutto è codice deterministico**, già scritto in `prontobox/` e
orchestrato da `scout.py`: adattatori per fonte, parser di prezzi/date/
superfici, geografia, deduplicazione, i 4 hard filter, la scorecard 0-100,
il gate economico (SLN, capex, YoC), l'esito/voto, la classifica settimanale
e il confronto con lo storico. **Non toccare questi calcoli**: se sembrano
sbagliati su un caso specifico, segnalalo nella `valutazione_testuale`
invece di aggirarli.

Il modello (questa istanza di Claude Code, quando la skill gira in
produzione tramite il pannello) interviene **solo** su testo libero, con
tre compiti, ognuno confinato a un singolo record alla volta:

1. **Percorso autorizzativo stimato**: classificare, leggendo
   titolo+descrizione, uno tra i 5 livelli della tabella dello Stadio 2
   (12/10/7/5/2 punti — vedi la specifica, sezione "Stadio 2 — Scorecard
   qualità"), con un motivo di una frase.
2. **Indizi di rischio** aggiuntivi non già intercettati dalla checklist
   automatica per parole chiave (amianto, difformità, vincoli, occupazione),
   se il testo ne contiene di espliciti.
3. **Valutazione testuale**: 4-8 frasi in italiano — perché è in lista,
   punti di forza, rischi principali, cosa verificare per primo e, se
   `RISEGNALATA`, cosa è cambiato rispetto alla volta scorsa (i
   `motivi_risegnalazione` te lo dicono già: riprendili, non inventarne
   altri).

**Regole non negoziabili per questi tre compiti:**

- Basati **solo** sul testo del record (`titolo`, `descrizione`) e sui dati
  già estratti (`flag`, `checklist_dd`, `dati_mancanti`) che trovi nel file
  di lavoro. Non inventare metrature, prezzi, altezze o scadenze: quelli
  sono già stati letti dal codice, e se mancano è perché il testo non li
  dice.
- Ogni giudizio (`percorso_autorizzativo_*`, i punti di forza/rischio) porta
  `fonte` e `confidenza`. Se non hai indizi sufficienti per un livello di
  confidenza `alta`, usa `media` o `bassa` — non forzare la sicurezza.
- Non scartare mai un'opportunità tu stesso: non è una decisione che ti
  compete, la prende `scout.py` in base a regole scritte.
- Se ti è concesso `WebFetch` (vedi permessi sotto) e un annuncio ha
  descrizione troncata o ambigua, puoi aprire la sua `url` per leggere di
  più — ma resta un aiuto alla lettura, non un'estrazione di nuovi dati
  strutturati: quelli restano compito del codice (che oggi, in questo
  build, non fa fetch automatico — vedi "Limiti noti" in fondo).

## Come si esegue: i tre passi di scout.py

```
python scout.py --root "<Pronto_Automation>" [--config config.yaml] [--data AAAA-MM-GG] prepara
python scout.py --root "<Pronto_Automation>" seleziona --giudizi <file_giudizi.json>   # anteprima, opzionale
python scout.py --root "<Pronto_Automation>" scrivi    --giudizi <file_giudizi.json>
```

(`tutto` incatena `prepara` + `scrivi` con il ripiego euristico al posto del
modello — comodo per test e run manuali, **non** il percorso di produzione:
in produzione il modello deve intervenire tra `prepara` e `scrivi`.)

1. **`scout.py prepara`** normalizza tutti gli input, applica i 4 hard
   filter (Stadio 1) e scrive `opportunities/OUTPUT/.lavoro_testi_<data>.json`:
   un oggetto con `meta` e `items[]`, un elemento per ogni annuncio
   sopravvissuto ai filtri (quelli scartati non compaiono: non serve
   giudicarli). Ogni `item` ha:

   ```json
   {
     "id": "caseasta_51067",
     "canale": "ASTE",
     "categoria": "capannone",
     "comune": "Milano",
     "tipo_vendita": "asta",
     "titolo": "…",
     "descrizione": "… (troncata a 3000 caratteri)",
     "flag": ["ALTEZZA_DA_VERIFICARE"],
     "checklist_dd": [{"voce": "amianto", "allerta": "media", "indizio": "copertura in fibrocemento"}],
     "dati_mancanti": [{"dato": "altezza sotto trave", "dove_trovarlo": "…", "azione": "…"}]
   }
   ```

2. **Leggi quel file** e scrivi, per ogni `id` presente in `items[]`, un
   oggetto giudizio nello **stesso formato prodotto da
   `prontobox/giudizi_fallback.py`** (guardalo: è il ripiego euristico che
   sostituisci, e definisce esattamente la forma attesa):

   ```json
   {
     "caseasta_51067": {
       "percorso_autorizzativo_punti": 7,
       "percorso_autorizzativo_motivo": "cambio d'uso con richiesta di permesso di costruire ordinario, nessuna difformità dichiarata",
       "percorso_autorizzativo_fonte": "modello",
       "percorso_autorizzativo_confidenza": "media",
       "indizi_rischio": [{"voce": "amianto", "allerta": "media", "indizio": "copertura in fibrocemento, citata due volte nel testo"}],
       "punti_di_forza": ["capannone a pianta regolare, altezza sotto trave dichiarata 7,1 m"],
       "rischi_principali": ["destinazione d'uso attuale artigianale: verificare compatibilità con logistica/self-storage"],
       "da_verificare": ["altezza sotto trave: confermare con la perizia CTU (§Stadio2)"],
       "fonte": "modello",
       "confidenza": "media"
     }
   }
   ```

   Scrivi questo oggetto — `{id: giudizio}` per **tutti** gli id di
   `items[]`, anche quando non hai nulla da aggiungere oltre al percorso
   autorizzativo (in quel caso `indizi_rischio`, `punti_di_forza`,
   `rischi_principali`, `da_verificare` possono essere liste vuote: il
   codice se ne accorge e lo scrive chiaramente nel report, non è un
   errore). Usa sempre `"fonte": "modello"` (mai `"stima_euristica"`: quel
   valore è riservato al ripiego automatico) e una `confidenza` onesta.
   Salva il file dove preferisci dentro `opportunities/OUTPUT/` (es.
   `.giudizi_<data>.json`).

3. **`scout.py scrivi --giudizi <quel_file>`** ricalcola scorecard, gate
   economico, esito e voto (Stadio 2-4), fa la selezione settimanale con il
   controllo sullo storico, valida lo schema e scrive
   `report_AAAA_MM_GG.json` + aggiorna `summary.json`. A questo punto, **per
   le sole 10 opportunità selezionate** (non tutte quelle di `prepara`),
   rileggi `report_AAAA_MM_GG.json` e verifica che ogni
   `valutazione_testuale` sia coerente e che, per le `RISEGNALATA`, il
   confronto prima→dopo in `motivi_risegnalazione` abbia senso rispetto al
   testo. Se qualcosa non torna, **non modificare il JSON a mano**: è
   generato da codice deterministico e deve restare tale. Segnalalo
   all'utente.

Se il file `--giudizi` manca o è illeggibile, `scout.py` usa da solo il
ripiego euristico (`prontobox/giudizi_fallback.py`) e lo dichiara in
`meta.note` del report — la run non si blocca mai per l'assenza del
modello, ma il risultato è marcato come stima di bassa confidenza.

## Permessi, quando questa skill gira in produzione (§7.3)

In modalità non interattiva (`claude -p`, come la lancia il pannello)
autorizza **solo**:

- eseguire `python scout.py` (nient'altro in `Bash`);
- leggere `opportunities/INPUT/`;
- scrivere in `opportunities/OUTPUT/`;
- `WebFetch` sulle pagine degli annunci, se serve a leggere meglio un testo
  troncato.

Il comando di riferimento (verificato contro `claude --help` della versione
installata in questo ambiente) è in `config.yaml` → `pannello.comando_claude`,
non hardcoded nel pannello. Non chiedere né usare permessi più ampi di
questi quattro.

## File coinvolti

- `scout.py` — punto di ingresso, sottocomandi `prepara` / `seleziona` /
  `scrivi` / `tutto`.
- `prontobox/` — tutto il codice deterministico (adattatori, filtri,
  scorecard, economico, esito, selezione, report).
- `prontobox/giudizi_fallback.py` — ripiego euristico; leggilo per il
  formato esatto atteso da un giudizio.
- `config.yaml` — ogni soglia e parametro è qui, commentato in italiano.
  Non spostare soglie nel codice.
- `reference/*.csv` — dati di riferimento (IQSS per comune, concorrenti,
  sedi Prontobox, poli retail). In questo build sono vuoti (solo intestazioni):
  il deck sorgente non era disponibile. La scorecard lo gestisce come
  "ignoto" (punteggio interpolato neutro, confidenza bassa), mai come
  errore — ma finché restano vuoti, nessuna opportunità potrà mai risultare
  in classe A/B/C con margine sufficiente per `PROMUOVI`: vedi README.md.

## Limiti noti di questo build (leggi prima di "correggere" qualcosa)

- **Fetch condizionale (specifica, Stadio 0 punto 6)**: non implementato.
  Se superficie/scadenza/descrizione mancano, il record resta
  `DATI_INSUFFICIENTI` invece di essere arricchito da un fetch automatico
  della pagina o della perizia. `config.yaml` → `rete.fetch_abilitato` è
  `false` di proposito. È una scelta deliberata di questo build (niente
  rete in scrittura del codice, cache, rate limit da progettare), non un
  bug: se va implementato, va aggiunto come funzione dedicata in
  `prontobox/normalizza.py`, mai come chiamata sparsa.
- **`reference/*.csv` vuoti**: vedi sopra.
- **Il percorso `claude -p` reale del §7.3 non è mai stato eseguito in
  questo build** (per non ricorsare su se stesso durante la generazione
  della skill): solo documentato e verificato concettualmente contro
  `claude --help`. La prima run reale in produzione va osservata con
  attenzione.
