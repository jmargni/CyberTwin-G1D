#!/bin/bash
# Model demo (loop 1): the AI model drives the arms (packing), every chunk cleared by the PEP.
# Starts twin + PEP, simulator, model server on :8000 and the adapter, in tmux session 'g1d-demo'.
#
#   bash scripts/demo_model.sh
#   tmux kill-session -t g1d-demo      # tear down
#
# Same as `bash scripts/demo_up.sh` (packing mode); TWIN, SCRIPTS, BIND and PROMPT (0 = no LLM/prompt page) env overrides apply.
exec bash "$(dirname "$0")/demo_up.sh" packing
