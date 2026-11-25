#!/bin/bash
# Command demo (loop 2): the operator drives the arms from the twin's Comandi page.
# Starts twin + PEP, simulator, command relay on :8000 and the adapter, in tmux session 'g1d-demo'.
# Each command is cleared twice by the PEP: at the page, then as a chunk at the adapter.
#
#   bash scripts/demo_command.sh
#   then: arm the robot, wait for "console commands now reach the robot", send from http://localhost:3000/#commands
#   or type prompts at http://localhost:3002 (local LLM via MCP, plans need your approval)
#   tmux kill-session -t g1d-demo      # tear down
#
# Same as `bash scripts/demo_up.sh command`; TWIN, SCRIPTS, BIND and PROMPT (0 = no LLM/prompt page) env overrides apply.
exec bash "$(dirname "$0")/demo_up.sh" command
