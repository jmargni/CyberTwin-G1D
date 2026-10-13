import json, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from dgs_g1d_wma_adapter import run_cycle, WMA_BASE_URL, WMA_ENDPOINT

lock = threading.Lock()
audit_lock = threading.Lock()
last = None
EVIDENCE = Path(os.getenv('EVIDENCE_DIR', '/evidence'))

def audit(event):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    with audit_lock, (EVIDENCE/'wma-cycles.jsonl').open('a') as f:
        f.write(json.dumps({'timestamp':time.time(), 'source':'g1d-wma-adapter', **event}, allow_nan=False)+'\n')

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def reply(self, data, code=200):
        raw=json.dumps(data,allow_nan=False).encode()
        self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers()
        try:self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass
    def do_GET(self):
        if self.path in ('/', '/healthz'):
            return self.reply({'status':'ok','service':'wma-adapter','wma_url':WMA_BASE_URL+WMA_ENDPOINT,'wma_connectivity':'not_checked','simulation':'kinematic'})
        if self.path == '/last':return self.reply(last or {'status':'NO_CYCLE'})
        return self.reply({'error':'Not found'},404)
    def do_POST(self):
        global last
        if self.path != '/cycle':return self.reply({'error':'Not found'},404)
        origin=self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):return self.reply({'error':'Origin non ammessa'},403)
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=16384:return self.reply({'error':'Payload massimo 16 KiB'},413)
            if 'application/json' not in self.headers.get('Content-Type',''):return self.reply({'error':'Content-Type richiesto'},415)
            data=json.loads(self.rfile.read(n))
            if not isinstance(data,dict) or set(data)-{'instruction','execute'}:raise ValueError('Campi ammessi: instruction, execute')
            if not lock.acquire(blocking=False):return self.reply({'error':'Ciclo gia in corso'},409)
            try:
                # Verify audit can be written before inference or command execution.
                audit({'status':'CYCLE_REQUESTED','request':data})
                result=run_cycle(data.get('instruction','stand'),data.get('execute',False))
                last=result
                audit(result)
            finally:lock.release()
            return self.reply(result)
        except (ValueError,TypeError) as exc:return self.reply({'error':str(exc)},400)
        except OSError as exc:return self.reply({'error':'Audit non disponibile: '+str(exc)},503)

if __name__=='__main__':
    ThreadingHTTPServer(('0.0.0.0',int(os.getenv('ADAPTER_PORT','6001'))),Handler).serve_forever()
