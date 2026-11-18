# PEP as robot guardrail — review against current standards

Scope: the PEP (`Twin.command()`, `policy.json`) as the single check every command passes before it can
reach the G1-D actuators, from two angles: **functional safety** (do not command a hazardous motion) and
**cybersecurity** (do not let an attacker command the robot). Reviewed 2026-11-18. Clause references are
indicative; verify against the purchased standard texts before quoting them in a deliverable.

## Reference framework

| Area | Reference | Status (Nov 2026) | What it asks of a command guardrail |
|---|---|---|---|
| Industrial robot safety | ISO 10218-1:2025 / ISO 10218-2:2025 (collaborative content formerly in ISO/TS 15066 now in Part 2) | published | speed/space limiting, single point of control, protective and emergency stop, **mandatory cybersecurity risk assessment** referring to IEC 62443 |
| Humanoid / actively-stable mobile robots | ISO 25785-1 (dynamically stable industrial mobile robots) | committee draft, not published | stability-aware stopping, mobile-base + manipulator limits; track, not citable yet |
| Mobile base | ISO 3691-4:2023 (driverless trucks) | published | speed limits, protected zones, safe stop |
| Emergency stop | ISO 13850:2015 | published | E-stop always available, overrides everything, reset must not restart motion |
| Safety-related control | ISO 13849-1:2023, IEC 62061:2021 | published | the PEP is *not* a rated safety function; hardware E-stop and robot safety controller remain primary |
| Industrial cybersecurity | IEC 62443-3-3 (system SRs), 62443-4-2 (component), 62443-3-2 (risk) | published | SR 1.1/1.2 identification & authentication, SR 1.11 unsuccessful attempts, SR 2.1 authorization, SR 3.1 communication integrity, SR 3.5 input validation, SR 3.9 audit protection, SR 6.2 continuous monitoring, SR 7.1 DoS protection |
| EU Machinery Regulation | (EU) 2023/1230, Annex III 1.1.9 and 1.2.1 | applies 20 Jan 2027 | protection against corruption; keep evidence of legitimate or illegitimate intervention; control system resists malicious third-party attempts; limits on autonomous behaviour |
| EU AI Act | (EU) 2024/1689, AI safety component of machinery = high-risk | Annex I obligations from Aug 2027 | Art. 12 logging, Art. 14 human oversight (stop), Art. 15 robustness and cybersecurity |
| EU Cyber Resilience Act | (EU) 2024/2847 | reporting Sep 2026, full Dec 2027 | secure by default, integrity of data and commands, logging of security events |

## Existing rules — assessment

| Rule | Covers | Verdict | Note |
|---|---|---|---|
| G1D-108 bypass | IEC 62443 SR 2.1; MR 1.2.1 | **partial** | only sees requests that *declare* `via != pep`. Real bypass (direct DDS publish) needs network segmentation, DDS Security or a robot-side check (lab checklist §C) |
| G1D-106 spoofing | SR 1.1/1.2 | **weak** | identity is self-declared; without authentication (mTLS or signed commands) it is a consistency check, not a control. Deferred until the final loop |
| G1D-101 unauthorized | SR 2.1, least privilege | OK | defender-only privileged actions added in 1.1.0 |
| G1D-104 escalation | SR 2.1 | OK | |
| G1D-103 scope | SR 3.5 | OK | |
| G1D-105 replay | SR 3.1 (freshness) | **improved** | 30 s was too long for motion; motion now must be ≤ 5 s old (needs NTP-synced clocks if the adapter runs on another host) |
| G1D-102 parameters | ISO 10218 speed limiting; SR 3.5 | OK, gaps | joint speed and URDF limits on arm chunks; no acceleration/jerk limit, no Cartesian (TCP) speed |
| G1D-107 sequence | ISO 13850 | OK | motion needs `arm`; E-stop latched; reset leaves the robot disarmed (no automatic restart) |
| G1D-109 geofence | ISO 10218 space limiting; ISO 3691-4 zones | OK for base | base only; arms have no workspace or keep-out limit |
| E-stop / revoke channel | ISO 13850; AI Act Art. 14 | OK | never blocked, never rate-limited |
| MONITOR mode | — | **risk** | lets a defender run unenforced. On the real robot use it only with the physical E-stop staffed (lab checklist §E) |

## Added in policy 1.3.0

| Rule / control | Standard driver | Behaviour |
|---|---|---|
| **G1D-110 rate** | IEC 62443 SR 7.1; MR 1.2.1 | > `max_per_s` (20) commands/s per principal → DENY. `stop`/`estop` exempt |
| **G1D-111 lockout** | IEC 62443 SR 1.11, SR 6.2 | 5 *security* violations in 60 s → principal revoked until defender `restore`. Safety denials don't count; defender exempt |
| **motion freshness** (G1D-105) | SR 3.1 | motion commands older than `motion_max_age_s` (5 s) → DENY |
| **audit hash chain** | MR Annex III 1.1.9 (evidence of intervention); SR 3.9; AI Act Art. 12 | every event carries `prev_hash`/`hash`; `python3 engine.py data/events.jsonl` verifies |
| **policy digest** | MR 1.1.9 (protect safety-relevant data) | every event carries `policy_sha256` of the effective policy |

Known side effect: lockout is itself a DoS lever while identities are unauthenticated. An attacker
who can send as `operator` can get the model adapter quarantined. The robot then holds, which is the
fail-safe outcome, but authentication is what closes this.

## Remaining gaps, by priority

1. **Command authentication and integrity** (SR 1.1/1.2/3.1, MR 1.2.1, CRA): mTLS between adapter,
   consoles and PEP, or HMAC-signed payloads. This turns G1D-106 and G1D-108 from checks into controls.
   Deferred by decision until the final loop is fixed.
2. **Non-bypassability on the real robot** (G1D-108, ISO 10218 cyber risk assessment): the adapter must
   be the only DDS writer. Use network isolation of the robot interface and DDS Security permissions.
3. **Arm workspace limits** (ISO 10218 space limiting): forward kinematics on each chunk point, with a
   Cartesian box and keep-out volumes (table, body, operator side), plus a TCP speed limit.
4. **Acceleration / jerk limits** (G1D-102): needs measured chunk statistics from the model in the lab
   before choosing values, or legitimate chunks get denied.
5. **Human presence → reduced limits** (ISO 10218-2:2025 collaborative operation): an input from a
   presence sensor or the operator switches the PEP to a reduced-speed policy.
6. **Single point of control** (ISO 10218-1): today the console can drive the base while the model drives
   the arms. A control token would let only one source command motion at a time.
7. **Stability-aware limits** (ISO 25785-1, draft): base acceleration and column height versus arm
   extension. Track the draft; the robot's own controller owns balance.
