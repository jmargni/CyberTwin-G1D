import json, subprocess, sys, unittest
from pathlib import Path
from engine import Twin
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services' / 'mcp'))
import g1d_mcp

class LocalTwin:
    """The twin's two HTTP endpoints, called in-process (same behaviour as server.py)."""
    def __init__(self): self.t = Twin()
    def get(self, path): return self.t.snapshot()
    def post(self, path, body): return self.t.command(body['payload'], body['principal'])

def rpc(srv, method, params=None, mid=1): return srv.handle({'jsonrpc': '2.0', 'id': mid, 'method': method, 'params': params or {}})
def call(srv, tool, **args): return rpc(srv, 'tools/call', {'name': tool, 'arguments': args})['result']

class McpTests(unittest.TestCase):
    def setUp(self):
        self.tw = LocalTwin(); self.waits = []
        self.s = g1d_mcp.G1DServer(self.tw, sleep=self.waits.append)

    def test_handshake_and_protocol_errors(self):
        r = rpc(self.s, 'initialize', {'protocolVersion': '2025-06-18'})['result']
        self.assertEqual(r['protocolVersion'], '2025-06-18'); self.assertIn('tools', r['capabilities'])
        self.assertEqual(rpc(self.s, 'initialize', {'protocolVersion': '1999-01-01'})['result']['protocolVersion'], g1d_mcp.PROTOCOLS[0])
        self.assertIsNone(self.s.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))
        self.assertEqual(rpc(self.s, 'nope')['error']['code'], -32601)
        self.assertEqual(rpc(self.s, 'tools/call', {'name': 'reset'})['error']['code'], -32602)

    def test_tool_sets_depend_on_the_loop(self):
        names = {t['name'] for t in rpc(self.s, 'tools/list')['result']['tools']}
        self.assertTrue({'joint', 'gripper', 'plan_skill', 'execute_plan'} <= names)
        self.assertFalse(names & {'reset', 'enforce', 'restore', 'trajectory', 'revoke', 'command'})  # never exposed
        model = g1d_mcp.G1DServer(self.tw, loop='model')
        self.assertEqual(set(model.tools), {'get_state', 'get_events', 'arm', 'disarm', 'stop', 'estop'})

    def test_command_goes_through_pep_as_assistant(self):
        self.assertEqual(call(self.s, 'arm')['structuredContent']['decision'], 'ALLOW')
        r = call(self.s, 'joint', name='right_elbow_joint', value=1.0)['structuredContent']
        self.assertEqual(r['decision'], 'ALLOW'); self.assertEqual(self.tw.t.robot['joints']['right_elbow_joint'], 1.0)
        req = self.tw.t.events[-1]['request']
        self.assertEqual((req['identity'], req['origin'], req['via']), ('assistant', 'mcp', 'pep'))

    def test_llm_cannot_choose_principal_or_non_arm_joint(self):
        self.assertTrue(call(self.s, 'arm', principal='defender')['isError'])
        self.assertTrue(call(self.s, 'joint', name='Left_Wheel_Joint', value=1)['isError'])
        self.assertTrue(call(self.s, 'move', v=True)['isError'])

    def test_limits_are_decided_and_logged_by_the_pep(self):
        call(self.s, 'arm'); r = call(self.s, 'move', v=5)
        self.assertFalse(r['isError']); self.assertEqual(r['structuredContent']['rule_id'], 'G1D-102')
        self.assertEqual(self.tw.t.events[-1]['decision'], 'DENY')

    def test_plan_executes_once_and_stops_at_first_deny(self):
        n = len(self.tw.t.events)
        p = call(self.s, 'plan_sequence', steps=[{'action': 'arm'}, {'action': 'move', 'v': 5}, {'action': 'home'}])['structuredContent']
        self.assertEqual(len(self.tw.t.events), n)  # planning sends nothing to the PEP
        r = call(self.s, 'execute_plan', plan_id=p['plan_id'])['structuredContent']
        self.assertEqual((r['completed'], r['stopped_at']), (False, 1)); self.assertEqual(len(r['steps']), 2)
        self.assertEqual(self.tw.t.events[-1]['request']['plan_id'], p['plan_id'])
        self.assertTrue(call(self.s, 'execute_plan', plan_id=p['plan_id'])['isError'])  # single use
        self.assertTrue(call(self.s, 'plan_sequence', steps=[{'action': 'reset'}])['isError'])
        self.assertTrue(call(self.s, 'plan_sequence', steps=[{'action': 'wait', 'seconds': 60}])['isError'])

    def test_skills_expand_from_skills_json(self):
        call(self.s, 'arm')
        p = call(self.s, 'plan_skill', name='wave', params={'times': 3})['structuredContent']
        self.assertEqual(sum(s['action'] == 'joint' for s in p['steps']), 1 + 2 * 3)
        r = call(self.s, 'execute_plan', plan_id=p['plan_id'])['structuredContent']
        self.assertTrue(r['completed']); self.assertEqual(len(self.waits), 1 + 2 * 3)
        self.assertTrue(call(self.s, 'plan_skill', name='wave', params={'times': 9})['isError'])
        self.assertTrue(call(self.s, 'plan_skill', name='wave', params={'speed': 1})['isError'])
        self.assertEqual(call(self.s, 'plan_skill', name='grasp')['structuredContent']['steps'][1], {'action': 'wait', 'seconds': 2})

    def test_estop_stops_a_running_skill(self):
        call(self.s, 'arm'); p = call(self.s, 'plan_skill', name='wave')['structuredContent']
        self.s.sleep = lambda s: self.tw.t.defense('estop')  # E-stop pressed during the first wait
        r = call(self.s, 'execute_plan', plan_id=p['plan_id'])['structuredContent']
        self.assertFalse(r['completed']); self.assertEqual(r['steps'][-1]['rule_id'], 'G1D-107')

    def test_state_and_events_are_compact(self):
        call(self.s, 'arm'); st = call(self.s, 'get_state')['structuredContent']
        self.assertEqual(st['mode'], 'armed'); self.assertNotIn('Left_Wheel_Joint', st['arm_joints'])
        ev = call(self.s, 'get_events', n=1)['structuredContent']['events']
        self.assertEqual((ev[0]['decision'], ev[0]['principal']), ('ALLOW', 'assistant'))

    def test_stdio_transport(self):
        msgs = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, 'not json',
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
        inp = '\n'.join(m if isinstance(m, str) else json.dumps(m) for m in msgs) + '\n'
        out = subprocess.run([sys.executable, str(Path(g1d_mcp.__file__))], input=inp, capture_output=True, text=True, timeout=30)
        lines = [json.loads(x) for x in out.stdout.splitlines()]
        self.assertEqual([x.get('id') for x in lines], [1, None, 2])
        self.assertEqual(lines[1]['error']['code'], -32700); self.assertTrue(lines[2]['result']['tools'])

if __name__ == '__main__': unittest.main()
