#!/bin/bash
# Launch the whole PEP demo stack in a tmux session: twin, simulator, replay
# server, client, and the prompt control (local LLM + prompt page :3002). Attach to watch; Wazuh is separate
# (needs sudo, see below).
#
#   bash scripts/demo_up.sh            # packing demo (replay model) + attach
#   bash scripts/demo_up.sh command    # twin-console demo (arms from the Comandi page, via postino)
#   tmux kill-session -t g1d-demo      # tear down
#
# Both modes share twin, sim and client; only the port-8000 "model" differs, so the model and the
# console never drive the arms together. Paths auto-detect (TWIN = this repo, SCRIPTS = the sibling
# unifolm-* postino checkout); override TWIN / SCRIPTS (env) if your layout differs.
set -euo pipefail

MODE="${1:-packing}"
SESSION=g1d-demo
TWIN="${TWIN:-$(cd "$(dirname "$0")/.." && pwd)}"              # this repo (scripts/..)
if [ -z "${SCRIPTS:-}" ]; then                                # first sibling postino checkout
  for d in "$TWIN"/../*/unitree_deploy/scripts; do
    [ -f "$d/sim_g1_robot.py" ] && SCRIPTS="$(cd "$d" && pwd)" && break
  done
fi
[ -f "$TWIN/server.py" ] || { echo "twin server.py not under TWIN=$TWIN"; exit 1; }
[ -n "${SCRIPTS:-}" ] && [ -f "$SCRIPTS/sim_g1_robot.py" ] || { echo "postino scripts not found; set SCRIPTS=..."; exit 1; }
HEADLESS=""; [ -z "${DISPLAY:-}" ] && HEADLESS=" --headless"   # no X display -> web view only (:8080)
BIND="${BIND:-127.0.0.1}"   # set BIND=0.0.0.0 to reach twin/SIEM/sim views from another PC (exposes them on the LAN)
# Bake the absolute conda path into every window (a tmux window's fresh shell may not have conda on PATH).
SRC="source $(conda info --base)/etc/profile.d/conda.sh"
ACT="$SRC && conda activate unitree_deploy && cd $SCRIPTS"

# Prompt control (docs/MCP-LOOP.md): local LLM on :8081 + prompt page on :3002. PROMPT=0 leaves it out.
PROMPT="${PROMPT:-1}"
LLAMA="${LLAMA:-$HOME/llama.cpp/build/bin/llama-server}"
LLM_MODEL="${LLM_MODEL:-$HOME/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf}"

case "$MODE" in
  packing) MODEL_WIN=replay;  LOOP=model;   MODEL_CMD="python replay_policy_server.py --cover --loop";;
  command) MODEL_WIN=console; LOOP=command; MODEL_CMD="python twin_policy_server.py --twin_url http://127.0.0.1:3000";;
  *) echo "usage: $0 [packing|command]"; exit 1;;
esac

tmux has-session -t $SESSION 2>/dev/null && { echo "session '$SESSION' already up — attach: tmux attach -t $SESSION"; exit 0; }

# 1. twin (stdlib only, base python) — writes events.jsonl that Wazuh tails
tmux new-session  -d -s $SESSION -n twin   "$SRC && conda activate base && cd $TWIN && python server.py --host $BIND; read"
# 2. simulator (MuJoCo + web view on :8080)
tmux new-window      -t $SESSION -n sim    "$ACT && python sim_g1_robot.py --robot g1d --auto_reset --web_port 8080 --web_host $BIND$HEADLESS --twin_url http://127.0.0.1:3000; read"
# 3. port-8000 "model": replay server (packing) or twin_policy_server (console commands), no GPU
tmux new-window      -t $SESSION -n $MODEL_WIN "$ACT && $MODEL_CMD; read"
# 4. client — model driving the robot through the PEP (give the others a head start)
tmux new-window      -t $SESSION -n client "$ACT && sleep 6 && UNITREE_IMAGE_SERVER=127.0.0.1 python robot_client.py --control_freq 15 --pep_url http://127.0.0.1:3000; read"

# 5-6. prompt control: llama-server (skipped if one already answers on :8081) and the prompt page
if [ "$PROMPT" = 1 ]; then
  if curl -sf -m 2 http://127.0.0.1:8081/health >/dev/null; then echo "llama-server already up on :8081, reusing it"
  elif [ -x "$LLAMA" ] && [ -f "$LLM_MODEL" ]; then
    tmux new-window -t $SESSION -n llm "$LLAMA -m $LLM_MODEL --jinja --host 127.0.0.1 --port 8081 -c 8192 -t 10; read"
  else echo "no llama-server/model (LLAMA=$LLAMA LLM_MODEL=$LLM_MODEL): the prompt page will answer with an LLM error"; fi
  tmux new-window -t $SESSION -n prompt "$SRC && conda activate base && cd $TWIN && sleep 3 && python services/mcp/g1d_web.py --host $BIND --loop $LOOP; read"
fi

cat <<EOF
stack up in tmux session '$SESSION' (mode: $MODE). Windows: twin, sim, $MODEL_WIN, client$( [ "$PROMPT" = 1 ] && echo ', [llm], prompt')
  switch windows:  Ctrl-b <n>   (0=twin 1=sim 2=$MODEL_WIN 3=client, then llm/prompt)  or Ctrl-b w
  detach:          Ctrl-b d
  tear down:       tmux kill-session -t $SESSION

browser:  robot http://localhost:8080   twin http://localhost:3000  (click "Arma robot")
$( [ "$MODE" = command ] && echo 'console :  arm the robot, wait for "console commands now reach the robot" in the '"$MODEL_WIN"' window, then send from http://localhost:3000/#commands' )
$( [ "$PROMPT" = 1 ] && echo "prompt :  http://localhost:3002  (loop $LOOP; first answer ~40 s while the model warms up)" )
wazuh  :  https://localhost   OR  sudo tail -f /var/ossec/logs/alerts/alerts.json | jq .
EOF

if [ -t 1 ]; then tmux attach -t $SESSION; else echo "(no tty) attach with: tmux attach -t $SESSION"; fi
