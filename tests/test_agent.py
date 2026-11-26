import json, os, socket, subprocess, sys, tempfile, time, unittest, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services' / 'mcp'))
import g1d_agent, g1d_web

def free_port():
    with socket.socket() as s: s.bind(('127.0.0.1', 0)); return s.getsockname()[1]

def tc(name, **args): return {'id': name, 'function': {'name': name, 'arguments': json.dumps(args)}}

class ScriptedLLM:
    """Fake LLM: each step is a list of tool calls, a text reply, or a function of the last tool result."""
    def __init__(self, *steps): self.steps, self.calls = list(steps), 0
    def __call__(self, messages, tools):
        self.calls += 1; step = self.steps.pop(0)
        if callable(step): step = step(json.loads(messages[-1]['content']))
        return {'content': step} if isinstance(step, str) else {'content': '', 'tool_calls': step}

class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); p = free_port(); cls.url = f'http://127.0.0.1:{p}'
        pol = Path(cls.tmp.name) / 'policy.json'  # the tests share one twin: lift G1D-110, which test_engine covers
        pol.write_text(json.dumps({'version': 'test', 'rules': {'rate': {'max_per_s': 10000}}}))
        cls.twin = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--port-3d', str(p), '--port-security', str(free_port()),
                                     '--data', cls.tmp.name], env={**os.environ, 'TWIN_POLICY': str(pol)},
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try: urllib.request.urlopen(cls.url + '/api/robot', timeout=1).close(); break
            except OSError: time.sleep(.1)
    @classmethod
    def tearDownClass(cls): cls.twin.terminate(); cls.twin.wait(5); cls.tmp.cleanup()

    def setUp(self):
        self.mcp = g1d_agent.McpClient([sys.executable, str(ROOT / 'services' / 'mcp' / 'g1d_mcp.py'), '--twin_url', self.url], stderr=subprocess.DEVNULL)
        self.mcp.call('home', {}); self.mcp.call('disarm', {})  # start every test disarmed at the start pose
        self.asked = []
    def tearDown(self): self.mcp.close()
    def robot(self):
        with urllib.request.urlopen(self.url + '/api/robot') as r: return json.load(r)['robot']
    def agent(self, llm, approve=True):
        return g1d_agent.Agent(self.mcp, llm, lambda plan: self.asked.append(plan) or approve, log=lambda *_: None)

    def test_single_command_runs_without_confirmation(self):
        reply = self.agent(ScriptedLLM([tc('arm')], 'Armed.')).run('arm the robot')
        self.assertEqual(reply, 'Armed.'); self.assertEqual(self.robot()['mode'], 'armed'); self.assertEqual(self.asked, [])

    def test_plan_runs_only_after_the_human_approves(self):
        for approve in (False, True):
            with self.subTest(approve=approve):
                self.mcp.call('arm', {}); self.mcp.call('gripper', {'state': 'open'})
                llm = ScriptedLLM([tc('plan_sequence', steps=[{'action': 'gripper', 'state': 'closed'}])],
                                  lambda r: f"approved={r['approved']}")
                self.assertEqual(self.agent(llm, approve).run('close the grippers'), f'approved={approve}')
                self.assertEqual(len(self.asked), 1); self.asked.clear()
                self.assertEqual(self.robot()['gripper'], 'closed' if approve else 'open')

    def test_llm_cannot_execute_a_plan_itself(self):
        ag = self.agent(ScriptedLLM([tc('execute_plan', plan_id='abc123')], lambda r: r['error']))
        self.assertNotIn('execute_plan', ag.names)
        self.assertIn('no tool named execute_plan', ag.run('do it')); self.assertEqual(self.asked, [])

    def test_stop_words_bypass_the_llm(self):
        llm = ScriptedLLM()  # any LLM call would fail: no steps
        self.mcp.call('arm', {}); self.assertTrue(self.agent(llm).run('Stop!').startswith('stop: ALLOW'))
        self.assertTrue(self.agent(llm).run('e-stop').startswith('estop: ALLOW')); self.assertEqual(self.robot()['mode'], 'estop')
        self.assertTrue(self.agent(llm).run('Emergency stop now, there is a person in the cell!').startswith('estop: ALLOW'))
        self.assertEqual(llm.calls, 0)
        urllib.request.urlopen(urllib.request.Request(self.url + '/api/command', json.dumps(
            {'payload': {'action': 'reset'}, 'principal': 'defender'}).encode(), {'Content-Type': 'application/json'})).close()  # defender clears it

    def test_pep_deny_reaches_the_llm(self):
        self.mcp.call('arm', {})
        llm = ScriptedLLM([tc('move', v=5)], lambda r: f"refused {r['rule_id']}")
        self.assertEqual(self.agent(llm).run('go fast'), 'refused G1D-102')

    def test_earlier_results_go_stale_before_a_new_prompt(self):
        seen = []  # tool results the LLM sees, per call
        llm = ScriptedLLM([tc('arm')], 'Not armed: E-stop.', [tc('arm')], 'Armed.')
        def spy(messages, tools):
            seen.append([m['content'] for m in messages if m['role'] == 'tool']); return llm(messages, tools)
        ag = self.agent(spy)
        self.mcp.call('estop', {}); self.assertIn('E-stop', ag.run('arma il robot'))
        self.assertIn('G1D-107', seen[1][0])  # within the prompt the LLM sees the real DENY
        urllib.request.urlopen(urllib.request.Request(self.url + '/api/command', json.dumps(
            {'payload': {'action': 'reset'}, 'principal': 'defender'}).encode(), {'Content-Type': 'application/json'})).close()
        self.assertEqual(ag.run('arma il robot'), 'Armed.'); self.assertEqual(self.robot()['mode'], 'armed')
        self.assertEqual(seen[2], [g1d_agent.STALE])  # the old DENY is gone before the new prompt
        self.assertEqual(seen[3][0], g1d_agent.STALE); self.assertIn('ALLOW', seen[3][1])

    def test_bad_tool_calls_do_not_crash(self):
        bad = {'id': 'x', 'function': {'name': 'arm', 'arguments': '{not json'}}
        llm = ScriptedLLM([bad], lambda r: r['error'], [tc('reset')], lambda r: r['error'])
        ag = self.agent(llm)
        self.assertIn('not valid JSON', ag.run('arm')); self.assertIn('no tool named reset', ag.run('reset'))

    def test_tool_call_budget(self):
        llm = ScriptedLLM(*[[tc('get_state')]] * g1d_agent.MAX_CALLS)
        self.assertIn('more than', self.agent(llm).run('loop forever'))
        self.assertIn('longer than', self.agent(ScriptedLLM()).run('x' * (g1d_agent.MAX_PROMPT + 1)))

    def test_history_is_trimmed_by_whole_turns(self):
        ag = self.agent(ScriptedLLM(*([[tc('get_state')], 'ok'] * 30)))
        for i in range(30): ag.run(f'state {i}')
        self.assertLessEqual(len(ag.messages), g1d_agent.MAX_HISTORY + 4)
        self.assertEqual(ag.messages[0]['role'], 'system'); self.assertEqual(ag.messages[1]['role'], 'user')
        self.assertEqual(ag.messages[-4]['content'], 'state 29')

    def test_web_session_parks_the_plan_until_the_browser_decides(self):
        self.mcp.call('arm', {}); self.mcp.call('gripper', {'state': 'open'})
        plan = [tc('plan_sequence', steps=[{'action': 'gripper', 'state': 'closed'}])]
        llm = ScriptedLLM(plan, lambda r: f"approved={r['approved']}", plan, lambda r: f"approved={r['approved']}")
        s = g1d_web.Session(g1d_agent.Agent(self.mcp, llm, None))
        r = s.start('close the grippers'); self.assertEqual(r['status'], 'plan'); self.assertEqual(self.robot()['gripper'], 'open')
        r = s.start('close them now')  # abandons the first plan (rejected, nothing moved) and plans again
        self.assertEqual(r['status'], 'plan'); self.assertEqual(self.robot()['gripper'], 'open'); self.assertEqual(llm.calls, 3)
        r = s.decide(True); self.assertEqual((r['status'], r['reply']), ('done', 'approved=True'))
        self.assertTrue(any('⇒' in l for l in r['log'])); self.assertEqual(self.robot()['gripper'], 'closed')
        with self.assertRaises(ValueError): s.decide(True)  # nothing waiting

if __name__ == '__main__': unittest.main()
