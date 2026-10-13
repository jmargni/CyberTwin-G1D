#!/usr/bin/env python3
import argparse, json, os, socket, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

def emit(path, event):
    path.parent.mkdir(parents=True, exist_ok=True)
    event={"ts":time.time(),**event}
    with path.open("a",encoding="utf-8") as f:f.write(json.dumps(event,separators=(",",":"))+"\n")

def main():
    p=argparse.ArgumentParser(); p.add_argument('--name',required=True); p.add_argument('--port',type=int,required=True); p.add_argument('--evidence',default='/evidence/events.jsonl'); a=p.parse_args()
    evidence=Path(a.evidence)
    class H(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def reply(self,obj,code=200):
            b=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            obj={"service":a.name,"port":a.port,"status":"ok","robot_ip":os.getenv('ROBOT_IP','192.168.123.161'),"robot_nic":os.getenv('ROBOT_NIC','eth0')}; emit(evidence,{"kind":"probe","transport":"tcp","peer":self.client_address[0],**obj}); self.reply(obj)
        def do_POST(self):
            n=int(self.headers.get('Content-Length','0')); raw=self.rfile.read(min(n,1048576)); emit(evidence,{"kind":"command","transport":"tcp","peer":self.client_address[0],"service":a.name,"bytes":len(raw)}); self.reply({"accepted":True,"service":a.name,"bytes":len(raw)},202)
    def udp():
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(('0.0.0.0',a.port))
        while True:
            data,peer=s.recvfrom(65535); emit(evidence,{"kind":"datagram","transport":"udp","peer":peer[0],"service":a.name,"bytes":len(data)}); s.sendto(json.dumps({"service":a.name,"status":"ok"}).encode(),peer)
    threading.Thread(target=udp,daemon=True).start(); emit(evidence,{"kind":"startup","service":a.name,"port":a.port}); ThreadingHTTPServer(('0.0.0.0',a.port),H).serve_forever()
if __name__=='__main__':main()
