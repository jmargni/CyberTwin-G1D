"""Contract stub for local connectivity tests; not a model or inference benchmark."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/predict_action':self.send_error(404);return
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=65536:self.send_error(413);return
            body=json.loads(self.rfile.read(n))
            if not isinstance(body.get('observation'),dict):raise ValueError('observation missing')
            raw=json.dumps({'mock':True,'command':{'action':'status','params':{}}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        except (ValueError,AttributeError):self.send_error(400)
ThreadingHTTPServer(('0.0.0.0',8000),Handler).serve_forever()
