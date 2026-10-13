import json, os, socket, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import requests
import dgs_g1d_wma_adapter as adapter

class MappingTests(unittest.TestCase):
    def test_user_envelopes(self):
        self.assertEqual(adapter.simulator_to_wma({'x':1}),{'observation':{'x':1}})
        self.assertEqual(adapter.wma_to_simulator({'a':1}),{'source':'UnifoLM-WMA-0-Dual','action':{'a':1}})
    def test_known_mappings(self):
        for cmd in ({'action':'status'}, {'action':'stop'}, {'action':'move','params':{'v':.1,'w':0,'duration':1}}, {'action':'height','params':{'value':.1}}, {'action':'joint','params':{'name':'a','value':0}}, {'action':'gripper','params':{'state':'open'}}):
            self.assertEqual(adapter.map_command({'command':cmd})['action'],cmd['action'])
    def test_unknown_and_privileged(self):
        for response in ({'action':[1,2]}, {'command':{'action':'arm'}}, {'command':{'action':'estop'}}, {'command':{'action':'stop','identity':'admin'}}, {'status':'ERROR','command':{'action':'stop'}}, {'command':{'action':'move','params':{'v':1}}}):
            with self.subTest(response=response),self.assertRaises(ValueError):adapter.map_command(response)
    def test_non_finite(self):
        for val in (True,float('nan'),float('inf'),'1'):
            with self.assertRaises(ValueError):adapter.map_command({'command':{'action':'height','params':{'value':val}}})

