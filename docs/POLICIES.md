# PEP Policies

All policy lives in one method: `Twin.command()` in `engine.py`. Every command —
from the WMA adapter, the red-team console, or a test — passes through it under a
single lock. A command is processed in three stages; it must clear all three to
be applied.

## Stage 0 — defaults the PEP assigns (never the caller's to spoof usefully)

Before any check, the PEP deep-copies the payload and fills missing fields:
`request_id` (uuid), `timestamp` (now), `identity` (= principal), `target`
(`g1d-01`), `via` (`pep`). The **principal** is passed separately by the server
(`operator`, `guest`, or `defender`) — the payload cannot choose it.

## Stage 1 — structural validation → `ValueError` (HTTP 400, *not* a policy denial)

These are malformed-request rejections, raised before policy is evaluated. They
never reach the SIEM as a DENY; the server returns 400.

| Check | Rule |
|---|---|
| payload is a JSON object | — |
| `action` is a string, `params` is an object | — |
| principal ∈ {operator, guest, defender} | — |
| `request_id` is a string of 1..128 chars | — |
| `timestamp` is a finite number (no bool/NaN/Inf) | — |
| every numeric param (`v`, `w`, `duration`, `value`) is a finite number | — |

Key point: a `move` with `v = NaN` or `v = "5"` is a **400**, not a policy
violation. Malformed input is rejected before it can be classified as an attack.

## Stage 2 — policy violations → DENY / MONITOR (rules G1D-101..108)

Evaluated as a **first-match-wins** chain. The rule numbers are *not* the
evaluation order — this table is in the order the code actually checks:

| Order | Rule | Name | Triggers when |
|---|---|---|---|
| 1 | **G1D-108** | bypass | `via != 'pep'` — someone tried to reach the virtual endpoint without going through the PEP |
| 2 | **G1D-106** | spoofing | `identity != principal` — declared identity differs from the authenticated principal |
| 3 | **G1D-101** | unauthorized | principal is revoked (in `blocked`), or a `guest` asks for anything but `status` |
| 4 | **G1D-104** | escalation | payload carries a `role` that differs from the principal (e.g. `role: admin` on an operator session) |
| 5 | **G1D-103** | scope | `target != 'g1d-01'`, or the action is not one of the known actions |
| 6 | **G1D-105** | replay | `request_id` already seen, **or** `|now − timestamp| > 30 s` (stale) |
| 7 | **G1D-102** | parameters | action is well-formed but a value breaks a lab/URDF limit (see below) |
| 8 | **G1D-107** | sequence | a movement action while not `armed`, **or** any non-recovery action while `estop` is latched |

Because it is first-match-wins, a command that both spoofs *and* has a bad
parameter is logged as **spoofing (G1D-106)** — the earlier check wins. Document
and demo order matters.

### Known actions (anything else → scope / G1D-103)
`arm`, `disarm`, `stop`, `estop`, `move`, `height`, `joint`, `gripper`, `home`,
`status`, `trajectory`.

### Parameter limits (G1D-102) — lab limits, not Unitree hardware specs
| Action | Limit |
|---|---|
| `move` | \|v\| ≤ 0.5 m/s, \|w\| ≤ 1 rad/s, 0 < duration ≤ 5 s |
| `height` | 0 ≤ value ≤ 0.42 m |
| `joint` | name must be a non-fixed URDF joint; value within that joint's URDF `[lower, upper]` |
| `gripper` | state ∈ {open, closed} |
| `trajectory` | distinct arm joints (name contains shoulder/elbow/wrist); 1..64 points; dt ∈ (0, 1] s; every point within URDF limits; ≤ 3 rad/s between consecutive points and from `start` |

### Sequence rules (G1D-107)
- **arm-before-motion:** `move`, `height`, `joint`, `gripper`, `home`,
  `trajectory` require `mode == 'armed'`. `arm`/`disarm`/`stop`/`estop`/`status`
  are exempt (you can always arm, stop, or query).
- **E-stop latch:** once `mode == 'estop'`, only `status`, `stop`, `estop` are
  allowed. Recovery requires a Defense `reset` from the `:4000` console — the
  model/operator cannot clear it.

