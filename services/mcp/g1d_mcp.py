#!/usr/bin/env python3
"""G1-D MCP server: the one tool layer between an LLM and the command loop. Python standard library only.

MCP over stdio (newline-delimited JSON-RPC 2.0): initialize, ping, tools/list, tools/call.
Every motion tool becomes a POST /api/command to the twin, so each command passes the PEP (and, in loop 2,
a second time as a chunk at the adapter). The server fixes what the LLM must never choose: principal
('assistant'), request_id, timestamp, target and via. No privileged action (reset/enforce/restore), no
defense channel, no trajectory, no generic "send any command" tool.

  python3 services/mcp/g1d_mcp.py --twin_url http://127.0.0.1:3000 --loop command

--loop command (loop 2, relay on :8000): single commands, sequences and skills (skills.json).
--loop model   (loop 1, the model owns the arms): read tools plus arm/disarm/stop/estop only.
Sequences and skills are two-step: plan_* returns the expanded steps and a plan_id, execute_plan runs them.
The MCP client must show the plan to the human and call execute_plan only after they approve it.
"""
import argparse, json, sys, time, urllib.error, urllib.request, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from engine import Twin, load_policy  # noqa: E402  (same repo, stdlib only)

PROTOCOLS = ('2025-06-18', '2025-03-26', '2024-11-05')
PRINCIPAL = 'assistant'
PLAN_TTL_S, PLAN_MAX_STEPS, WAIT_MAX_S = 120, 32, 10
READ = {'readOnlyHint': True, 'openWorldHint': False}
MOTION = {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': True}


def obj(props=None, required=()):
    return {'type': 'object', 'properties': props or {}, 'required': list(required), 'additionalProperties': False}


def check(schema, value, where='arguments'):
    """Validate value against the small JSON-Schema subset used here; raise ValueError."""
    t = schema.get('type')
    if t == 'object':
        if not isinstance(value, dict): raise ValueError(f'{where}: object required')
        props = schema.get('properties', {})
        extra = set(value) - set(props)
        if extra and schema.get('additionalProperties') is False: raise ValueError(f'{where}: unknown field(s) {sorted(extra)}')
        for k in schema.get('required', []):
            if k not in value: raise ValueError(f'{where}: {k} is required')
        for k, v in value.items():
            if k in props: check(props[k], v, f'{where}.{k}')
    elif t in ('number', 'integer'):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (t == 'integer' and not isinstance(value, int)):
            raise ValueError(f'{where}: {t} required')
        if value != value or value in (float('inf'), float('-inf')): raise ValueError(f'{where}: finite number required')
        if 'minimum' in schema and value < schema['minimum']: raise ValueError(f"{where}: minimum {schema['minimum']}")
        if 'maximum' in schema and value > schema['maximum']: raise ValueError(f"{where}: maximum {schema['maximum']}")
    elif t == 'string' and not isinstance(value, str): raise ValueError(f'{where}: string required')
    elif t == 'array':
        if not isinstance(value, list) or not 1 <= len(value) <= schema.get('maxItems', PLAN_MAX_STEPS):
            raise ValueError(f"{where}: list of 1..{schema.get('maxItems', PLAN_MAX_STEPS)} items required")
        for i, v in enumerate(value): check(schema['items'], v, f'{where}[{i}]')
    if 'enum' in schema and value not in schema['enum']: raise ValueError(f"{where}: one of {schema['enum']}")


class TwinHTTP:
    def __init__(self, url): self.url = url.rstrip('/')

    def get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=5) as r: return json.load(r)

    def post(self, path, body):
        req = urllib.request.Request(self.url + path, json.dumps(body).encode(), {'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as r: return json.load(r)
        except urllib.error.HTTPError as e:  # 400 = the twin refused a malformed command
            raise ValueError(json.loads(e.read() or b'{}').get('error', f'HTTP {e.code}')) from None


class G1DServer:
    def __init__(self, twin, loop='command', skills_path=ROOT / 'skills.json', sleep=time.sleep):
        self.twin, self.loop, self.sleep, self.plans = twin, loop, sleep, {}
        rules = load_policy()['rules']['parameters']
        mv, hmax = rules['move'], rules['height_max']
        # Arm joints only: base motion goes through move, the column through height. Names come from the URDF.
        arm = sorted(j for j in Twin().joints if any(k in j for k in rules['trajectory']['joint_keys']))
        self.skills = json.loads(Path(skills_path).read_text())['skills']
        num = {'type': 'number'}
        # Shape only: the limits are quoted for the LLM but enforced by the PEP, so a violation is a logged DENY.
        self.step_schemas = {
            'arm': obj(), 'disarm': obj(), 'stop': obj(), 'estop': obj(), 'home': obj(),
            'move': obj({'v': num, 'w': num, 'duration': num}, ['v']),
            'height': obj({'value': num}, ['value']),
            'joint': obj({'name': {'type': 'string', 'enum': arm}, 'value': num}, ['name', 'value']),
            'gripper': obj({'state': {'type': 'string', 'enum': ['open', 'closed']}}, ['state']),
        }
        doc = {
            'arm': 'Arm the robot. Motion commands are refused until it is armed.',
            'disarm': 'Disarm the robot and stop the base.', 'stop': 'Stop the base (v=w=0).',
            'estop': 'Emergency stop (latched). Only the Defense console can reset it.',
            'home': 'Arms back to the start pose, grippers open, column down.',
            'move': f"Drive the base: v m/s (|v|<={mv['v_max']}), w rad/s (|w|<={mv['w_max']}), duration s (<= {mv['duration_max']}).",
            'height': f'Set the column height in m (0..{hmax}).',
            'joint': 'Set one arm joint angle in rad (URDF limits apply). Use small steps.',
            'gripper': 'Open or close both grippers.',
        }
        self.tools = {
            'get_state': (READ, obj(), 'Robot mode, base pose, column height, gripper, arm joints, guardrail mode.'),
            'get_events': (READ, obj({'n': {'type': 'integer', 'minimum': 1, 'maximum': 20}}),
                           'Last PEP decisions (ALLOW/DENY with rule id), newest last.'),
        }
        supervise = ('arm', 'disarm', 'stop', 'estop')
        for a, s in self.step_schemas.items():
            if loop == 'command' or a in supervise: self.tools[a] = (MOTION, s, doc[a])
        if loop == 'command':
            step = {'type': 'object'}  # each step is {action, ...args}; checked per action in plan()
            self.tools['plan_sequence'] = (READ, obj({'steps': {'type': 'array', 'items': step}}, ['steps']),
                'Plan several commands, e.g. [{"action":"arm"},{"action":"joint","name":"...","value":0.5},'
                '{"action":"wait","seconds":1}]. Returns the steps and a plan_id; nothing moves yet.')
            self.tools['plan_skill'] = (READ, obj({'name': {'type': 'string', 'enum': sorted(self.skills)},
                                                   'params': {'type': 'object', 'properties': {}, 'additionalProperties': True}}, ['name']),
                'Plan a named action. ' + ' '.join(f"{k}: {v['description']} params {list(v['params']) or 'none'}."
                                                   for k, v in sorted(self.skills.items())))
            self.tools['execute_plan'] = (MOTION, obj({'plan_id': {'type': 'string'}}, ['plan_id']),
                'Run a planned sequence or skill after the human approved it. Stops at the first refused step.')

    # ---- MCP / JSON-RPC ----
    def handle(self, msg):
        """One JSON-RPC message in, the response dict out (None for notifications)."""
        if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or not isinstance(msg.get('method'), str):
            return {'jsonrpc': '2.0', 'id': msg.get('id') if isinstance(msg, dict) else None,
                    'error': {'code': -32600, 'message': 'Invalid request'}}
        if 'id' not in msg: return None  # notifications/initialized, cancelled, ...
        mid, method, params = msg['id'], msg['method'], msg.get('params') or {}
        try:
            if method == 'initialize':
                v = params.get('protocolVersion')
                result = {'protocolVersion': v if v in PROTOCOLS else PROTOCOLS[0],
                          'capabilities': {'tools': {'listChanged': False}},
                          'serverInfo': {'name': 'g1d-cybertwin', 'version': '0.1.0'},
                          'instructions': 'Robot commands pass the PEP. A DENY is final: report it, do not retry '
                                          'it in another form. For several steps use plan_sequence or plan_skill, '
                                          'show the plan to the human, then execute_plan.'}
            elif method == 'ping': result = {}
            elif method == 'tools/list':
                result = {'tools': [{'name': n, 'description': d, 'inputSchema': s, 'annotations': a}
                                    for n, (a, s, d) in self.tools.items()]}
            elif method == 'tools/call':
                name = params.get('name')
                if name not in self.tools:
                    return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32602, 'message': f'Unknown tool: {name}'}}
                result = self.call(name, params.get('arguments') or {})
            else:
                return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': f'Method not found: {method}'}}
        except Exception as e:  # never crash the stdio loop
            return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32603, 'message': str(e)}}
        return {'jsonrpc': '2.0', 'id': mid, 'result': result}

    def call(self, name, args):
        """Run a tool. Bad arguments and twin errors come back as isError results the LLM can read."""
        try:
            check(self.tools[name][1], args)
            out = getattr(self, 't_' + name, None)
            out = out(**args) if out else self.command(name, args)
            err = False
        except (ValueError, OSError) as e:
            out, err = {'error': str(e)}, True
        return {'content': [{'type': 'text', 'text': json.dumps(out, ensure_ascii=False)}],
                'structuredContent': out, 'isError': err}

    # ---- tools ----
    def command(self, action, args, **audit):
        payload = {'action': action, 'params': args, 'request_id': str(uuid.uuid4()), 'timestamp': time.time(),
                   'identity': PRINCIPAL, 'target': 'g1d-01', 'via': 'pep', 'origin': 'mcp', **audit}
        ev = self.twin.post('/api/command', {'payload': payload, 'principal': PRINCIPAL})
        return {'decision': ev['decision'], 'rule_id': ev['rule_id'], 'message': ev['message'][:200]}

    def t_get_state(self):
        s = self.twin.get('/api/state'); r = s['robot']
        arm = {k: round(v, 3) for k, v in r['joints'].items() if any(x in k for x in ('shoulder', 'elbow', 'wrist'))}
        return {'mode': r['mode'], 'x': round(r['x'], 3), 'y': round(r['y'], 3), 'yaw': round(r['yaw'], 3),
                'v': r['v'], 'w': r['w'], 'height': round(r['height'], 3), 'gripper': r['gripper'], 'arm_joints': arm,
                'enforce': s['enforce'], 'blocked': s['blocked'], 'policy_version': s['policy_version']}

    def t_get_events(self, n=10):
        # Structured fields only, message truncated: event text is data, never instructions for the LLM.
        return {'events': [{'time': e['timestamp'], 'decision': e['decision'], 'rule_id': e['rule_id'],
                            'action': (e.get('request') or {}).get('action'),
                            'principal': (e.get('request') or {}).get('identity'), 'message': e['message'][:120]}
                           for e in self.twin.get('/api/state')['events'][-n:]]}

    def plan(self, steps, source):
        flat = []
        for i, st in enumerate(steps):
            if not isinstance(st, dict) or not isinstance(st.get('action'), str): raise ValueError(f'step {i}: action required')
            a, args = st['action'], {k: v for k, v in st.items() if k != 'action'}
            if a == 'wait': check(obj({'seconds': {'type': 'number', 'minimum': 0, 'maximum': WAIT_MAX_S}}, ['seconds']), args, f'step {i}')
            elif a in self.step_schemas: check(self.step_schemas[a], args, f'step {i}')
            else: raise ValueError(f'step {i}: action {a!r} not allowed')
            flat.append({'action': a, **args})
        if len(flat) > PLAN_MAX_STEPS: raise ValueError(f'plan longer than {PLAN_MAX_STEPS} steps')
        now = time.monotonic()
        self.plans = {k: p for k, p in self.plans.items() if p['expires'] > now}
        pid = uuid.uuid4().hex[:12]
        self.plans[pid] = {'steps': flat, 'source': source, 'expires': now + PLAN_TTL_S}
        return {'plan_id': pid, 'source': source, 'steps': flat, 'expires_in_s': PLAN_TTL_S,
                'note': 'Nothing has moved. Show these steps to the human; call execute_plan only if approved.'}

    def t_plan_sequence(self, steps): return self.plan(steps, 'sequence')

    def t_plan_skill(self, name, params=None):
        sk, params = self.skills[name], dict(params or {})
        spec = {k: {kk: vv for kk, vv in v.items() if kk != 'default'} for k, v in sk['params'].items()}
        check(obj(spec), params, 'params')
        vals = {k: params.get(k, v.get('default')) for k, v in sk['params'].items()}
        def sub(x): return vals[x[1:]] if isinstance(x, str) and x.startswith('$') else x
        def expand(steps):
            for st in steps:
                if 'repeat' in st:
                    for _ in range(sub(st['repeat'])): yield from expand(st['steps'])
                else: yield {k: sub(v) for k, v in st.items()}
        return self.plan(list(expand(sk['steps'])), f'skill:{name}')

    def t_execute_plan(self, plan_id):
        p = self.plans.pop(plan_id, None)  # single use
        if not p or p['expires'] < time.monotonic(): raise ValueError('unknown or expired plan_id: plan again')
        done = []
        for i, st in enumerate(p['steps']):
            a, args = st['action'], {k: v for k, v in st.items() if k != 'action'}
            if a == 'wait':
                self.sleep(args['seconds']); done.append({'step': i, 'action': 'wait', 'decision': 'DONE'}); continue
            r = self.command(a, args, plan_id=plan_id, step=i)
            done.append({'step': i, 'action': a, **r})
            if r['decision'] != 'ALLOW':
                return {'completed': False, 'stopped_at': i, 'source': p['source'], 'steps': done}
        return {'completed': True, 'source': p['source'], 'steps': done}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--twin_url', default='http://127.0.0.1:3000')
    ap.add_argument('--loop', choices=('command', 'model'), default='command')
    ap.add_argument('--skills', default=str(ROOT / 'skills.json'))
    a = ap.parse_args()
    srv = G1DServer(TwinHTTP(a.twin_url), a.loop, a.skills)
    print(f'g1d-mcp: loop={a.loop} twin={a.twin_url} tools={len(srv.tools)}', file=sys.stderr, flush=True)
    for line in sys.stdin:  # stdout carries MCP messages only; logs go to stderr
        if not line.strip(): continue
        try: msg = json.loads(line)
        except json.JSONDecodeError:
            out = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        else: out = srv.handle(msg)
        if out is not None: print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