class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.processes=[];cls.logs=[]
        def port():
            with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
        cls.tp,cls.sp,cls.ap,cls.security=port(),port(),port(),port()
        cls.reply_data={'command':{'action':'status'}};cls.status=200;cls.delay=0;cls.callback=None;cls.seen=[]
        class WMA(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                cls.seen.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                body,status,delay,callback=cls.reply_data,cls.status,cls.delay,cls.callback
                if callback:callback()
                time.sleep(delay)
                raw=body.encode() if isinstance(body,str) else json.dumps(body).encode()
                self.send_response(status);self.send_header('Content-Length',str(len(raw)));self.end_headers()
                try:self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError):pass
        cls.mock=ThreadingHTTPServer(('127.0.0.1',0),WMA);threading.Thread(target=cls.mock.serve_forever,daemon=True).start()
        root=Path(__file__).resolve().parents[1]
        env={**os.environ,'PYTHONPATH':str(root),'TWIN_URL':f'http://127.0.0.1:{cls.tp}','SIMULATOR_PORT':str(cls.sp),'SIMULATOR_URL':f'http://127.0.0.1:{cls.sp}','ADAPTER_PORT':str(cls.ap),'WMA_BASE_URL':f'http://127.0.0.1:{cls.mock.server_port}','WMA_TIMEOUT':'.5','MAX_OBSERVATION_AGE':'.2','EVIDENCE_DIR':cls.temp.name}
        for args in (['server.py','--port-3d',str(cls.tp),'--port-security',str(cls.security),'--data',cls.temp.name],['services/simulator/app.py'],['services/adapter/app.py']):
            log=open(Path(cls.temp.name)/(str(len(cls.logs))+'.log'),'w');cls.logs.append(log)
            cls.processes.append(subprocess.Popen([sys.executable,*args],cwd=root,env=env,stdout=log,stderr=log))
        for url in (f'http://127.0.0.1:{cls.tp}/api/state',f'http://127.0.0.1:{cls.sp}/state',f'http://127.0.0.1:{cls.ap}/healthz'):
            for _ in range(100):
                try:
                    if requests.get(url,timeout=1).status_code==200:break
                except requests.RequestException:pass
                time.sleep(.05)
            else:raise RuntimeError('Startup failed '+url)
    @classmethod
    def tearDownClass(cls):
        for p in cls.processes:p.terminate()
        for p in cls.processes:p.wait(timeout=5)
        cls.mock.shutdown();cls.mock.server_close()
        for f in cls.logs:f.close()
        cls.temp.cleanup()
    def setUp(self):
        c=type(self);c.reply_data={'command':{'action':'status'}};c.status=200;c.delay=0;c.callback=None;c.seen=[]
        self.twin('/api/defense',{'action':'reset'})
        self.twin('/api/defense',{'action':'restore'})
        self.twin('/api/defense',{'action':'enforce','value':True})
    def twin(self,path,data):return requests.post(f'http://127.0.0.1:{self.tp}'+path,json=data,timeout=3).json()
    def cycle(self,execute=False):return requests.post(f'http://127.0.0.1:{self.ap}/cycle',json={'instruction':'stand','execute':execute},timeout=5).json()
    def state(self):return requests.get(f'http://127.0.0.1:{self.tp}/api/state',timeout=3).json()
    def move(self,v=.1):type(self).reply_data={'command':{'action':'move','params':{'v':v,'w':0,'duration':1}}}
    def test_preview_and_live_observation(self):
        self.twin('/api/command',{'payload':{'action':'arm'}})
        self.twin('/api/command',{'payload':{'action':'height','params':{'value':.2}}})
        self.move();r=self.cycle();self.assertEqual(r['status'],'PREVIEW');self.assertFalse(r['applied'])
        path,payload=type(self).seen[-1];self.assertEqual(path,'/predict_action');self.assertEqual(payload['observation']['height'],.2);self.assertEqual(payload['observation']['instruction'],'stand');self.assertEqual(self.state()['robot']['v'],0)
    def test_execute_changes_authoritative_twin(self):
        self.twin('/api/command',{'payload':{'action':'arm'}});self.move();r=self.cycle(True)
        self.assertEqual(r['status'],'APPLIED');time.sleep(.08);self.assertGreater(self.state()['robot']['x'],0)
    def test_disarmed_blocks(self):
        self.move();r=self.cycle(True);self.assertEqual(r['status'],'BLOCKED');self.assertEqual(r['pep_result']['rule_id'],'G1D-107')
    def test_overspeed_blocks(self):
        self.twin('/api/command',{'payload':{'action':'arm'}});self.move(99);r=self.cycle(True);self.assertEqual(r['status'],'BLOCKED');self.assertEqual(r['pep_result']['rule_id'],'G1D-102')
    def test_estop_during_inference_blocks(self):
        self.twin('/api/command',{'payload':{'action':'arm'}});self.move()
        type(self).callback=lambda:self.twin('/api/defense',{'action':'estop'})
        self.assertEqual(self.cycle(True)['status'],'BLOCKED');self.assertEqual(self.state()['robot']['v'],0)
    def test_revoked_identity(self):
        self.twin('/api/defense',{'action':'revoke','value':'operator'})
        self.assertEqual(self.cycle(True)['status'],'BLOCKED')
    def test_monitor_does_not_execute_invalid(self):
        self.twin('/api/command',{'payload':{'action':'arm'}});self.twin('/api/defense',{'action':'enforce','value':False});self.move(99)
        r=self.cycle(True);self.assertEqual(r['pep_result']['decision'],'MONITOR');self.assertFalse(r['applied'])
    def test_unknown_model_output(self):
        type(self).reply_data={'action':[[.2,.4]]};self.assertEqual(self.cycle(True)['status'],'UNMAPPED')
    def test_http_error(self):
        type(self).status=500;self.assertEqual(self.cycle(True)['status'],'WMA_ERROR')
    def test_non_json(self):
        type(self).reply_data='<html>error</html>';self.assertEqual(self.cycle(True)['status'],'WMA_ERROR')
    def test_array_json(self):
        type(self).reply_data=[1,2];self.assertEqual(self.cycle(True)['status'],'WMA_ERROR')
    def test_timeout(self):
        type(self).delay=.8;self.assertEqual(self.cycle(True)['status'],'WMA_ERROR')
    def test_stale_observation(self):
        type(self).delay=.3;self.assertEqual(self.cycle(True)['status'],'STALE')
    def test_validation(self):
        for data in ({'execute':'false'},{'instruction':''},{'url':'http://other'}):
            self.assertEqual(requests.post(f'http://127.0.0.1:{self.ap}/cycle',json=data,timeout=2).status_code,400)
    def test_concurrent_cycle_rejected(self):
        type(self).delay=.3
        t=threading.Thread(target=self.cycle);t.start()
        try:
            for _ in range(100):
                if type(self).seen:break
                time.sleep(.005)
            self.assertEqual(requests.post(f'http://127.0.0.1:{self.ap}/cycle',json={},timeout=2).status_code,409)
        finally:t.join()
    def test_audit_and_existing_ui_suite(self):
        r=self.cycle();rows=[json.loads(s) for s in (Path(self.temp.name)/'wma-cycles.jsonl').read_text().splitlines()]
        self.assertTrue(any(x.get('cycle_id')==r['cycle_id'] for x in rows))
        suite=self.twin('/api/tests',{});self.assertEqual(len(suite),9);self.assertTrue(all(x['passed'] for x in suite))

if __name__=='__main__':unittest.main()
