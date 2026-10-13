# Validazione WMA — 28 settembre 2026

**33/33 test superati, nessun test saltato**, eseguiti con Python 3.12, requests 2.32.5 e Streamlit 1.49.1. Output completo in TEST-WMA.txt.

- 12 test esistenti del motore e delle policy.
- 4 test unitari del contratto/mapping: envelope dello script, comandi ammessi, privilegi/output ignoti respinti, parametri non finiti.
- 16 test di integrazione HTTP con processi reali twin, simulator e adapter, più un WMA mock locale.
- 1 test del Test Harness con Streamlit AppTest: anteprima predefinita, movimento bloccato quando disarmato, applicazione dopo arm manuale. Nessuna eccezione dell'app.

La suite integrata della console è stata richiamata via HTTP: 9/9 scenari superati. Sono gli scenari del pacchetto multi-container ricevuto, non i 14 della precedente variante CPU-camera.

## Pipeline verificata

Stato reale del twin → facade simulator → observation WMA → POST /predict_action su mock → risposta → mapping → PEP → aggiornamento dello stesso twin. Verificato il movimento effettivo nella telemetria e la registrazione del ciclo JSONL. Verificati anche timeout, HTTP 500, non JSON, array, mapping sconosciuto, risposta obsoleta, richiesta concorrente, E-stop durante inferenza, overspeed, revoca, monitor e input non valido.

Le prove usano mock per l'inferenza, non per i servizi della pipeline. Il mock non rappresenta le capacità o il formato reale del modello UnifoLM.

## Limiti effettivi della verifica

- Tentata connessione HTTP a 10.32.55.4:8000 dall'ambiente di preparazione: connessione non riuscita, nessuna risposta HTTP. Questo non dimostra che il DGX sia spento o irraggiungibile dalla VM7.
- Nessuna inferenza sul DGX reale verificata. Manca lab_server.py o lo schema reale della risposta /predict_action.
- Docker Engine non disponibile: build delle immagini, networking Compose, mapping porte e permessi del volume sulla VM7 non provati.
- AppTest verifica il comportamento Streamlit; non è una verifica visuale con browser della nuova pagina. Le console 3D esistenti sono conservate.
- Nessun robot fisico, DDS, MuJoCo, Isaac o collegamento al manager Wazuh verificato.

Comando usato: `python -m unittest discover -s tests -v`. Le dipendenze per ripeterlo sono in requirements-test.txt. La suite usa solo localhost e porte temporanee; non contatta il DGX.
