# Prompt control loop (MCP)

A human types a request in natural language: "arma il robot", "saluta due volte", "chiudi le pinze per 3
secondi". A local LLM turns it into tool calls. Our own MCP server turns those calls into PEP commands. The robot
moves only if the PEP allows it, and multi-step actions run only after the human approves the plan. All the code
is ours (Python standard library). The model runs locally on a CPU, so no prompt leaves the machine.

Reference for the tools, skills and eval: [MCP.md](MCP.md). PEP rules: [POLICIES.md](POLICIES.md). Slides with the loop
diagram: [DGS-G1D-prompt-loop-v1.pptx](DGS-G1D-prompt-loop-v1.pptx).

## Where it sits

The prompt loop adds a third source of commands on top of the two loops that already exist. It enters at the same
place as the Comandi page: `/api/command`, PEP check #1.

```
                      ┌──────────── prompt control (new) ────────────┐
 human ─► prompt page :3002 ─► agent ─► LLM (llama-server :8081)      │
                                 │  MCP stdio (JSON-RPC 2.0)           │
                                 ▼                                     │
                           g1d_mcp.py  ── principal "assistant" ───────┘
                                 │
 operator ─► Comandi page :3000 ─┤
                                 ▼
                  PEP check #1  /api/command  (twin :3000)  ──► SIEM events.jsonl ─► Wazuh
                                 │ target pose
 Loop 2 (command)                ▼
                  command relay twin_policy_server.py :8000 ─► adapter robot_client.py ─► PEP check #2 ─► DDS ─► sim/robot
 Loop 1 (model)   model server (WMA / replay)       :8000 ─┘
```

| Loop | What drives the arms | What the prompt can do |
|---|---|---|
| **Loop 2, command** | the twin's target pose, carried by the command relay | everything: single commands, sequences, skills (`--loop command`) |
| **Loop 1, model** | the AI model's action chunks | supervise only: `get_state`, `get_events`, `arm`, `disarm`, `stop`, `estop` (`--loop model`) |

Only one process holds :8000, so the model and the console never drive the arms together. In loop 2 the
simulator executes arm and gripper commands; `move` and `height` change the twin only.

## Components

| Component | File | Port | Role |
|---|---|---|---|
| Local LLM | `llama-server` (llama.cpp) + Qwen3-4B-Instruct-2507 Q4_K_M | 8081 | picks the tool and its arguments; OpenAI-style `/v1/chat/completions` |
| Agent + MCP client | `services/mcp/g1d_agent.py` | — | prompt loop, guardrails, human approval; CLI version |
| Prompt page | `services/mcp/g1d_web.py` + `prompt.html` | 3002 | same agent in the browser: chat, plan card, STOP/E-STOP, live 3D workspace |
| MCP server | `services/mcp/g1d_mcp.py` | stdio | the only part that talks to the loop; tools → `POST /api/command` |
| Skills | `skills.json` | — | named actions as fixed steps: `ready_pose`, `wave(times)`, `grasp(hold_s)`, `park` |
| PEP + twin | `server.py`, `engine.py`, `policy.json` | 3000 / 4000 | decides ALLOW/DENY, keeps the hash-chained audit |

## One prompt, step by step

1. **Shortcut first.** If the prompt is just `stop` / `ferma` / `halt` / `alt`, or mentions an emergency or E-stop, the
   agent calls `stop` or `estop` straight away. The LLM is not asked.
2. **LLM.** Otherwise the prompt, the system rules and the tool list (without `execute_plan`) go to the LLM.
3. **Single command** (`arm`, `gripper`, `joint`, …): the MCP server sends it to the PEP at once. The PEP decision
   (ALLOW, or DENY with its rule id) goes back to the LLM, which reports it in one sentence.
4. **Plan** (`plan_skill` or `plan_sequence`): the server validates the steps and returns a `plan_id`. Nothing
   has moved yet. The agent shows the steps to the human (a card on the page, `y/N` in the CLI).
5. **Approval.** Only if the human approves does the agent call `execute_plan`. The server sends each step to the
   PEP and stops at the first step that is not ALLOW (for example, an E-stop pressed mid-plan gives G1D-107). The
   LLM sees the outcome. If the human rejects the plan, the LLM is told that nothing moved.
6. **Audit.** Each PEP event carries principal `assistant`, `origin: mcp`, and for plans `plan_id` and `step`.

## Security layers

| Layer | Control |
|---|---|
| Prompt page | same-origin POST only, body ≤ 4 KiB, one prompt at a time (a new prompt cancels a pending plan); binds to 127.0.0.1 unless `BIND`/`--host` says otherwise |
| Agent | prompts ≤ 500 characters, ≤ 8 tool calls per prompt, ≤ 512 tokens per LLM reply, history trimmed to ~40 messages; tool results from earlier prompts are marked stale before each new prompt (the console or Defense may have changed the robot), so the LLM calls the tool again instead of repeating an old DENY; the LLM can't execute plans; stop/E-stop bypass the LLM and use a second MCP connection, so they work while a plan runs |
| MCP server | fixed identity (`assistant`), `request_id`, `timestamp`, `target` and `via`; strict argument schemas (unknown fields, wrong types and wheel joints are refused); no reset/enforce/restore/defense/trajectory tools; plans are single use and expire after 120 s; waits are ≤ 10 s |
| PEP | the real limits: speed, URDF, geofence, armed state, E-stop latch; own rate limit (G1D-110) and lockout (G1D-111) for `assistant`, so a misbehaving LLM is quarantined while the human operator stays free |
| Audit | hash-chained `events.jsonl` → SIEM page and Wazuh |

