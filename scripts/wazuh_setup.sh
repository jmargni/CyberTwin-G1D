#!/bin/bash
# Wire the G1-D cyber-twin into a local Wazuh manager. Run with sudo.
#   sudo bash scripts/wazuh_setup.sh
set -euo pipefail

REPO="/home/jmargni/projects/DGS-CyberTwin-G1D"
EVENTS="$REPO/data/events.jsonl"
RULES_SRC="$REPO/integrations/wazuh-rules.xml"
RULES_DST="/var/ossec/etc/rules/g1d_rules.xml"
OSSEC_CONF="/var/ossec/etc/ossec.conf"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

# Step 2: install rules
install -o wazuh -g wazuh -m 660 "$RULES_SRC" "$RULES_DST"
echo "rules -> $RULES_DST"

# Step 3: point Wazuh at the twin's event log (idempotent)
if grep -qF "$EVENTS" "$OSSEC_CONF"; then
  echo "localfile already present, skipping"
else
  cp "$OSSEC_CONF" "$OSSEC_CONF.g1d.bak"
  # insert a localfile block before the final </ossec_config>
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

# Step 4: restart + smoke-test a real DENY line against the rules
systemctl restart wazuh-manager
echo "wazuh-manager restarted"
echo "=== logtest: feeding a real G1D-107 DENY line ==="
grep -m1 'G1D-107' "$EVENTS" | /var/ossec/bin/wazuh-logtest 2>&1 | grep -iE "rule id|level|description" || \
  echo "(no match — check /var/ossec/logs/ossec.log for rule-load errors)"
