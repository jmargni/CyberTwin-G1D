# Wazuh overlay for the PEP demo

Adds a live SIEM to the PEP demo: every policy decision the twin logs becomes a
**Wazuh alert**, leveled by severity, on a dashboard the audience can watch.

This is an *overlay*. The base loop and the four presenter scenarios are in the
postino repo — `unitree_deploy/scripts/DEMO_PEP.md` (narrative) and
`DEMO_COMMANDS.txt` (exact launch commands). Run those as-is; Wazuh needs no
change to the twin or the loop — it just tails `data/events.jsonl`.

## One-time setup (already done on `hubble`)

```bash
sudo bash scripts/wazuh_setup.sh      # rules -> /var/ossec, localfile -> events.jsonl, restart
```

Wazuh 4.13 all-in-one (manager + indexer + dashboard), installed natively via
`wazuh-install.sh -a`. The setup script loads `integrations/wazuh-rules.xml`
and points the manager at `data/events.jsonl`. Verify any time with:

```bash
grep -m1 'G1D-107' data/events.jsonl | sudo /var/ossec/bin/wazuh-logtest
```

## Add one window to the demo

Alongside the two browser windows from `DEMO_PEP.md` (robot sim + twin console),
open a third:

- **Dashboard:** `https://localhost` → *Threat Hunting* / *Security events*,
  filter `rule.groups: g1d`. Big, color-coded, good for a room.
- **Or terminal** (lighter, no login): keep this running where the audience sees it —
  ```bash
  sudo tail -f /var/ossec/logs/alerts/alerts.json \
    | jq '{t:.timestamp, rule:.rule.id, level:.rule.level, g1d:.data.rule_id, desc:.rule.description}'
  ```

As the loop runs, allowed chunks (`G1D-200`) stay quiet (level 3); the moment
you trigger a scenario, a high-level alert pops in Wazuh in real time.

## What each scenario raises in Wazuh

The scenarios in `DEMO_PEP.md` (and the Red Team console's `/api/attack`
buttons on `:4000`) map to these alerts:

| Demo action | PEP code | Wazuh rule | Level |
|---|---|---|---|
| Revoke operator identity | G1D-101 unauthorized | 110101 | 8 |
| Over-speed / bad params | G1D-102 parameters | 110102 | 8 |
| Wrong target / unknown action | G1D-103 scope | 110103 | 8 |
| **Privilege escalation** | G1D-104 escalation | 110104 | **12** (High) |
| Replay a stale command | G1D-105 replay | 110105 | 8 |
| **Identity spoofing** | G1D-106 spoofing | 110106 | **13** (High) |
| Move before arm (sequence) | G1D-107 sequence | 110107 | 8 |
| **Bypass the PEP (direct DDS)** | G1D-108 bypass | 110108 | **15** (Critical) |
| **Geofence / zona interdetta** | G1D-109 geofence | 110109 | **12** (High) |
| Allowed command | G1D-200 | 110001 | 3 |

Escalation, spoofing and bypass are the "someone is attacking the control path"
cases, so they're raised to **High (12–13)** and **Critical (15)** — they fill
the High/Critical severity tiles on the dashboard, standing out from the
level-8 denials. Wazuh severity bands: Low 0–6, Medium 7–11, High 12–14,
Critical 15+.

## The line to say

"Blocking the command is the robot staying safe. The alert is your SOC finding
out it happened. Same event, logged once by the PEP, feeds both — the twin
stops the robot, Wazuh tells the humans."

## Reset between runs

The twin controls reset the robot (see `DEMO_PEP.md`). Wazuh keeps the full
alert history — that's the point, it's the audit trail. Nothing to reset.