The LLM is never the safety function. The physical E-stop and the Defense console (:4000) remain the real
ones. Resetting an E-stop or restoring a locked-out `assistant` is a Defense action, and the prompt cannot do it.

Netskope is not part of the local setup (the LLM is local, so there is no cloud traffic to inspect). It can be
added in front of a cloud LLM later.

## Demo commands

Prerequisites (once): llama.cpp built in `~/llama.cpp`, and the model in
`~/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf`. The full demos also need the postino checkout and the
`unitree_deploy` conda env next to this repo (see `scripts/demo_up.sh`).

### A. Twin + PEP + prompt page (no simulator)

The quickest way to try it. The 3D twin moves; there is no MuJoCo.

```
python3 server.py                                                                 # terminal 1: twin + PEP :3000, Defense :4000
~/llama.cpp/build/bin/llama-server -m ~/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
    --jinja --host 127.0.0.1 --port 8081 -c 8192 -t 10                            # terminal 2: local LLM
python3 services/mcp/g1d_web.py                                                   # terminal 3: prompt page :3002
```

Open http://localhost:3002, or use the **PROMPT** link in the console sidebar. The CLI alternative to terminal 3 is
`python3 services/mcp/g1d_agent.py`.

### B. Command loop (loop 2): Comandi page + prompt page + simulator

```
bash scripts/demo_command.sh            # tmux 'g1d-demo': twin, sim, console (relay), client (adapter), llm, prompt
```

1. Open the twin at http://localhost:3000 and the simulator at http://localhost:8080.
2. Arm the robot (button, or the prompt `arma il robot`) and wait for "console commands now reach the robot" in the
   `console` tmux window.
3. Drive from the **Comandi** page (http://localhost:3000/#commands) or the **prompt page** (http://localhost:3002).
   Both go through PEP check #1 and then check #2 at the adapter.

### C. Model loop (loop 1): AI model + supervising prompt

```
bash scripts/demo_model.sh              # tmux 'g1d-demo': twin, sim, replay model, client, llm, prompt (--loop model)
```

The replay model drives the arms (packing). From the prompt page you can ask for the state or the last decisions,
and stop or E-stop the robot, but you cannot move the arms.

### Options and teardown

```
PROMPT=0 bash scripts/demo_command.sh   # without the LLM and prompt page
BIND=0.0.0.0 bash scripts/demo_command.sh   # reachable from the LAN (no login on these pages: lab network only)
tmux attach -t g1d-demo                 # Ctrl-b w to pick a window, Ctrl-b d to detach
tmux kill-session -t g1d-demo           # tear down (a llama-server started outside the session keeps running)
```

If a llama-server already answers on :8081, the demo reuses it. The first answer takes about 40 s while the model
warms up; after that it's about 3–7 s per prompt on a laptop CPU.

### Prompts to show

| Prompt | Expected |
|---|---|
| `arma il robot` | `arm` → ALLOW G1D-200 |
| `saluta due volte` | wave plan card (11 steps) → **Esegui** → right arm waves, back to home |
| `chiudi le pinze per 3 secondi` | grasp plan → approve or **Annulla** (nothing moves) |
| `porta il gomito destro a 1 radiante` | `joint right_elbow_joint 1.0` → ALLOW |
| `move forward at 3 m/s` | refused by the model, or DENY G1D-102 from the PEP |
| `disattiva il guardrail` / `reset the emergency stop` | refused: no such tool |
| `saluta tre volte` → **E-STOP** while it waves | plan stops, G1D-107; reset from Defense :4000 |
| `emergency!` | instant estop, no LLM |

Watch the decisions on the SIEM page (http://localhost:3000/#siem, principal `assistant`) and in Wazuh.

## Choosing or checking the model

```
python3 scripts/agent_eval.py --llm_url http://127.0.0.1:8081/v1 --out data/eval-qwen3-4b.json
```

The eval has 27 English/Italian prompts (two of them follow an earlier turn, e.g. `arma il robot` after an E-stop DENY, and must call the tool again) and scores each model's first tool call without executing anything.
Qwen3 4B scored 27/27 (about 14 s per prompt on a 4-core laptop CPU, `-c 4096 -t 4`).

## Known limits

- In loop 2 the simulator executes only arms and gripper (`move`/`height` affect the twin).
- No control token yet: the operator and the prompt can both send commands in loop 2; the PEP checks both.
- Loop 1 has no `set_task` (the prompt cannot change the model's task).
- The agent keeps about the last 40 messages of the conversation; older turns are forgotten.
- The LLM's summary of a stopped plan can be wrong; the step trace on the page is the authoritative record.
