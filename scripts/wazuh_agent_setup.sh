#!/bin/bash
# Wire the G1-D PEP event log into the local Wazuh AGENT (agent -> remote manager).
# Run with sudo on the machine where the PEP (twin) runs. The detection RULES go on
# the MANAGER separately (see integrations/wazuh-rules.xml / docs/DEMO_WAZUH.md).
#   sudo bash scripts/wazuh_agent_setup.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
EVENTS="$REPO/data/events.jsonl"
OSSEC_CONF="/var/ossec/etc/ossec.conf"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
[ -f "$OSSEC_CONF" ] || { echo "no $OSSEC_CONF - is the Wazuh agent installed here?"; exit 1; }

# Point the agent's logcollector at the twin's event log (idempotent).
if grep -qF "$EVENTS" "$OSSEC_CONF"; then
  echo "localfile already present, skipping"
else
  cp "$OSSEC_CONF" "$OSSEC_CONF.g1d.bak"
  awk -v loc="$EVENTS" '
    /<\/ossec_config>/ && !done {
      print "  <localfile>";
      print "    <log_format>json</log_format>";
      print "    <location>" loc "</location>";
      print "  </localfile>";
      done=1
    } {print}
  ' "$OSSEC_CONF.g1d.bak" > "$OSSEC_CONF"
  echo "localfile -> $EVENTS (backup: $OSSEC_CONF.g1d.bak)"
fi

# The agent's logcollector runs as root and must descend to the file. If $HOME is 0750
# and the agent runs as the 'wazuh' user, grant traversal: chmod o+x on each parent dir.
systemctl restart wazuh-agent
echo "wazuh-agent restarted; new G1-D events will ship to the manager."
echo
echo "NEXT, on the MANAGER (other server) - without this, events arrive but no alerts fire:"
echo "  install -o wazuh -g wazuh -m 660 g1d_rules.xml /var/ossec/etc/rules/g1d_rules.xml"
echo "  systemctl restart wazuh-manager"
