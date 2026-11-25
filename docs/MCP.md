# Prompt control via MCP

`services/mcp/g1d_mcp.py` is an MCP server (stdio, JSON-RPC 2.0, Python standard library only) that lets an
LLM command the G1-D through the PEP. It is the only part that talks to the loop: every motion tool is a
`POST /api/command` as principal `assistant`, so it gets the same checks as the Comandi page (and, in loop 2,
a second check as a chunk at the adapter).

```
human ─► MCP client + LLM ─► g1d_mcp.py (stdio) ─► PEP /api/command ─► twin ─► relay :8000 ─► adapter ─► PEP ─► sim/robot
```

## Run

The MCP client starts the server as a subprocess:

```
python3 services/mcp/g1d_mcp.py --twin_url http://127.0.0.1:3000 --loop command   # loop 2
python3 services/mcp/g1d_mcp.py --loop model                                      # loop 1: supervise only
```

Test it by hand (one JSON-RPC message per line):

```
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_state","arguments":{}}}' \
  | python3 services/mcp/g1d_mcp.py
```

## Tools

| Tool | Loop 2 | Loop 1 | What it does |
|---|---|---|---|
| `get_state`, `get_events` | ✓ | ✓ | read only: robot state, last PEP decisions (structured, messages truncated) |
| `arm`, `disarm`, `stop`, `estop` | ✓ | ✓ | single PEP commands |
| `move`, `height`, `joint`, `gripper`, `home` | ✓ | — | single PEP commands; `joint` accepts arm joints only |
| `plan_sequence(steps)` | ✓ | — | checks a list of commands (plus `wait`, ≤ 10 s) and returns a `plan_id`; nothing moves |
| `plan_skill(name, params)` | ✓ | — | expands a named action from `skills.json` into steps and returns a `plan_id` |
| `execute_plan(plan_id)` | ✓ | — | runs the plan step by step; stops at the first step that is not ALLOW |

In loop 2 the simulator executes arm and gripper commands (the relay carries only those); `move` and
`height` act on the twin.

## Safety rules built into the server

- **The LLM never picks identity or routing.** Principal `assistant`, `request_id`, `timestamp`, `target` and
  `via` are set by the server. Extra arguments (e.g. `principal`) are rejected.
- **No privileged or bypass tools.** No `reset`/`enforce`/`restore`, no defense channel, no `trajectory`,
  no generic "send any command" tool.
- **The PEP decides the limits.** Tool schemas check shape and types only; speed, URDF and geofence limits are
  quoted in the descriptions but enforced by the PEP, so a violation is a logged DENY (G1D-102, 109, …).
- **Plans need the human.** `plan_*` moves nothing. The MCP client must show the steps and call
  `execute_plan` only after the human approves. A `plan_id` is single use and expires after 120 s.
- **Stop on the first refusal.** A DENY (or MONITOR, or an E-stop pressed mid-plan, G1D-107) ends the plan.
- **Own counters.** `assistant` has its own rate limit (G1D-110) and lockout (G1D-111): a misbehaving LLM is
  quarantined, the human operator is not.
- **Audit.** Each event carries `origin: mcp`, and for plans `plan_id` and `step`, inside the hash-chained log.
- **Not an emergency stop.** `estop` depends on the LLM. The physical E-stop and the Defense console stay the
  real ones.

## Skills

`skills.json` holds named actions as fixed steps, like `policy.json` holds rule parameters. Each skill has a
description, typed parameters with bounds and defaults, and `steps` (`{"action": ..., args}`, `wait`, and
`{"repeat": "$param", "steps": [...]}`). `$name` values are substituted from the parameters. Shipped:
`ready_pose`, `wave(times)`, `grasp(hold_s)`, `park`.

Not in yet: the MCP client and local LLM (next step), loop-1 `set_task`, a control token so only one source
drives motion at a time.
