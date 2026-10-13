> Documento storico della correzione precedente. Il Compose WMA attuale usa Dockerfile.adapter su base Python, non Dockerfile.sdk. Vedere README.md.

# Correzione build — 28 settembre 2026

## Causa

`docker/Dockerfile.sdk` partiva da `ubuntu:22.04` e chiedeva `ros-humble-ros-base` senza configurare il repository APT ROS 2. Il log mostra solo i repository Ubuntu. Non è un problema di GPU o di porte.

## Modifiche

- Base adapter: `ros:humble-ros-base-jammy`. È l'immagine ufficiale con ROS Humble già installato e il relativo entrypoint. Rimosso il pacchetto ROS ridondante dal comando apt.
- UID/GID di simulator e probe allineati a 10001, come twin e adapter. Tutti scrivono sul volume `evidence`, con file comuni; utenti diversi avrebbero provocato Permission denied all'avvio o al primo probe.
- Porte e comandi applicativi conservati. Nessun driver NVIDIA o desktop aggiunto.

## Applicare

Estrarre lo ZIP aggiornato sopra la cartella del progetto (dopo aver salvato eventuali proprie modifiche). Dalla cartella con compose.yaml:

```bash
docker compose build --pull adapter simulator probe
docker compose up -d
docker compose ps
docker compose logs --tail=80 adapter simulator probe
```

Non serve `--no-cache`: Docker invalida i layer modificati. Non serve installare ROS sull'host.

Se esiste già un volume avviato con la versione precedente e nei log compare Permission denied, riallineare i permessi senza cancellare i dati:

```bash
docker compose stop
docker compose run --rm --no-deps --user 0 --entrypoint sh adapter -c 'chown -R 10001:10001 /evidence'
docker compose up -d
```

Il chown interessa esclusivamente il volume audit montato su /evidence. Non usare `docker compose down -v`: cancellerebbe il volume.

Controlli:

```bash
curl -f http://localhost:6001/
curl -f http://localhost:6003/
```

Console 3000 e 4000; probe Streamlit 6002. Usare l'IP del server al posto di localhost da un altro PC.

## Limiti del progetto ricevuto

`services/adapter/port_service.py` e `services/simulator/port_service.py` sono endpoint di prova: registrano byte ricevuti e restituiscono JSON. Il loro stato ok attesta l'endpoint, non il funzionamento del robot o di DDS. Nessuna chiamata SDK controlla il robot. Il Dockerfile simulator clona unitree_mujoco ma non lo installa né lo avvia. Il twin 3D è il motore cinematico locale del servizio twin. Questo ZIP non implementa la camera sulla 60001.

Questa modifica corregge il problema di build segnalato e il conflitto di permessi; non aggiunge un simulatore fisico o il collegamento SDK/DDS.

## Verifica

Docker Engine non disponibile nell'ambiente di preparazione: build e avvio Compose non eseguiti. Suite esistente del motore: 12/12 test superati (non verifica il build Docker). Controllati i file modificati, le dipendenze CMake ufficiali Unitree e i permessi dichiarati dei servizi. I repository Git vengono ancora clonati dalle branch predefinite: eventuali errori successivi di compilazione/pip richiedono il relativo log.

Fonti:
- https://hub.docker.com/_/ros — tag ufficiale humble-ros-base-jammy.
- https://github.com/unitreerobotics/unitree_sdk2/blob/main/CMakeLists.txt — dipendenze C++ SDK.
- https://github.com/unitreerobotics/unitree_sdk2_python/blob/master/README.md — dipendenze Python e CycloneDDS.
