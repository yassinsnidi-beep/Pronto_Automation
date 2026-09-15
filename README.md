# Prontobox Scouting — guida rapida

Questa cartella contiene la skill **prontobox-scouting** e il **pannello
settimanale** che la usa in automatico: uno scouting immobiliare che ogni
settimana raccoglie gli annunci di aste e vendite (idealista/immobiliare),
li valuta con le regole di Prontobox e ti segnala le 10 migliori
opportunità — nuove o migliorate rispetto alla settimana scorsa.

Questa guida è pensata per chi userà il pannello ogni settimana, non solo
per chi programma. Per i dettagli tecnici completi (come funziona ogni
calcolo, ogni filtro, ogni caso limite) la fonte di verità resta
`prompt_skill_prontobox_scouting_v2.md`, in questa stessa cartella.

## 1. Cosa c'è in questa cartella

```
Pronto_Automation/
├── Avvia Prontobox Scouting.bat   ← doppio clic qui per iniziare
├── README.md                       ← questa guida
├── prompt_skill_prontobox_scouting_v2.md   ← specifica completa (riferimento tecnico)
├── verifiche_scraper_per_claude_code.md    ← checklist per rifinire gli scraper (vedi §5)
├── pannello/
│   ├── pannello.py     ← il server del pannello (non va lanciato a mano: usa il .bat)
│   ├── runner.py        ← fa il lavoro vero e proprio quando premi "Avvia"
│   └── static/           ← le pagine del pannello (Home, Avanzamento, Risultati, Storico)
├── .claude/skills/prontobox-scouting/   ← la skill vera e propria (motore + regole)
├── scrapers_v2/           ← i tuoi scraper esistenti (non toccati da questo pacchetto)
└── opportunities/
    ├── INPUT/             ← qui gli scraper scrivono i dati grezzi (ASTE e IMM_ID)
    └── OUTPUT/             ← qui il pannello scrive i report e lo storico
```

## 2. Il primo avvio

### 2.0 Un passaggio manuale, solo la prima volta

Per una regola di sicurezza dello strumento con cui ho consegnato i file,
non posso scrivere direttamente dentro la cartella `.claude` del tuo PC
(è la cartella che usa Claude Code stesso). Per questo la skill vera e
propria te l'ho data come file compresso, **`prontobox-scouting-skill.zip`**,
che trovi sia qui in chat sia già dentro `Pronto_Automation` sul tuo PC.
Serve estrarlo al posto giusto una volta sola:

- **Modo più comodo**: apri la sessione di Claude Code che stai già usando
  sul PC per gli scraper e incollale semplicemente: *"estrai
  prontobox-scouting-skill.zip dentro .claude\skills\prontobox-scouting"*
  (creando le cartelle se non esistono). Fatto in pochi secondi, perché
  quella sessione lavora già in locale senza questa limitazione.
