# Camere simulate — VM7 10.32.55.9

Questa versione aggiunge il servizio Compose `cameras`. Renderizza la scena del G1-D su CPU usando l'URDF e le mesh originali già inclusi. Legge lo stato autorevole da `twin:3000` attraverso la porta host 3000: la stessa posa usata dalle console e dall'adapter WMA.

| Vista | URL |
|---|---|
| Testa binoculare | https://10.32.55.9:60001 |
| Polso sinistro | https://10.32.55.9:60002 |
| Polso destro | https://10.32.55.9:60003 |

Sulla VM sono utilizzabili anche https://localhost:60001, :60002 e :60003. Nel browser aprire la pagina e premere **Start**. I link sono aggiunti alle console e al Test Harness WMA.

## Avvio e aggiornamento

Estrarre lo ZIP aggiornato nella cartella del progetto, salvando prima le proprie modifiche. Se `.env` esiste già, aggiungere le variabili delle camere da `.env.example` senza sovrascrivere la configurazione WMA.

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 cameras
```

Al primo avvio vengono creati un certificato TLS autofirmato e la chiave nel volume `camera-certs`, validi per 10.32.55.9, 127.0.0.1 e localhost. Il browser segnalerà che il certificato non è attendibile: per il laboratorio si può importare il certificato nel trust store del PC o accettare l'eccezione per ciascuno dei tre URL. Non disattivare globalmente i controlli TLS del browser. Non vengono installate CA sul computer utente.

I certificati persistono tra i riavvii. Per usare certificati aziendali sostituire cert.pem/key.pem nel volume, rendendoli leggibili da UID 10001, e riavviare cameras. La creazione automatica avviene solo quando la coppia è assente; non gestisce il rinnovo.

Verifiche iniziali dalla VM (qui `-k` serve solo per il certificato autofirmato del lab):

```bash
curl -kfsS https://localhost:60001/healthz
curl -kf -o head.jpg https://localhost:60001/snapshot.jpg
curl -kf -o left.jpg https://localhost:60002/snapshot.jpg
curl -kf -o right.jpg https://localhost:60003/snapshot.jpg
```

## Rete, WebRTC e porte

Le camere usano `network_mode: host` su Linux. **Non è una traduzione NAT Docker:** il servizio si lega direttamente alle interfacce della VM. Questo soddisfa l'accesso dall'IP VM e da localhost ed evita che WebRTC annunci l'indirizzo privato del container. Le altre console mantengono le pubblicazioni porte Docker precedenti.

- 60001–60003 TCP: pagine HTTPS, snapshot e signaling WebRTC `/offer`.
- WebRTC video: UDP su porte dinamiche negoziate da ICE. Aprire solo le tre TCP non garantisce il video. Tra PC e VM deve essere consentito questo traffico UDP nella rete del laboratorio. Nessuna regola firewall viene modificata dal progetto.
- Non sono configurati STUN/TURN: si assume accesso diretto PC↔VM sulla LAN/VPN. Per attraversare NAT/router aggiuntivi serve una configurazione ICE/TURN dedicata.
- 60000 TCP: richiesta/risposta configurazione ZMQ.
- 5555, 5556, 5557 TCP: JPEG ZMQ rispettivamente testa, SX, DX. Sono scelte di questa configurazione del lab, non porte hardware G1-D dichiarate ufficiali.

`CAMERA_HOST_IP` serve per il certificato, non assegna un IP alla VM. La VM deve già avere 10.32.55.9. Lasciare BIND_IP=0.0.0.0 per consentire al servizio camere di leggere il twin su 127.0.0.1:3000.

## Rendering e risorse

Default: due viste testa da 320×180 unite in un frame 640×180, più due polsi da 320×180, target 3 fps. Sono due posizioni ottiche distinte con baseline virtuale 60 mm, non due copie dello stesso frame. Baseline, FOV e posizionamenti non sono calibrazioni Unitree. La scena contiene pavimento, tavolo e oggetti di riferimento.

Nessuna GPU, CUDA, desktop o dispositivo /dev/video richiesto. Il servizio camere è limitato a 2 CPU e 2 GiB. Gli fps effettivi dipendono dalla CPU, dalle dimensioni e dai client connessi; non sono garantiti. Il rendering delle tre camere è continuo perché alimenta anche i publisher ZMQ. Ogni client WebRTC richiede codifica video software; massimo 4 sessioni per camera.

Per ridurre il carico in `.env`:

```ini
CAMERA_WIDTH=160
CAMERA_HEIGHT=120
CAMERA_FPS=2
```

Poi `docker compose up -d`. Il servizio restituisce 503 su health e snapshot se perde il twin; non presenta una vecchia immagine come dato fresco. Non simula collisioni, profondità, esposizione o rumore dei sensori.

## Teleimager e passaggio futuro al reale

`config/teleimager_server.yaml` è incluso. È **la configurazione del publisher virtuale di questo progetto**, non un file da copiare così com'è sul robot: `simulated_cpu` non è un backend ufficiale Teleimager.

Il servizio implementa il sottoinsieme di protocollo osservato nel client ufficiale:

1. REQ/REP su 60000: risponde con un dizionario di camere.
2. PUB/SUB su una porta per camera: ogni messaggio contiene un singolo JPEG, senza multipart o prefisso topic.
3. WebRTC: signaling JSON SDP tramite POST `/offer`, risposta con `sdp` e `type`.

La compatibilità completa con tutte le versioni Teleimager non è certificata. Il client corrente usa `teleimager.client.TeleImageClient`; il vecchio URL image_client.py non era presente sul ramo main consultato.

Sul robot reale si userà il Teleimager già installato sul PC2, sostituendo host/config e verificando nomi camere, risoluzioni, intrinseci/estrinseci e formato stereo. Non si deve installare il nostro renderer virtuale sul PC2 per leggere le camere fisiche.

Le immagini non sono automaticamente inserite nelle observation WMA: manca ancora lo schema immagine richiesto dal vero /predict_action. Il flusso stato/comandi WMA continua come prima. Non viene inventata una serializzazione immagini per il modello.

Fonti consultate:
- Sezione Image Service Interface G1D fornita dall'utente: HTTPS 60001, 60002, 60003.
- https://github.com/unitreerobotics/teleimager/blob/main/src/teleimager/client.py — discovery 60000 e frame JPEG ZMQ.
- https://github.com/unitreerobotics/teleimager/blob/main/src/teleimager/server.py — SDP /offer e WebRTC.

## Test da eseguire sulla VM7

La prova separata delle camere richiede Python 3.11 e le dipendenze camera. Avvia istanze locali su porte temporanee, un twin temporaneo e client WebRTC/ZMQ; non richiede DGX o robot fisico:

```bash
python3.11 -m venv .venv-camera
. .venv-camera/bin/activate
pip install --only-binary=:all: -r requirements-camera.txt
python scripts/test_cameras.py
```

Vedere docs/VALIDAZIONE-CAMERE.md per l'esito effettivamente ottenuto nell'ambiente di preparazione e i controlli non eseguiti.
