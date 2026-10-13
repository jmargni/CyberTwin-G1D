"""HTTP facade for the authoritative kinematic twin; no second independent state."""
import json, os, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
TWIN_URL=os.getenv('TWIN_URL','http://twin:3000').rstrip('/')
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def reply(self,obj,code=200):
        raw=json.dumps(obj).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def upstream(self,path,data=None):
        req=urllib.request.Request(TWIN_URL+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=5) as r:return json.load(r)
    def do_GET(self):
        if self.path not in ('/','/healthz','/state'):return self.reply({'error':'Not found'},404)
        try:
            r=self.upstream('/api/state')['robot']
            if self.path!='/state':return self.reply({'status':'ok','simulation':'kinematic','upstream':'connected'})
            self.reply({'robot':'Unitree G1-D','mode':'simulation','robot_mode':r['mode'],'position':{'x':r['x'],'y':r['y'],'z':0.0},'orientation':{'roll':0.0,'pitch':0.0,'yaw':r['yaw']},'height':r['height'],'joints':r['joints'],'velocity':{'v':r['v'],'w':r['w']},'gripper':r['gripper']})
        except (OSError,ValueError,KeyError) as exc:self.reply({'error':str(exc)},502)
    def do_POST(self):
        if self.path!='/command':return self.reply({'error':'Not found'},404)
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=16384:return self.reply({'error':'Payload massimo 16 KiB'},413)
            d=json.loads(self.rfile.read(n))
            if not isinstance(d,dict):raise ValueError('Oggetto richiesto')
            self.reply(self.upstream('/api/command',d))
        except urllib.error.HTTPError as exc:self.reply({'error':exc.read().decode()},exc.code)
        except (ValueError,TypeError) as exc:self.reply({'error':str(exc)},400)
        except OSError as exc:self.reply({'error':str(exc)},502)
if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',int(os.getenv('SIMULATOR_PORT','6003'))),Handler).serve_forever()
