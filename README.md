> **Aggiornamento camere VM7 (10.32.55.9):** aggiunte tre camere virtuali CPU con WebRTC/HTTPS, testa stereo e due polsi. Configurazione, rete e limiti in [docs/CAMERE.md](docs/CAMERE.md). Le prove nuove sono in [docs/VALIDAZIONE-CAMERE.md](docs/VALIDAZIONE-CAMERE.md).

# DGS G1-D — Adapter UnifoLM-WMA

Versione adattata allo script `dgs_g1d_wma_adapter.py` fornito dall'utente.

Il twin cinematico CPU delle console è la sorgente autorevole dello stato. Il servizio simulator sulla 6003 espone tale stato, l'adapter sulla 6001 invia `POST /predict_action` al DGX e, su richiesta esplicita, applica un comando riconosciuto attraverso il PEP del twin. Il Test Harness Streamlit è sulla 6002. Non serve GPU sulla VM7; l'inferenza WMA è sul DGX.

**Non è un simulatore fisico MuJoCo/Isaac e non controlla un robot reale.** Il formato effettivo del WMA non è stato fornito: il contratto `observation` dello script è provvisorio. Il mapping di vettori/action tensor va implementato dopo aver letto `lab_server.py` o una risposta reale. Nessuna risposta ignota diventa automaticamente un movimento.

## Avvio

Linux con Docker Engine e Compose. Dalla cartella estratta che contiene compose.yaml:

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 adapter simulator probe
```

Il build usa immagini Python, requests/Streamlit e un servizio camere PyBullet/aiortc senza GPU; non compila ROS, SDK o MuJoCo. `docker/Dockerfile.sdk` è conservato come riferimento storico e non viene usato da Compose. I vecchi port_service.py sono anch'essi conservati ma non vengono avviati.

Il progetto Compose mantiene il nome `g1d-cyber-twin` e il volume `evidence` del pacchetto precedente. Se esistono log creati con vecchi UID e compare Permission denied:

```bash
docker compose stop
docker compose run --rm --no-deps --user 0 --entrypoint sh adapter -c 'chown -R 10001:10001 /evidence'
docker compose up -d
```

Non usare `down -v` per aggiornare: cancella l'audit.

| Porta | Funzione |
|---|---|
| 3000/TCP | Robot 3D, comandi, test, SIEM |
| 4000/TCP | Red Team / Defense |
| 6001/TCP | Adapter WMA: GET /healthz, GET /last, POST /cycle |
| 6002/TCP | Test Harness Streamlit |
| 6003/TCP | Facciata simulatore: GET /state, POST /command |
| 60001–60003/TCP | HTTPS/WebRTC: testa, polso SX, polso DX |
| 60000/TCP | Discovery ZMQ camere |
| 5555–5557/TCP | Frame JPEG ZMQ: porte del laboratorio |

Apri `http://IP-VM7:6002`. Tutte le porte sono anche raggiungibili da localhost sulla VM. I vecchi echo UDP sono rimossi: questa pipeline usa HTTP/TCP. Le camere CPU sono ora disponibili via HTTPS/WebRTC su 60001–60003: vedere `docs/CAMERE.md`.

Le console e gli endpoint sono un laboratorio senza autenticazione reale: usare una rete lab controllata. Il PEP simula policy; non sostituisce l'autenticazione di rete.

## Configurazione DGX

`.env`:

```ini
BIND_IP=0.0.0.0
WMA_BASE_URL=http://10.32.55.4:8000
WMA_ENDPOINT=/predict_action
WMA_TIMEOUT=120
MAX_OBSERVATION_AGE=10
```

Il container adapter deve poter raggiungere il DGX sulla TCP 8000. `WMA_TIMEOUT` è il read timeout requests, non una garanzia sul tempo massimo totale; connect timeout 3 secondi. Non vengono seguiti redirect né ritentati automaticamente i comandi.

`MAX_OBSERVATION_AGE` limita l'età dell'osservazione prima dell'invio del comando al PEP. Un'inferenza più lenta produce STALE in modalità esecuzione. L'anteprima resta disponibile. Adeguare questa soglia consapevolmente alle latenze del modello e allo scopo del laboratorio.

## Primo test con DGX reale

1. Avvia il progetto, apri `:6002` e lascia disattivata la casella di esecuzione.
2. Scrivi l'istruzione e premi **Esegui ciclo WMA**.
3. Il risultato contiene observation, payload inviato, risposta WMA e mapping. Scarica l'evidenza JSON.
4. Se appare UNMAPPED, il collegamento può aver funzionato ma manca il mapping della risposta reale. Condividere il JSON o il contratto di `lab_server.py` per completarlo.
5. Solo per il contratto esplicito supportato: arma il robot dalla 3000 e attiva la casella esecuzione. E-stop e policy sono valutati dal twin al momento del comando.

Da terminale, stessa prova di anteprima:

```bash
curl -sS http://localhost:6003/state
curl -sS --max-time 135 -H 'Content-Type: application/json' \
  -d '{"instruction":"stand","execute":false}' http://localhost:6001/cycle
```

