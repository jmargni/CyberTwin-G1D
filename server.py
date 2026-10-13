#!/usr/bin/env python3
"""Two consoles, one authoritative twin. Python standard library only."""
import argparse, copy, functools, json, mimetypes, os, threading, time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit
from engine import Twin, ROOT

def serve(host='127.0.0.1',port3d=3000,portsec=4000,log_dir=None):
    twin=Twin(log_dir or ROOT/'data')
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'public'),**kw)
        def log_message(self,*a):pass
        def end_headers(self):
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','same-origin')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            super().end_headers()
        def send_json(self,obj,status=200):
            data=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):
            path=urlsplit(self.path).path
            if path=='/api/state':return self.send_json(twin.snapshot())
            if path=='/api/robot':
                # Small poll target for the postino bridge: robot state only, without the event history.
                with twin.lock:
                    twin.tick();robot=copy.deepcopy(twin.robot);enforce=twin.enforce
                return self.send_json({'robot':robot,'enforce':enforce})
            if path=='/api/config':return self.send_json({'console':'security' if self.server.server_port==portsec else 'twin','ports':{'twin':port3d,'security':portsec},'mode':'simulation'})
            if path=='/api/export':
                with twin.lock:
                    file=twin.log_dir/'events.jsonl'
                    data=file.read_bytes() if file.exists() else b''
                self.send_response(200);self.send_header('Content-Type','application/x-ndjson');self.send_header('Content-Disposition','attachment; filename="g1d-events.jsonl"');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
            if path.startswith('/api/'):return self.send_json({'error':'Endpoint inesistente'},404)
            if path=='/':self.path='/index.html'
            return super().do_GET()
        def do_POST(self):
            # Same-origin browser requests only; no CORS enabled.
            origin=self.headers.get('Origin')
            if origin and urlsplit(origin).netloc!=self.headers.get('Host'):
                return self.send_json({'error':'Origin non ammessa'},403)
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=16384:return self.send_json({'error':'Payload massimo 16 KiB'},413)
                if 'application/json' not in self.headers.get('Content-Type',''):return self.send_json({'error':'Content-Type application/json richiesto'},415)
                data=json.loads(self.rfile.read(n))
                if not isinstance(data,dict):raise ValueError('Oggetto JSON richiesto')
                path=urlsplit(self.path).path
                if path=='/api/command':result=twin.command(data.get('payload',{}),data.get('principal','operator'))
                elif path=='/api/attack':result=twin.attack(data.get('scenario'))
                elif path=='/api/defense':result=twin.defense(data.get('action'),data.get('value'))
                elif path=='/api/tests':result=twin.suite()
                else:return self.send_json({'error':'Endpoint inesistente'},404)
                self.send_json(result)
            except (ValueError,TypeError,KeyError) as e:self.send_json({'error':str(e)},400)
    servers=[]
    try:
        for port in (port3d,portsec):servers.append(ThreadingHTTPServer((host,port),Handler))
    except OSError:
        for s in servers:s.server_close()
        raise
    for s in servers:threading.Thread(target=s.serve_forever,daemon=True).start()
    print(f'G1-D 3D: http://{host}:{port3d}\nRed Team / Defense: http://{host}:{portsec}\nModalità: simulazione locale | Ctrl+C per arrestare',flush=True)
    try:
        while True:
            with twin.lock:twin.tick()
            time.sleep(.05)
    except KeyboardInterrupt:pass
    finally:
        for s in servers:s.shutdown();s.server_close()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--host',default=os.getenv('TWIN_HOST','127.0.0.1'));p.add_argument('--port-3d',type=int,default=3000);p.add_argument('--port-security',type=int,default=4000);p.add_argument('--data',default=os.getenv('TWIN_DATA',str(ROOT/'data')))
    a=p.parse_args();serve(a.host,a.port_3d,a.port_security,a.data)