### Replay window (G1D-105), the two interacting timers
- The `seen` cache remembers each `request_id` and is pruned after **60 s**.
- Independently, a command is stale if its `timestamp` is more than **30 s** off
  from now.

A resent request within 60 s is caught by `seen`. A request older than 30 s is
caught by the timestamp check regardless. Together they bound how long a captured
command stays replayable.

## Enforce vs. Monitor — both still block

`enforce` is toggled from the Defense console. The **only** difference it makes:

| Mode | Decision logged on a violation | Action applied? |
|---|---|---|
| enforce (default) | `DENY` | no |
| monitor | `MONITOR` | **no** |

This is stricter than a typical IDS "monitor" mode: a violating command is
**never applied in either mode** — monitor only changes the label/severity in the
SIEM, so you can demo "what would have been denied" without ever letting a bad
command through. A *valid* command is applied normally in both modes.

## Stage 3 — apply + ALLOW (G1D-200)

Only a command with no violation reaches the apply block, which mutates the
authoritative twin state (mode, velocity, height, joints, gripper, trajectory
end-pose) and logs an **ALLOW / G1D-200** event. `trajectory` mirrors only the
last validated point onto the twin; the physical simulator executes the full
chunk.

## MITRE ATT&CK mapping (best-effort, not official)

The `G1D-xxx` codes are a project convention, **not** MITRE identifiers. The
table below is an analyst interpretation that aligns each policy to the closest
technique — mostly **ATT&CK for ICS** (the right matrix for a cyber-physical
robot), with **Enterprise** where the violation is really about identity or
defeating a control. Treat the *fit* column honestly: "strong" means the
technique describes the attack directly; "weak" means it's the nearest label for
what is really a project-specific safety guardrail. Validate against the current
ATT&CK release before using these in a report.

