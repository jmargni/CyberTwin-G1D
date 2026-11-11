# Lab readiness — sim vs. real G1-D comparison

**Question:** are we ready to compare the simulated environment with the real robot in the lab?
**Short answer:** **Yes for the security/guardrail layer and the command pipeline. Not yet for task
performance** — the manipulation model is still training and hasn't reliably packed the camera even
in sim, and it learned on simulator images/positions (a known sim-to-real perception gap). Friday's
value is **measuring that gap and proving the guardrail transfers**, not demoing a finished
pick-and-place.

Keep two things separate in the conversation:
- **PEP / security layer** — our deliverable. Tested and portable. **Ready.**
- **Manipulation model** — still training. Not our deliverable. **Measure, don't promise.**

## The pivotal item to confirm first
**Is the PEP inline on the real robot's DDS bus, and non-bypassable?**
Required path: model/console → **PEP** → DDS → actuators, with nothing able to publish to DDS around
the PEP. This is exactly what rule **G1D-108 (bypass)** models. If the real DDS path doesn't go
through the PEP, the comparison shows a guardrail that isn't in the loop — close this before committing.

## Ready vs. needs work
| Ready | Needs calibration / work before Friday |
|---|---|
| PEP enforcement — 10/10 guard suite | **PEP inline on real DDS + non-bypass proof** (pivotal) |
| `policy.json` external policy (PDP) | Re-measure **real** workspace → geofence, keep-out, box coords |
| Enriched audit trail (2.8-C fields) | Validate joint/speed limits vs. real G1-D safe envelope |
| Wazuh pipeline + agent setup script | Wazuh agent on lab compute → real `events.jsonl` |
| Operator / Red-Team consoles | Model task success on real robot is **unknown** |
| `smoke.sh` guard verification | Conservative speeds; start in MONITOR, then ENFORCE |

## Friday plan
1. **Confirm PEP inline on real DDS** + prove non-bypass.
2. **Re-measure the real workspace** → set real geofence / keep-out / box position in `policy.json`;
   start with conservative speeds.
3. **Wazuh agent on lab compute** → `scripts/wazuh_agent_setup.sh`, rules already on the manager.
4. **Run the comparison protocol** (below).
5. **Safety:** human on the physical E-stop, keep-out zone clear, no one in reach during motion;
   first pass in **MONITOR** (observe), then flip to **ENFORCE**.

## What the comparison produces — a parity table
For each scripted command, run it **identically on sim and real** and record:

| Command | Sim decision | Real decision | Wazuh alert | Match |
|---|---|---|---|---|
| Allowed arm sequence | ALLOW G1D-200 | ALLOW G1D-200 | level 3 | ✓ |
| Over-speed params | DENY G1D-102 | DENY G1D-102 | level 8 | ✓ |
| Privilege escalation | DENY G1D-104 | DENY G1D-104 | level 12 | ✓ |
| Identity spoofing | DENY G1D-106 | DENY G1D-106 | level 13 | ✓ |
| Geofence / zona interdetta | DENY G1D-109 | DENY G1D-109 | level 12 | ✓ |
| PEP bypass (direct DDS) | DENY G1D-108 | DENY G1D-108 | level 15 | ✓ |

Same `rule_id` / decision on both sides = **the guardrail transfers**. Plus, if the model is far
enough along, a few **supervised** pack attempts for a qualitative perception/motion read — explicitly
framed as "measuring the sim-to-real gap," not a pass/fail on the task.

## Bottom line
Confident, defensible **yes** to the guardrail-and-pipeline comparison next Friday. We measure the
sim-to-real gap for the model rather than claim it's solved — and we do not go to the real robot until
the PEP is proven inline and non-bypassable on the DDS bus.