- **In alternativa, a mano**: tasto destro su `prontobox-scouting-skill.zip`
  → Estrai tutto. Si crea una cartella `prontobox-scouting`: spostala dentro
  `Pronto_Automation\.claude\skills\` (crea la cartella `skills` dentro
  `.claude` se non c'è già). Alla fine deve esistere il file
  `Pronto_Automation\.claude\skills\prontobox-scouting\scout.py`.

Il file `.bat` del punto 3 controlla da solo se questo passaggio è stato
fatto, e ti avvisa chiaramente se manca ancora.

1. Assicurati che sul PC sia installato **Python** (versione 3.9 o
   successiva). Se non lo hai: <https://www.python.org/downloads/> — durante
   l'installazione spunta "Add python.exe to PATH".
2. Assicurati che sul PC sia installato e collegato **Claude Code** (con il
   tuo account, non una chiave API): è quello che scrive la valutazione
   testuale di ogni opportunità ogni settimana. Se non l'hai ancora fatto,
   vedi il punto 4 qui sotto.
3. Fai doppio clic su **`Avvia Prontobox Scouting.bat`**. Si apre una
   finestra nera (il "motore" del pannello: lasciala aperta) e dopo un
   paio di secondi il browser si apre da solo sulla pagina del pannello.
4. Se è la primissima volta, nella schermata **Home** vedrai "Nessun
   report ancora generato": è normale, premi **Avvia scansione
   settimanale** per generare il primo.

Da qui in avanti, ogni settimana basta ripetere il punto 3 e premere il
tasto **Avvia**: tutto il resto — scraping di tutte le fonti, valutazione,
classifica, confronto con la settimana precedente — succede da solo.

**Importante:** il pannello resta raggiungibile solo dal tuo PC (non da
Internet, non da altri computer in rete): è pensato per essere usato da un
browser sullo stesso computer dove giri il file `.bat`.

## 3. Come si usa ogni settimana

### Home
Il tasto grande **Avvia scansione settimanale** fa tutto da solo: aggiorna
i dati di tutte le fonti (le 8 aste per intero, idealista e immobiliare per
intero, non solo gli annunci nuovi — vedi §5.4 della specifica) e poi
valuta e sceglie le 10 opportunità della settimana. Il banner giallo in
alto ti avvisa se sono passati troppi giorni dall'ultimo report (di
default 7, si cambia in `config.yaml`, vedi §6).

Se l'ultima volta lo scraping è andato bene ma la sola valutazione ha
fallito (es. Claude Code non era connesso), la Home mostra un tasto
**Riprova solo la valutazione**: rifà solo quel passaggio, senza rifare
ore di scraping.

### Avanzamento
Mentre una scansione è in corso (o appena finita) questa schermata mostra
a che punto è: una fila di passaggi (Controlli → Scraping → Valutazione →
Controllo finale → Completata), lo stato di ognuna delle 10 fonti con
quanti annunci ha trovato, e un registro dettagliato (in fondo, si apre
cliccandoci) per capire cosa è successo se qualcosa va storto. Da qui puoi
anche **interrompere** una scansione in corso, se necessario.

### Risultati
Le 10 opportunità della settimana, in ordine di punteggio: comune,
prezzo, tipologia, voto, se sono **nuove** o **risegnalate** (e perché),
i punti da verificare e la valutazione scritta per esteso. In fondo trovi
anche quante ne sono state scartate e perché, e quante sono "da
monitorare" (interessanti ma non ancora azionabili, es. asta troppo
lontana).

### Storico
Tutti i report delle settimane passate, e una ricerca per comune o per id
di un annuncio: utile per vedere di nuovo un'opportunità già segnalata
prima, o per controllare se un immobile è già stato valutato in passato.

## 4. Collegare Claude Code (la parte "intelligente")

Il calcolo di punteggio, filtri, classifica e confronto con lo storico è
**tutto codice deterministico**: non serve nessuna intelligenza artificiale
e dà sempre lo stesso risultato con gli stessi dati. L'unica parte affidata
a Claude Code è la **valutazione testuale** di ogni opportunità (perché è
interessante, quali rischi, cosa verificare) e la stima del percorso
autorizzativo quando serve leggere il testo dell'annuncio — cose che un
programma non può giudicare da solo.

Perché questo funzioni ogni settimana in automatico, sullo stesso PC dove
gira il pannello deve esserci **Claude Code installato e già collegato al
tuo account** (login fatto almeno una volta a mano). Il pannello lo lancia
da sé in modalità non interattiva quando serve: non devi aprirlo tu.

**Se Claude Code non è connesso, o si blocca, o non risponde in tempo:**
il pannello **non si ferma**. Dopo aver aspettato fino al timeout
configurato (di default 60 minuti, vedi §6), scrive comunque il report
della settimana usando un ripiego automatico a regole semplici al posto
del giudizio di Claude. In quel caso il report lo dice chiaramente: ogni
voce coinvolta ha `"fonte": "stima_euristica"` e `"confidenza": "bassa"`, e
in cima al report trovi una nota che lo segnala. In pratica: non perdi mai
la settimana, ma se vedi quella nota sappi che quella valutazione testuale
va riletta con più attenzione, perché non è stata scritta da un modello
linguistico.

## 5. Prima di fidarti al 100% dei risultati

Questo pacchetto (skill + pannello) è stato costruito e provato a fondo
**con i dati che c'erano nella cartella al momento della costruzione**, non
con gli scraper "a regime". Due cose da sapere:

1. **Gli scraper hanno ancora dei problemi noti**, elencati con dettaglio
   in `verifiche_scraper_per_claude_code.md` (bug nel confronto delle
   date, file idealista/immobiliare gonfiati da dati grezzi non necessari,
   copertura geografica incompleta in alcune fonti). Puoi far sistemare
   quei punti a un'altra sessione di Claude Code incollando il prompt
   pronto che trovi in fondo a quel file: la skill funziona anche senza,
   ma con dati migliori le valutazioni saranno più precise e complete.
2. **Il comando reale che lancia Claude Code** (in `config.yaml`, sezione
   `pannello.comando_claude`) è stato scritto e collegato correttamente,
   ma non è mai stato eseguito per davvero in questo ambiente di sviluppo
   (che non ha accesso a Claude Code in quel modo). La prima volta che lo
   userai sul tuo PC, tienilo d'occhio dalla schermata Avanzamento: se le
   opzioni del comando `claude` fossero cambiate rispetto a quando è stato
   scritto questo pacchetto, potrebbe servire un piccolo aggiustamento in
   quella sezione di `config.yaml` (il registro dell'Avanzamento ti dirà
   esattamente cosa non ha funzionato).

Tutto il resto — normalizzazione dei 10 formati, deduplicazione, filtri,
punteggio, gate economico, classifica, confronto con lo storico, scrittura
del report — è stato provato con **48 test automatici** (tutti superati)
e con **run reali sui dati presenti in cartella**, oltre a una prova
end-to-end del pannello via richieste HTTP vere (avvio, blocco di run
concorrenti, interruzione, lettura di report e storico).

## 6. Le impostazioni che puoi cambiare

Tutti i numeri che governano la skill stanno in un solo posto, mai nel
codice: `.claude/skills/prontobox-scouting/config.yaml`. Ogni parametro ha
un commento che spiega cosa succede se lo cambi. I più utili da conoscere:

- `filtri.superficie_min_mq` / `superficie_max_mq` — oggi 1.000-3.000 m²:
  sotto il minimo si scarta, sopra il massimo resta in classifica ma con
  una penalità crescente sul voto.
- `pannello.giorni_promemoria` — dopo quanti giorni senza una scansione la
  Home mostra l'avviso (default 7).
- `pannello.porta` — la porta del pannello sul tuo PC (default 8765; se
  occupata, il pannello ne sceglie una libera da solo e te lo dice).
- `economia.scenari_tariffa_eur_mq_anno` — oggi usa una media nazionale di
  settore (261 €/m²/anno): è il parametro che più di ogni altro sposta il
  calcolo economico. Sostituirlo con il ricavo reale per m² di Prontobox
  renderà il gate economico (SLN, capex, YoC) molto più affidabile.

Dopo aver modificato `config.yaml`, non serve riavviare il pannello: lo
ricarica da solo a ogni azione.

## 7. Problemi comuni

**"Non trovo Python installato su questo PC"** (il `.bat` lo dice subito) —
installa Python da python.org come descritto al punto 1 di questa guida.

**Il browser non si apre da solo** — apri manualmente il browser e vai
all'indirizzo scritto nella finestra nera (es. `http://127.0.0.1:8765/`).