| Code | Policy | Matrix | Technique | Tactic | Fit |
|---|---|---|---|---|---|
| G1D-101 | unauthorized | ICS | [T0855](https://attack.mitre.org/techniques/T0855/) Unauthorized Command Message | Execution / Impair Process Control | strong |
| G1D-102 | parameters | ICS | [T0836](https://attack.mitre.org/techniques/T0836/) Modify Parameter | Impair Process Control | strong |
| G1D-103 | scope | ICS | [T0855](https://attack.mitre.org/techniques/T0855/) Unauthorized Command Message | Execution | moderate — shares T0855 with G1D-101; distinguished by wrong target / unknown action |
| G1D-104 | escalation | Enterprise | [T1548](https://attack.mitre.org/techniques/T1548/) Abuse Elevation Control Mechanism | Privilege Escalation | strong |
| G1D-105 | replay | ICS | [T0830](https://attack.mitre.org/techniques/T0830/) Adversary-in-the-Middle | Collection / Lateral Movement | moderate — ATT&CK has no dedicated "replay"; AiTM is the enabling technique |
| G1D-106 | spoofing | Enterprise | [T1036](https://attack.mitre.org/techniques/T1036/) Masquerading | Defense Evasion | strong — pairs with ICS [T0859](https://attack.mitre.org/techniques/T0859/) Valid Accounts if a real principal is impersonated |
| G1D-107 | sequence | ICS | [T0831](https://attack.mitre.org/techniques/T0831/) Manipulation of Control | Impair Process Control | weak — a safe-state guardrail (arm/E-stop), not an adversary technique as such |
| G1D-108 | bypass | Enterprise | [T1562](https://attack.mitre.org/techniques/T1562/) Impair Defenses | Defense Evasion | moderate — pairs with ICS [T0886](https://attack.mitre.org/techniques/T0886/) Remote Services for direct, un-mediated endpoint access |

> **Wazuh note:** Wazuh rules support `<mitre><id>…</id></mitre>` tags, but the
> manager's bundled MITRE database is **Enterprise** — the ICS `T0xxx` ids above
> are not in it and will not resolve. If you wire these into
> `integrations/wazuh-rules.xml`, use the Enterprise ids only (T1548, T1036,
> T1562), or load an ICS MITRE dataset into the manager first.

## Worked examples per rule

Each command reaches the PEP as `{"payload": {...}, "principal": "..."}`. On the
twin console that is `POST :3000/api/command`; through the AI path the adapter
builds the same envelope with `principal: operator`. Example over the wire:

```bash
curl -sS -H 'Content-Type: application/json' \
  -d '{"payload":{"action":"arm"},"principal":"operator"}' \
  http://localhost:3000/api/command
```

The payloads below are what trips each rule (these mirror the `Twin.attack()`
fixtures and `tests/test_engine.py`). A `move` omits `w`/`duration` freely at the
PEP — they default to 0 and 1 — but are shown in full for clarity.

### G1D-108 — bypass
```json
{"payload":{"action":"move","params":{"v":0.2,"w":0,"duration":1},"via":"direct-dds"},"principal":"operator"}
```
→ **DENY G1D-108**. `via != 'pep'`: the command tried to reach the endpoint
without going through the PEP.

### G1D-106 — spoofing
```json
{"payload":{"action":"move","params":{"v":0.2,"w":0,"duration":1},"identity":"defender"},"principal":"operator"}
```
→ **DENY G1D-106**. Declared `identity` (defender) ≠ authenticated principal
(operator).

### G1D-101 — unauthorized
```json
{"payload":{"action":"move","params":{"v":0.2,"w":0,"duration":1}},"principal":"guest"}
```
→ **DENY G1D-101**. A `guest` may only call `status`. (Same rule fires for a
`move` by an operator after Defense `revoke operator`.)

### G1D-104 — escalation
```json
{"payload":{"action":"move","params":{"v":0.2,"w":0,"duration":1},"role":"admin"},"principal":"operator"}
```
→ **DENY G1D-104**. Payload carries `role: admin` on an operator session; the
payload cannot elevate the role.

### G1D-103 — scope
```json
{"payload":{"action":"move","params":{"v":0.2,"w":0,"duration":1},"target":"g1d-02"},"principal":"operator"}
```
→ **DENY G1D-103**. Target is not `g1d-01`. The same rule fires for an unknown
action, e.g. `{"action":"shell"}`.

### G1D-105 — replay
```json
{"payload":{"action":"status","request_id":"abc-123"},"principal":"operator"}   // 1st → ALLOW
{"payload":{"action":"status","request_id":"abc-123"},"principal":"operator"}   // 2nd → DENY G1D-105
```
→ **DENY G1D-105** on the resend (request_id already in `seen`). A stale
timestamp trips the same rule: `{"action":"status","timestamp": <now − 40s>}`.

### G1D-102 — parameters
```json
{"payload":{"action":"move","params":{"v":5,"w":0,"duration":1}},"principal":"operator"}
```
→ **DENY G1D-102**. \|v\|=5 > 0.5 m/s. Other examples that trip it:
`{"action":"joint","params":{"name":"left_elbow_joint","value":100}}` (outside
URDF limit), `{"action":"height","params":{"value":-0.1}}` (below 0), and a
`trajectory` whose points exceed 3 rad/s between steps.

### G1D-107 — sequence
```json
{"payload":{"action":"move","params":{"v":0.2,"w":0,"duration":1}},"principal":"operator"}   // twin still disarmed
```
→ **DENY G1D-107** — movement requires `arm` first. The E-stop latch variant:
after Defense `estop`, even `{"action":"arm"}` → **DENY G1D-107** until a Defense
`reset`.

### ALLOW — G1D-200 (the happy path)
```json
{"payload":{"action":"arm"},"principal":"operator"}                                           // → ALLOW
{"payload":{"action":"move","params":{"v":0.2,"w":0,"duration":1}},"principal":"operator"}     // → ALLOW G1D-200
```
→ **ALLOW G1D-200**. Arm first, then a valid move within limits applies to the
twin.

## Principals at a glance
| Principal | Capability |
|---|---|
| `operator` | full command set (subject to all policy above) |
| `guest` | `status` only — anything else is G1D-101 |
| `defender` | the only principal allowed `reset`, `enforce` and `restore` (PEP commands, G1D-101 otherwise); estop/revoke via `Twin.defense()` are open to the consoles |

## Where decisions go
Every ALLOW / DENY / MONITOR is appended to the in-memory event ring (last 2000)
and to `data/events.jsonl`, surfaced in the SIEM view and exportable via
`:3000/api/export`. Default rule id for non-policy events is `G1D-000`.
