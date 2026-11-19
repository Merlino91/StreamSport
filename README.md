# 🏆 StreamSport

**StreamSport** è un addon per [Stremio](https://stremio.com) dedicato agli eventi sportivi in streaming e ai canali TV sportivi live. Aggrega automaticamente palinsesti, metadati arricchiti, locandine dinamiche e flussi multi-sorgente (italiani e internazionali) in un catalogo sportivo unificato e costantemente aggiornato.

---

## 🚀 Avvio Rapido

```bash
# Con Python (>= 3.12)
pip install -r requirements.txt
python run.py

# Oppure con Docker
docker compose up -d --build
```

La pagina di configurazione sarà accessibile all'indirizzo `http://localhost:7002/configure`.

---

## 💖 Ringraziamenti & Credits

Un ringraziamento speciale ai progetti open-source e agli autori che hanno ispirato o reso possibili componenti chiave di questo addon:

- **[qwertyuiop8899/streamvix](https://github.com/qwertyuiop8899/streamvix)** – Per l'ispirazione e i concetti nella gestione del catalogo sportivo e dei flussi.
- **[qwertyuiop8899/tvvoo](https://github.com/qwertyuiop8899/tvvoo)** – Per l'integrazione e gestione del catalogo dei canali TV live.
- **[realbestia1/EasyProxy](https://github.com/realbestia1/EasyProxy)** – Per la solida architettura di proxying e gestione dei flussi live.