`stand` è inoltrato come istruzione linguistica, non è interpretato localmente come arm o comando posturale. L'adapter non arma mai il robot e non può rimuovere E-stop. Il ciclo è manuale, uno per richiesta; non è un controllo continuo.

## Contratto e mapping

Input WMA:

```json
{"observation":{"robot":"Unitree G1-D","mode":"simulation","robot_mode":"armed","position":{"x":0,"y":0,"z":0},"orientation":{"roll":0,"pitch":0,"yaw":0},"height":0,"joints":{},"velocity":{"v":0,"w":0},"gripper":"open","instruction":"stand"}}
```

Le joint contengono i valori del modello URDF. `z=0` indica il riferimento del pavimento; la colonna è `height`. Camere, forze e contatti non fanno parte dell'osservazione.

Unica risposta eseguibile di laboratorio (non attribuita all'API reale UnifoLM):

```json
{"command":{"action":"move","params":{"v":0.1,"w":0,"duration":1}}}
```

| action | params |
|---|---|
| status, stop | oggetto vuoto |
| move | v m/s, w rad/s, duration s: tutti obbligatori |
| height | value m |
| joint | name URDF e value rad o m secondo il joint |
| gripper | state: open oppure closed |

Limiti del PEP del progetto: v ±0.5 m/s, w ±1 rad/s, durata >0 e ≤5 s, colonna 0–0.42 m, limiti joint URDF. Sono limiti del lab, non specifiche hardware Unitree.

L'identità operator, il target, il timestamp e il request_id sono assegnati dall'adapter, mai dal modello. Campi di privilegio inseriti nel command causano UNMAPPED. Array di azioni, joint senza nomi, output non JSON ed errori non vengono eseguiti. Non è previsto un comando diretto su DDS.

Gli stati principali sono PREVIEW, APPLIED, BLOCKED, UNMAPPED, WMA_ERROR, STALE e TRANSPORT_ERROR. HTTP 200 su /cycle significa che il report è disponibile: controllare `status` e `applied`. Un timeout durante l'invio del comando lascia l'esito incerto: verificare lo stato/audit del twin, non ritentare automaticamente. HTTP 409 indica che un altro ciclo è in corso.

## Test locale senza DGX

```bash
docker compose -f compose.yaml -f compose.mock.yaml up -d --build
```

Il mock restituisce sempre `command: status` e `mock: true`; non esegue inferenza. Apri la 6002 ed esegui un ciclo. Per tornare al DGX reale:

```bash
docker compose up -d --remove-orphans
```

## Suite automatica sui sorgenti

Con Python 3.12:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-test.txt
python -m unittest discover -s tests -v
```

La suite avvia processi twin, simulator e adapter su porte locali temporanee e un mock WMA. Non contatta il DGX. Esito verificato e limiti in `docs/VALIDAZIONE-WMA.md`. La suite UI precedente resta di 9 scenari; i test WMA sono la suite Python separata.

## Audit e SIEM

I comandi passati al PEP compaiono nel SIEM delle console e in `events.jsonl`. Tutti i cicli WMA, incluse anteprime ed errori, sono in `wma-cycles.jsonl`, nello stesso volume evidence, e l'ultimo risultato è leggibile da `:6001/last`. Il Test Harness permette anche il download JSON. I log dei cicli non vengono automaticamente incorporati nella tabella SIEM della console.

```bash
docker compose cp adapter:/evidence/wma-cycles.jsonl ./wma-cycles.jsonl
```

Le regole Wazuh del progetto sono conservate. L'invio a un manager Wazuh non viene configurato o verificato da questa modifica. I file audit non hanno rotazione automatica.

## Comando `trajectory` per il postino UnifoLM-WMA

Aggiunto per il bridge del client `robot_client.py` (repository `unifolm-wma-postino`, opzione `--pep_url`). Un chunk di azioni del modello passa dal PEP come **un solo** comando:

```json
{"payload":{"action":"trajectory","params":{"joints":["left_shoulder_pitch_joint","..."],"points":[[0.1,"..."],"..."],"dt":0.0667,"start":[0.1,"..."],"gripper":"open"},"request_id":"…","timestamp":0,"identity":"operator","via":"pep","target":"g1d-01"},"principal":"operator"}
```

| Campo | Significato |
|---|---|
| joints | nomi URDF dei joint del braccio (shoulder, elbow, wrist), distinti |
| points | 1..64 punti, un valore per joint |
| dt | secondi tra due punti, (0, 1] |
| start | opzionale: posa misurata prima del primo punto |
| gripper | opzionale: open / closed alla fine del chunk |

Controlli oltre a quelli esistenti (identità, replay, armed, E-stop): limiti URDF di ogni punto e velocità massima di laboratorio di 3 rad/s tra punti consecutivi, e da `start` al primo punto (G1D-102). Se consentito, il twin riporta nella telemetria l'ultimo punto e lo stato delle mani. In modalità monitor la violazione è MONITOR e il twin non applica il chunk.

`GET /api/robot` restituisce solo `robot` ed `enforce`, senza lo storico eventi: il client lo interroga prima di ogni passo per fermarsi entro un passo dopo un E-stop o un disarm.

Test: `python3 -m unittest discover -s tests` (classe `TrajectoryTests` in `tests/test_engine.py`).
