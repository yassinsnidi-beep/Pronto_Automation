# Prompt pronto per Claude Code

**Prima di usarlo**: serve che tu abbia già fatto il punto 5 di
`GUIDA_NUOVO_PC.md` — VS Code + estensione Claude Code installati, e
l'accesso con il tuo account già fatto. Quello è l'unico passaggio che
resta comunque manuale (un login non può farlo un agente al posto tuo).

Fatto quello, apri questa cartella in VS Code, avvia una chat con Claude
Code e incolla il messaggio qui sotto così com'è.

---

```
Segui GUIDA_NUOVO_PC.md in questa cartella e portami all'installazione
completa del pannello Prontobox Scouting. Nello specifico:

1. Controlla se Python, Node.js e Google Chrome sono già installati sul
   sistema. Se manca qualcosa che puoi installare tu da terminale (es.
   Node.js via winget), fallo, spiegandomi cosa stai facendo passo per
   passo. Se manca Google Chrome o Python e serve un programma di
   installazione con finestra grafica, dammi il link esatto da cui
   scaricarlo e i passaggi da seguire io a mano.

2. Installa la CLI di Claude Code con:
   npm install -g --allow-scripts=@anthropic-ai/claude-code @anthropic-ai/claude-code
   Poi dimmi tu di lanciare "claude" da terminale per fare il login: quel
   passaggio non puoi farlo al posto mio.

3. Installa le dipendenze Python del progetto (requirements.txt in radice
   e in scrapers_v2/) e lancia "playwright install chrome".

4. Verifica che esista il file
   .claude/skills/prontobox-scouting/scout.py

5. Controlla che il pannello si avvii correttamente: puoi lanciarlo tu da
   terminale (python pannello/pannello.py) per verificare che non dia
   errori, poi lo chiudo e lo riapro io con il doppio clic su
   "Avvia Prontobox Scouting.bat" come uso normale.

6. Alla fine, dammi un riepilogo chiaro e breve di:
   - cosa hai sistemato tu
   - cosa devo ancora fare io a mano (login, eventuali installazioni con
     finestra grafica)
   - cosa aspettarmi alla prima scansione reale di Idealista/Immobiliare
     (il possibile blocco anti-bot descritto al punto 10 della guida, e
     come comportarmi se capita)
```
