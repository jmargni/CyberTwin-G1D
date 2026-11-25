#!/bin/bash
# Smoke-test the PEP guard set against a running twin. Re-verify everything before a demo.
#   bash scripts/smoke.sh            # against http://localhost:3000
#   URL=http://localhost:4000 bash scripts/smoke.sh
set -u
URL=${URL:-http://localhost:3000}
REPO="$(cd "$(dirname "$0")/.." && pwd)"
EVENTS="$REPO/data/events.jsonl"
fails=0
cmd(){ curl -s -XPOST "$URL/api/command" -H 'Content-Type: application/json' -d "$1"; }
ok(){ printf '  \033[32mPASS\033[0m %s\n' "$1"; }
no(){ printf '  \033[31mFAIL\033[0m %s — %s\n' "$1" "$2"; fails=$((fails+1)); }

command -v jq >/dev/null || { echo "jq required"; exit 2; }
echo "PEP smoke test → $URL"

# 1. twin reachable
state=$(curl -s "$URL/api/state") || true
[ -n "$state" ] && echo "$state" | jq -e .robot >/dev/null 2>&1 \
  && ok "twin reachable" || { no "twin reachable" "no /api/state"; echo "stack down? bash scripts/demo_up.sh"; exit 1; }

# 2. external policy loaded (not the builtin default)
pv=$(echo "$state" | jq -r '.policy_version // "null"')
[ "$pv" = "1.4.0" ] && ok "policy.json loaded (v$pv)" || no "policy version" "got '$pv', expected 1.4.0"

# 3. full guard regression suite (12/12 incl. geofence, flooding, lockout)
suite=$(curl -s -XPOST "$URL/api/tests" -H 'Content-Type: application/json' -d '{}')
read -r pass total < <(echo "$suite" | jq -r '[.[].passed] | "\(map(select(.))|length) \(length)"')
[ "$pass" = "$total" ] && [ "$total" -ge 10 ] \
  && ok "regression suite ${pass}/${total}" \
  || { no "regression suite" "${pass}/${total}"; echo "$suite" | jq -r '.[]|select(.passed|not)|"      FAILED \(.scenario): expected \(.expected) got \(.actual)"'; }

# 4. geofence guard: armed base move out of the fence -> DENY G1D-109
cmd '{"payload":{"action":"arm"}}' >/dev/null
gf=$(cmd '{"payload":{"action":"move","params":{"v":0.5,"w":0,"duration":5}}}')
[ "$(echo "$gf" | jq -r .decision)" = DENY ] && [ "$(echo "$gf" | jq -r .rule_id)" = G1D-109 ] \
  && ok "geofence blocks out-of-bounds move (G1D-109)" \
  || no "geofence guard" "$(echo "$gf" | jq -rc '{decision,rule_id,message}')"

# 5. allowed path: a small in-bounds move -> ALLOW G1D-200
al=$(cmd '{"payload":{"action":"move","params":{"v":0.2,"w":0,"duration":1}}}')
[ "$(echo "$al" | jq -r .decision)" = ALLOW ] && [ "$(echo "$al" | jq -r .rule_id)" = G1D-200 ] \
  && ok "in-bounds move allowed (G1D-200)" \
  || no "allowed move" "$(echo "$al" | jq -rc '{decision,rule_id}')"

# 6. enriched audit trail (RFI 2.8-C): correlation fields present and non-null in the log
if [ -f "$EVENTS" ]; then
  miss=$(tail -1 "$EVENTS" | jq -r '[ "session_id","safety_state","human_override","policy_version","model_version" ] - [ to_entries[]|select(.value!=null)|.key ] | join(",")')
  [ -z "$miss" ] && ok "audit trail enriched (session_id, safety_state, versions)" \
    || no "audit fields" "missing/null: $miss"
else
  no "events.jsonl" "not found at $EVENTS"
fi

# leave the twin disarmed and reset
curl -s -XPOST "$URL/api/command" -H 'Content-Type: application/json' -d '{"payload":{"action":"reset"},"principal":"defender"}' >/dev/null

echo
[ "$fails" -eq 0 ] && { echo -e "\033[32mall guards OK\033[0m"; exit 0; } || { echo -e "\033[31m$fails check(s) failed\033[0m"; exit 1; }
