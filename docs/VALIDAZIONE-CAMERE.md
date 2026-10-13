# Verifica camere — 28 settembre 2026

## Eseguito con successo

- Regressione motore, adapter WMA e Test Harness: **33/33 test passati**, nessuno saltato nella suite unittest.
- Servizio camere e twin avviati come processi locali reali, fuori da Docker.
- HTTPS delle tre pagine camera e certificato con SAN 10.32.55.9.
- Render PyBullet CPU delle tre camere: testa stereo 640×180, polsi 320×180.
- Differenza fra le due viste della testa verificata sui pixel: immagini non duplicate.
- Snapshot JPEG delle tre camere ricevuti e decodificati.
- Cambio altezza nel twin produce un cambiamento dell'immagine renderizzata, escludendo le etichette dal confronto.
- Offer malformata respinta; perdita del twin segnalata con health/snapshot HTTP 503.
- Controllo sintattico JavaScript console e client camere.
- Ispezione visiva della vista testa stereo; esempi JPEG in questa cartella.

Log: TEST-CAMERE.txt e TEST-REGRESSIONE-CAMERE.txt.

## Non verificato / bloccato

Il tentativo iniziale di test ZeroMQ TCP si arresta in libzmq con `Operation not permitted` durante la risoluzione degli indirizzi. La prova WebRTC si arresta durante `ifaddr.get_adapters()` con `PermissionError: Operation not permitted`. L'ambiente impedisce l'enumerazione delle interfacce necessaria a queste librerie.

Per verificare separatamente rendering e HTTPS, il test è stato rieseguito con `SKIP_ZMQ=1 SKIP_WEBRTC=1`: i controlli di trasporto vengono esplicitamente saltati. Quindi **il video WebRTC end-to-end e ZMQ TCP non sono dichiarati collaudati**, né sul server locale né sulla VM7. La configurazione predefinita consegnata abilita entrambi; queste esclusioni sono state solo per il test parziale.

Non sono stati eseguiti Docker build/Compose, test firewall/rete VM7, il client ufficiale Teleimager o prove su robot reale. Il DGX WMA rimane non raggiungibile dall'ambiente di preparazione. La compatibilità del sottoinsieme Teleimager si basa sull'ispezione del codice, non su un test completo con il client ufficiale.

## Verifica completa sulla VM7

Dopo installazione dipendenze in Python 3.11, eseguire senza variabili SKIP:

```bash
python scripts/test_cameras.py
```

La prova verifica anche frame video WebRTC decodificati sulle tre camere e discovery/JPEG ZMQ, usando porte temporanee. Verificare poi l'accesso effettivo dal PC ai tre URL https://10.32.55.9:60001–60003 e premere Start.