**"Una run è già in corso" mentre non hai lanciato nulla** — probabilmente
la finestra nera di una sessione precedente è ancora aperta da qualche
parte, o il PC non l'ha chiusa correttamente. Chiudi tutte le finestre nere
di Prontobox Scouting e riprova; se il messaggio persiste, riavvia il PC.

**Una fonte segna "errore" nella schermata Avanzamento** — le altre fonti
proseguono comunque (una singola fonte che fallisce non blocca le altre):
il report userà per quella fonte i dati della settimana precedente. Guarda
il registro (in fondo alla schermata Avanzamento) per il motivo esatto.

**Il PC va in sospensione durante una scansione lunga** — il pannello
prova a impedirlo da solo mentre gira (parametro
`pannello.impedisci_sospensione` in `config.yaml`), ma solo su Windows e
solo mentre la finestra nera resta aperta: non chiuderla e non far andare
il PC in stand-by manualmente durante una scansione.

**Voglio provare il pannello senza toccare i dati veri** — lancialo da
terminale con `python pannello/pannello.py --simula`: usa dati di prova al
posto degli scraper e di Claude Code, e finisce in meno di un minuto.

---

*Costruito da Claude (Cowork) a partire dalla specifica
`prompt_skill_prontobox_scouting_v2.md`. Versione della skill: 2.0.0.*
