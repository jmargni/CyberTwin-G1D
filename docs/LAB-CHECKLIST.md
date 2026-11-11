# Checklist tecnica — lab G1-D reale (confronto sim vs reale)

Obiettivo della giornata: dimostrare che **il guardrail PEP si trasferisce dal simulatore al robot
reale** — comandi validi eseguiti, comandi non validi bloccati prima degli attuatori — e produrre la
**tabella di parità** (decisione sim vs reale vs alert Wazuh).

> Ordine: non si manda nessun comando *valido* al robot finché i limiti reali non sono calibrati e
> la non-bypassabilità del PEP non è verificata. I comandi *non validi* sono sicuri (vengono bloccati).

---

## A. Prima del lab (questa settimana)
- [ ] **Confermare l'architettura di enforcement:** chi inoltra il comando approvato su DDS — il PEP
      stesso oppure `robot_client` dopo l'OK del PEP. (Verificare con il team postino/Treviso.)
- [ ] **Config DDS reale** — `config/cyclonedds.xml`: interfaccia di rete corretta, **domain ID** del
      robot, topic dell'`unitree_sdk2`. Impostare `CYCLONEDDS_URI` se serve.
- [ ] **Flag `robot_client` in modalità robot reale** (senza simulatore), con `--pep_url` verso il PEP.
      Verificare i parametri esatti con chi ha scritto il bridge.
- [ ] **Agent Wazuh** sul PC del lab pronto (`scripts/wazuh_agent_setup.sh`), regole già sul manager.
- [ ] **E-stop fisico** del robot identificato e funzionante; persona dedicata a presidiarlo.
- [ ] Snapshot/backup del `policy.json` attuale (per tornare ai valori sim dopo il lab).

## B. Setup in lab
- [ ] Robot in stato sicuro, area libera, nessuno a portata di braccio.
- [ ] **Rete DDS:** il PC di controllo vede il robot (discovery DDS ok, ping sull'interfaccia).
- [ ] Avviare il **PEP**: `cd <repo> && python3 server.py`.
- [ ] Avviare `robot_client` puntato sul **DDS reale** + `--pep_url http://127.0.0.1:3000`.
- [ ] `policy.json`: partire con **valori conservativi** (velocità e workspace ridotti).
- [ ] `events.jsonl` resettato (`truncate -s 0 data/events.jsonl`).

## C. Verifica PEP inline + non aggirabile  ← CRITICA (regola G1D-108)
- [ ] **Comando valido passa:** dalla console un comando lecito → PEP **ALLOW** → il robot si muove.
- [ ] **Comando non valido bloccato:** uno scenario attacco dalla console Red Team → PEP **DENY** →
      il robot **non** si muove, alert su Wazuh.
- [ ] **`robot_client` rispetta il DENY:** su decisione DENY il client **non** pubblica su DDS.
- [ ] **Test bypass (G1D-108):** tentare una pubblicazione **diretta** su DDS senza passare dal PEP →
      deve essere **impedita o rilevata**. Se un percorso diretto al DDS esiste e muove il robot, il
      guardrail **non è nel loop**: fermarsi qui e chiudere quel percorso prima di proseguire.

## D. Calibrazione limiti reali (prima dei comandi validi)
- [ ] **Misurare il workspace reale** → impostare in `policy.json`: `geofence` (box), `keepout`
      (zone interdette), posizione reale della scatola.
- [ ] **Limiti giunto/velocità** vs inviluppo di sicurezza reale del G1-D: partire **sotto** le soglie
      Unitree, poi alzare gradualmente.
- [ ] **Test incrementale:** prima piccoli movimenti a bassa velocità, verificare corrispondenza col
      comando atteso, poi ampliare.

## E. Protocollo di confronto (parity)
- [ ] **Passata in MONITOR** (enforce off): eseguire lo script di comandi, osservare le decisioni
      senza bloccare nulla — controllo di sicurezza.
- [ ] **Passata in ENFORCE:** ripetere lo stesso script, verificare che i blocchi avvengano.
- [ ] **Compilare la tabella di parità** (vedi `docs/LAB-READINESS.md`): per ogni comando, decisione
      sim vs reale vs alert Wazuh. Stesso `rule_id`/decisione = guardrail trasferito.
- [ ] `bash scripts/smoke.sh` contro il PEP reale → suite guardrail 10/10.
- [ ] (Se il modello è abbastanza avanti) qualche tentativo di **pack supervisionato**, dichiarato come
      *misura del sim-to-real gap*, non come pass/fail del task.

## F. Sicurezza (sempre attiva)
- [ ] E-stop fisico presidiato per tutta la sessione.
- [ ] Nessuno a portata del braccio durante il movimento; zona interdetta sgombra.
- [ ] Velocità conservative; aumentare solo dopo conferma del comportamento.
- [ ] In caso di dubbio: E-stop, `disarm`, analisi del log, poi ripartire.

## G. Chiusura
- [ ] `disarm` + reset del robot, PEP in stato sicuro.
- [ ] Salvare `data/events.jsonl` e la **tabella di parità** come evidenza.
- [ ] Ripristinare `policy.json` ai valori sim (se si torna al simulatore).
- [ ] Nota breve: cosa ha coinciso sim↔reale, cosa no, gap osservati.

---

### Comandi di riferimento
```bash
# attacco dalla console (produce un DENY)
curl -s -XPOST localhost:3000/api/attack -H 'Content-Type: application/json' -d '{"scenario":"geofence"}' | jq '{decision,rule_id}'
# verifica guardrail completo
bash scripts/smoke.sh
# alert sul manager Wazuh
sudo tail -f /var/ossec/logs/alerts/alerts.json | jq 'select(.rule.groups[]?=="g1d") | {agent:.agent.name, rule:.rule.id, g1d:.data.rule_id, level:.rule.level}'
```

**Criterio di stop:** se il PEP non è dimostrabilmente inline e non aggirabile sul DDS reale (sezione C),
non si inviano comandi validi al robot — si chiude prima quel percorso.
