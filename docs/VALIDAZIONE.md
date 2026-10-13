# Verifica della consegna

Data: 23 settembre 2026.

- 12 test automatici Python: PASS.
- Suite integrata: 8 scenari di attacco + 1 comando autorizzato, 9/9 PASS.
- HTTP: console 3000 e 4000 correttamente identificate, stato condiviso verificato.
- Browser Chromium headless: URDF e 41 mesh caricati; nessun errore JavaScript/console rilevato.
- UI: arm, replay, suite e propagazione E-stop tra due pagine verificati.
- Mobile 390 px: nessun overflow orizzontale rilevato.
- Asset: 44 file verificati rispetto agli SHA256 del manifest di provenienza.
- Configurazione Wazuh: XML sintatticamente valido. Integrazione con un manager Wazuh non eseguita; da validare con wazuh-logtest.
- Dockerfile e Compose forniti; build Docker non eseguito nell'ambiente di creazione.

Queste verifiche riguardano il laboratorio locale; non attestano compatibilità di controllo con hardware G1-D né accuratezza di simulazione fisica.
