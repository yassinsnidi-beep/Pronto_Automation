# Prontobox Scouting — installazione su un PC nuovo

Questa cartella contiene tutto: scraper, skill, pannello, **e i dati già
raccolti** (aste, Idealista, Immobiliare) — non parti da zero. Segui questi
passaggi in ordine, una volta sola. Per l'uso settimanale una volta
installato, leggi invece `README.md`.

Tempo previsto: 30-40 minuti di installazioni, più un margine imprevedibile
la prima volta che Idealista/Immobiliare incontrano l'anti-bot (vedi punto 10).

---

## 1. Google Chrome — obbligatorio

Gli scraper di Idealista e Immobiliare aprono il **Chrome vero** installato
sul sistema (non il Chromium interno di Playwright), perché ha un'impronta
digitale meno riconoscibile dall'anti-bot dei due siti (DataDome).

Se non c'è già: <https://www.google.com/chrome/>

## 2. Python 3.9 o successivo — obbligatorio

Da <https://www.python.org/downloads/>. Durante l'installazione, **spunta
"Add python.exe to PATH"** — senza questa spunta il file `.bat` non trova
Python e si ferma subito con un errore chiaro a riguardo.

## 3. Node.js (versione LTS) — obbligatorio

Serve solo per la CLI di Claude Code al passo successivo. Da
<https://nodejs.org>, scegli la versione **LTS**. Verifica da un terminale
nuovo:

```
node --version
npm --version
```

## 4. CLI di Claude Code + accesso — obbligatorio

È il motore che scrive la valutazione testuale di ogni opportunità ogni
settimana — il pannello la lancia da sé, senza bisogno di aprirla a mano.
**Serve un account Claude proprio** di chi userà questo PC (non si eredita
da un altro computer): la valutazione consuma l'uso/i crediti di
quell'account.

```
npm install -g --allow-scripts=@anthropic-ai/claude-code @anthropic-ai/claude-code
```

Poi, dallo stesso terminale, avvia l'accesso e segui le istruzioni a
schermo:

```
claude
```

> Se `npm install` segnala uno script di post-installazione bloccato
> ("allow-scripts"), è quello già gestito sopra con `--allow-scripts` —
> senza, la CLI si installa ma un passaggio interno non gira.

## 5. VS Code + estensione Claude Code — consigliato

Questo è il modo per **parlare con Claude interattivamente** sul PC — utile
per farsi aiutare a correggere uno scraper, capire un errore nel log, o
modificare la configurazione. È separato dalla CLI del punto 4 (quella
lavora da sola dentro il pannello).

1. Installa [Visual Studio Code](https://code.visualstudio.com)
2. Dal pannello estensioni (`Ctrl+Shift+X`), cerca **"Claude Code"** e installala
3. Apri questa cartella come cartella di lavoro (*File → Apri cartella*)
4. Avvia l'estensione e accedi con lo stesso account del punto 4

## 6. Dipendenze Python del progetto — obbligatorio

Da un terminale, dentro questa cartella:

```
pip install -r requirements.txt
pip install -r scrapers_v2\requirements.txt
playwright install chrome
```

L'ultimo comando è indispensabile: dice a Playwright di usare il Chrome
vero installato al punto 1.

## 7. Controlla che sia tutto al suo posto

Deve esistere questo file (se hai copiato l'intera cartella, c'è già):

```
.claude\skills\prontobox-scouting\scout.py
```

## 8. Primo avvio

Doppio clic su **`Avvia Prontobox Scouting.bat`**. Si apre una finestra
nera (lasciala aperta) e dopo un paio di secondi il browser mostra il
pannello su `http://127.0.0.1:8765/`.

## 9. Cosa trovi già pronto in questa cartella

A differenza di un'installazione da zero, `opportunities/INPUT/` contiene
già i dati dell'ultima raccolta (aste + Idealista + Immobiliare) e
`scrapers_v2/dataset_idealista_completo.json` /
`dataset_immobiliare_completo.json` contengono lo storico completo delle
due fonti principali. Questo significa che:

- il primo "Avvia scansione settimanale" non riparte da zero: gli scraper
  con il resume attivo riprendono da dove erano arrivati sul PC di origine
- il confronto "nuovo/risegnalato rispetto alla settimana scorsa" ha già
  una base su cui lavorare

`opportunities/OUTPUT/` è invece vuota apposta: il primo report vero lo
genera la prima scansione su questo PC.

## 10. Cosa aspettarsi da Idealista e Immobiliare la prima volta

Il profilo Chrome che questi due scraper usano per superare l'anti-bot
**non è incluso** in questa cartella (è legato al dispositivo che l'ha
creato, copiarlo non aiuterebbe). Su questo PC nuovo, la sessione riparte
da zero ed è probabile che nella prima scansione incontri un blocco
anti-bot temporaneo — è capitato anche a noi, risolto con una pausa di
qualche ora. Non è un errore da correggere, è normale rodaggio:

- tieni d'occhio la finestra Chrome che si apre durante questa fase: se
  compare un controllo anti-bot, risolvilo a mano appena possibile (hai
  circa 20 secondi prima che lo script riprovi da solo)
- se si blocca per 3 città di fila, lo script si ferma da solo invece di
  insistere all'infinito — è voluto, protegge da un blocco più lungo
- se resta bloccato, chiudi tutto e riprova dopo qualche ora: è quasi
  sempre un limite temporaneo legato alla sessione, non un guasto

---

## Sei pronto quando…

- [ ] `python --version`, `node --version` e `claude --version` rispondono
      tutti da un terminale nuovo
- [ ] `.claude\skills\prontobox-scouting\scout.py` esiste
- [ ] il doppio clic su `Avvia Prontobox Scouting.bat` apre il pannello nel
      browser senza errori di Python mancante
- [ ] la Home del pannello mostra il pulsante **Avvia scansione
      settimanale**

Da qui in avanti vale `README.md` per l'uso di tutti i giorni.
