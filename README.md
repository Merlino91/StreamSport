# 🏆 StreamSport - Stremio Addon

Addon per **Stremio** dedicato agli eventi sportivi in streaming, che unisce la solida architettura asincrona Python/FastAPI di **EasySports** con il catalogo a generi granulari di **StreamViX** e i flussi live delle emittenti sportive italiane (Sky Sport, DAZN) e internazionali.

---

## ✨ Caratteristiche Principali

1. **Catalogo Unico con Generi Granulari**:
   - Invece di un calderone generico, un solo catalogo Stremio (`StreamSport Eventi`) con menu a tendina "Genere" per selezionare all'istante:
     - ⚽ **Calcio**: Serie A, Serie B, Serie C & Coppa Italia, Coppe Europee (Champions/Europa/Conference), Premier League, LaLiga, Bundesliga, Ligue 1, Calcio Estero & Americhe.
     - 🏎️ **Motori**: Formula 1, MotoGP, NASCAR & Rally.
     - 🥊 **Combat Sports**: UFC & MMA, Boxe, Wrestling (WWE/AEW).
     - 🏀 **Basket**: Basket NBA, Eurolega & Basket Italiano, WNBA & Leghe Internazionali.
     - 🏈 **Football Americano**: Football NFL, College Football & CFL.
     - 🎾 **Altri Sport**: Tennis (ATP/WTA), Baseball MLB, Hockey NHL, Rugby & AFL, Volley, Altri Sport.
2. **Flussi Live Italiani & Internazionali**:
   - Integrazione diretta delle dirette italiane (Sky Sport Calcio, Sky Sport Uno, Sky Sport F1, Sky Sport MotoGP, DAZN 1, Eurosport, TV8, Rai Sport) veicolate tramite il tuo **EasyProxy**.
   - Flussi live internazionali multi-sorgente con bandiere delle lingue.
3. **Logica Oraria e Protezione Partite**:
   - **> 20 minuti prima dell'inizio**: visualizza la scheda oraria elegante con data/orario nel tuo fuso orario (`Europe/Rome`) e avviso che i flussi aprono 20 minuti prima.
   - **Da 20 minuti prima fino a fine gara**: compaiono i flussi live pronti per la visione.
   - **Oltre 4 ore dopo l'inizio**: la scheda passa automaticamente a evento concluso, offrendo le sintesi (YouTube, Dailymotion) e i Replay completi Torrent.
4. **Bypass Blocchi ISP (DNS-over-HTTPS)**:
   - Client DoH integrato (Cloudflare/Google) per risolvere e servire locandine e metadati anche sotto blocchi DNS italiani.
5. **Database SQLite Leggero**:
   - Retention automatica a 72 ore per conservare le partite recenti per i replay senza memory leak.

---

## 🚀 Avvio Rapido

### Avvio Locale con Python
Richiede Python 3.12 o 3.14:
```bash
pip install -r requirements.txt
python run.py
```
Il server si avvierà su `http://localhost:7002`.

### Avvio con Docker / Docker Compose
```bash
docker compose up -d --build
```

---

## ⚙️ Configurazione

1. Apri nel browser: `http://<IP_VPS_O_DOMINIO>:7002/configure`
2. Inserisci l'URL del tuo **EasyProxy** (es. `https://ep.tuodominio.com`) e l'eventuale password API.
3. Seleziona il tuo fuso orario (es. `Europe/Rome`).
4. Clicca su **Genera Link Installazione** e poi su **Installa su Stremio**.
