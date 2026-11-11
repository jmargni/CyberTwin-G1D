# Prontezza per il lab — confronto G1-D sim vs reale

**Domanda:** siamo pronti a confrontare l'ambiente simulato con il robot reale in laboratorio?
**Risposta breve:** **Sì per il livello di sicurezza/guardrail e per la pipeline di comando. Non ancora
per le prestazioni del task** — il modello di manipolazione è ancora in addestramento e non ha ancora
impacchettato la camera in modo affidabile nemmeno in simulazione, ed è stato addestrato su immagini e
posizioni del simulatore (noto *sim-to-real gap* percettivo). Il valore di venerdì è **misurare quel
gap e dimostrare che il guardrail si trasferisce**, non mostrare un pick-and-place finito.

Tenere separate due cose nella discussione:
- **Livello PEP / sicurezza** — il nostro deliverable. Testato e portabile. **Pronto.**
- **Modello di manipolazione** — ancora in training. Non è il nostro deliverable. **Si misura, non si promette.**

## Il punto da confermare per primo
**Il PEP è inline sul bus DDS del robot reale ed è non aggirabile?**
Percorso richiesto: modello/console → **PEP** → DDS → attuatori, senza che nulla possa pubblicare su
DDS scavalcando il PEP. È esattamente ciò che modella la regola **G1D-108 (bypass)**. Se il percorso
DDS reale non passa dal PEP, il confronto mostra un guardrail che non è nel loop — va chiuso prima di impegnarsi.

## Pronto vs. da calibrare
| Pronto | Da calibrare / da fare prima di venerdì |
|---|---|
| Enforcement PEP — suite guardrail 10/10 | **PEP inline sul DDS reale + prova di non-bypass** (critico) |
| `policy.json` — policy esterna (PDP) | Ri-misurare il workspace **reale** → geofence, zone interdette, coord. scatola |
| Audit trail arricchito (campi 2.8-C) | Validare limiti giunto/velocità vs inviluppo di sicurezza reale del G1-D |
| Pipeline Wazuh + script setup agent | Agent Wazuh sul PC del lab → `events.jsonl` reale |
| Console Operator / Red-Team | Successo del task sul robot reale **sconosciuto** |
| `smoke.sh` verifica guardrail | Velocità conservative; partenza in MONITOR, poi ENFORCE |

## Piano per venerdì
1. **Confermare PEP inline sul DDS reale** + prova di non-bypass.
2. **Ri-misurare il workspace reale** → impostare geofence / zone interdette / posizione scatola in
   `policy.json`; partire con velocità conservative.
3. **Agent Wazuh sul PC del lab** → `scripts/wazuh_agent_setup.sh`, regole già sul manager.
4. **Eseguire il protocollo di confronto** (sotto).
5. **Sicurezza:** persona dedicata all'E-stop fisico, zona interdetta sgombra, nessuno a portata del
   braccio durante il movimento; prima passata in **MONITOR** (osservazione), poi **ENFORCE**.

## Cosa produce il confronto — una tabella di parità
Per ogni comando scriptato, eseguito **identico su sim e reale**, registrare:

| Comando | Decisione sim | Decisione reale | Alert Wazuh | Coincide |
|---|---|---|---|---|
| Sequenza arm consentita | ALLOW G1D-200 | ALLOW G1D-200 | livello 3 | ✓ |
| Parametri over-speed | DENY G1D-102 | DENY G1D-102 | livello 8 | ✓ |
| Escalation privilegi | DENY G1D-104 | DENY G1D-104 | livello 12 | ✓ |
| Spoofing identità | DENY G1D-106 | DENY G1D-106 | livello 13 | ✓ |
| Geofence / zona interdetta | DENY G1D-109 | DENY G1D-109 | livello 12 | ✓ |
| Bypass del PEP (DDS diretto) | DENY G1D-108 | DENY G1D-108 | livello 15 | ✓ |

Stesso `rule_id` / decisione su entrambi = **il guardrail si trasferisce**. In più, se il modello è
abbastanza avanti, qualche tentativo di **pack supervisionato** per una lettura qualitativa di
percezione/movimento — dichiarato esplicitamente come "misura del sim-to-real gap", non come pass/fail del task.

## In sintesi
**Sì** convinto e difendibile al confronto di guardrail e pipeline sul robot reale venerdì prossimo.
Misuriamo il sim-to-real gap del modello invece di dichiararlo risolto — e non si va sul robot reale
finché il PEP non è dimostrabilmente inline e non aggirabile sul bus DDS.
