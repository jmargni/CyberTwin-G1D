"""Smoke test of the postino -> twin PEP enforcement loop, over real HTTP.

Drives the actual `pep_bridge.TwinPEP` (from the unifolm-wma-postino repo) against a
real twin subprocess. Only the robot executor and the model are absent; the PEP never
sees them. Skips unless the bridge and numpy are importable.

Locate the bridge via $PEP_BRIDGE_PATH, else the sibling checkout
../unifolm-wma-postino/unitree_deploy/unitree_deploy/pep_bridge.py.
"""
import importlib.util, os, socket, subprocess, sys, tempfile, time, unittest
from pathlib import Path
try:
    import requests, numpy as np
except ImportError:
    requests = None

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = Path(os.getenv('PEP_BRIDGE_PATH',
    ROOT.parent / 'unifolm-wma-postino' / 'unitree_deploy' / 'unitree_deploy' / 'pep_bridge.py'))


def load_bridge():
    spec = importlib.util.spec_from_file_location('pep_bridge', BRIDGE)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(requests and BRIDGE.exists(), f'needs requests+numpy and {BRIDGE}')
class PepBridgeLoopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pb = load_bridge()
        with socket.socket() as s: s.bind(('127.0.0.1', 0)); p3 = s.getsockname()[1]
        with socket.socket() as s: s.bind(('127.0.0.1', 0)); p4 = s.getsockname()[1]
        cls.base = f'http://127.0.0.1:{p3}'
        cls.p4 = f'http://127.0.0.1:{p4}'
        cls.tmp = tempfile.TemporaryDirectory()
        cls.proc = subprocess.Popen(
            [sys.executable, str(ROOT/'server.py'), '--host', '127.0.0.1',
             '--port-3d', str(p3), '--port-security', str(p4), '--data', cls.tmp.name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):  # wait up to ~10s for the twin to answer
            try:
                if requests.get(cls.base+'/api/robot', timeout=1).status_code == 200: break
            except requests.RequestException: pass
            time.sleep(.1)
        else:
            cls.proc.terminate(); raise RuntimeError('twin did not start')

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(timeout=5); cls.tmp.cleanup()

    def arm(self):   return requests.post(self.base+'/api/command', json={'payload':{'action':'arm'},'principal':'operator'}).json()
    def estop(self): return requests.post(self.p4+'/api/defense', json={'action':'estop'}).json()

    def test_enforcement_loop(self):
        pep = self.pb.TwinPEP(self.base)
        dt = 1/30  # 30 Hz -> 3 rad/s cap = 0.1 rad/step

        self.assertFalse(pep.may_continue())                       # disarmed: robot holds still
        self.assertEqual(self.arm()['decision'], 'ALLOW')
        self.assertTrue(pep.may_continue())                        # armed

        j = pep.state()['joints']
        start = np.array([j[n] for n in self.pb.G1_DEX1_ARM_JOINTS] + [5.4, 5.4], dtype=float)

        good = start.copy()[None, :]; good[0, 0] += 0.02           # within the 0.1 rad/step cap
        d = pep.check_trajectory(good, dt, start=start)
        self.assertTrue(d.allowed); self.assertEqual(d.rule_id, 'G1D-200')

        fast = start.copy()[None, :]; fast[0, 0] += 0.5            # over 3 rad/s
        d = pep.check_trajectory(fast, dt, start=start)
        self.assertFalse(d.allowed); self.assertEqual(d.rule_id, 'G1D-102')

        self.assertEqual(self.estop()['decision'], 'DEFENSE')
        self.assertFalse(pep.may_continue())                       # client aborts within one step
        self.assertFalse(pep.check_trajectory(good, dt, start=start).allowed)


if __name__ == '__main__':
    unittest.main()
