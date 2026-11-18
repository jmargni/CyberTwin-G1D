# DGS CyberTwin G1-D — Purpose & Architecture

## Aim in one line

Prove that a **Policy Enforcement Point (PEP)** holds for a humanoid robot's
commands — no matter who sends them or how they are shaped — by routing *every*
command through a single authoritative twin that validates against policy and
logs the decision before any state changes.

The model is just one more untrusted client. The PEP is the single chokepoint.

## The authoritative twin + PEP

`engine.py` → `Twin`. One deterministic kinematic state, one `command()` gate.
No shell execution, no DDS, no physical robot I/O. It loads real joint limits
from `public/model/g1_d.urdf` and records every decision (ALLOW / DENY /
MONITOR / DEFENSE) to the SIEM and `data/events.jsonl`.

The full policy set, evaluation order, and a worked example per rule are in
[POLICIES.md](POLICIES.md).

Two ways drive the same PEP:

## Side 1 — AI / operator path

```
client prompt → adapter :6001 → WMA model (DGX) → map_command → PEP → twin
```

A legitimate operator asks the model for an action. The adapter
(`dgs_g1d_wma_adapter.py`) never trusts the model:

- identity, request_id, PEP route and target are assigned **locally**, never
  taken from model output;
- unknown / unmapped model output becomes `UNMAPPED` and is **not** executed;
- the PEP still gates the mapped command.

The simulator facade (`:6003`, `services/simulator/app.py`) exposes twin state
as the WMA `observation` and forwards commands to the twin. This is the
supervised happy path.

## Side 2 — cyber-range / security path

The consoles drive the **same** twin adversarially — this is the training /
demonstration purpose:

- **:3000** — 3D twin + SIEM view (operator screen)
- **:4000** — Red Team / Defense console
- **8 attack scenarios** (`Twin.attack()`, rules `G1D-101..108`): unauthorized,
  parameter abuse, out-of-scope, privilege escalation, replay, spoofing,
  sequence abuse, PEP bypass
- **Defense actions**: E-stop (latched) and revoke identity on the direct channel
  (`Twin.defense()`, risk-reducing only); reset, enforce↔monitor and restore are
  risk-raising, so they pass the PEP as `defender` commands (`/api/command`,
  any other principal gets G1D-101)
- audit → `events.jsonl` + `integrations/wazuh-rules.xml` feeding a SIEM

A red-teamer fires hostile commands; the defender responds; every decision is
logged.

## Why both sides share one gate

An attack launched from the `:4000` console and a malicious mapping returned by
the WMA model hit the **identical** `Twin.command()` policy check. Side 1 proves
the PEP works for real traffic; Side 2 proves it works against hostile traffic.
The security value is that the PEP does not care where the command came from.

## Ports

| Port | Role |
|---|---|
| 3000 | 3D twin, commands, tests, SIEM |
| 4000 | Red Team / Defense |
| 6001 | WMA adapter: `GET /healthz`, `GET /last`, `POST /cycle` |
| 6002 | Streamlit test harness |
| 6003 | Simulator facade: `GET /state`, `POST /command` |
| 60001–60003 | HTTPS/WebRTC cameras (head stereo, left/right wrist) |

## What it is NOT

Not a MuJoCo/Isaac physics simulator, not a controller for a real robot. The
WMA `observation` contract is provisional until validated against the real
`lab_server.py`. The lab endpoints have no real authentication: run on a
controlled lab network. The PEP simulates policy; it does not replace network
auth.
