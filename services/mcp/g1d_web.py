#!/usr/bin/env python3
"""Web page for the G1-D prompt agent: chat + plan approval + the live 3D workspace. Stdlib only.

  python3 services/mcp/g1d_web.py            # http://127.0.0.1:3002

Same agent as g1d_agent.py (own MCP client + local LLM); the browser is the human who approves plans.
The 3D view reuses the console's scene.js and model files (served read-only from public/) and polls the
twin's /api/state through this server, so the twin itself is unchanged.
STOP / E-STOP (buttons, or a stop/emergency prompt) use a second MCP connection, so they work while the
LLM is thinking or a plan is running.
"""
import argparse, json, sys, threading, urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit
import g1d_agent as ga

HERE = Path(__file__).resolve().parent
PUBLIC = HERE.parents[1] / 'public'
PLAN_WAIT_S = 120  # an unanswered plan is rejected (the server's plan_id expires then anyway)


class Session:
    """One prompt at a time. The agent runs in a thread; confirm() parks the plan until the browser decides."""
    def __init__(self, agent):
        self.agent, self.cv = agent, threading.Condition()
        self.busy, self.plan, self.decision, self.reply, self.log, self.sent = False, None, None, None, [], 0
        agent.confirm, agent.log = self.confirm, self.log.append

    def start(self, prompt):
        with self.cv:
            if self.plan:  # a new prompt (or a reloaded page) abandons the pending plan: rejected, nothing moves
                self.plan, self.decision = None, False; self.cv.notify_all()
                self.cv.wait_for(lambda: self.reply is not None, 60)
            if self.busy and self.reply is None: raise ValueError('busy: the previous prompt is still running')
            self.busy, self.reply, self.log[:], self.sent = True, None, [], 0
        def run():
            try: reply = self.agent.run(prompt)
            except Exception as e: reply = f'Error: {e}'  # LLM down, MCP server gone, ...
            with self.cv: self.reply = reply; self.cv.notify_all()
        threading.Thread(target=run, daemon=True).start()
        return self.wait()

    def confirm(self, plan):
        with self.cv:
            self.plan, self.decision = plan, None; self.cv.notify_all()
            self.cv.wait_for(lambda: self.decision is not None, PLAN_WAIT_S)
            ok, self.plan = bool(self.decision), None
            return ok

    def decide(self, approve):
        with self.cv:
            if not self.plan: raise ValueError('no plan is waiting')
            self.plan, self.decision = None, bool(approve); self.cv.notify_all()
        return self.wait()

    def wait(self):
        with self.cv:
            self.cv.wait_for(lambda: self.plan or self.reply is not None, 600)
            out = {'log': self.log[self.sent:]}; self.sent = len(self.log)  # only lines not sent yet
            if self.plan: return {**out, 'status': 'plan', 'plan': self.plan}
            self.busy = False
            return {**out, 'status': 'done', 'reply': self.reply}


def serve(host, port, twin_url, llm_url, model, loop):
    mcp_cmd = [sys.executable, str(HERE / 'g1d_mcp.py'), '--twin_url', twin_url, '--loop', loop]
    session = Session(ga.Agent(ga.McpClient(mcp_cmd), ga.llm_chat(llm_url, model), None))
    stopper, stop_lock = ga.McpClient(mcp_cmd), threading.Lock()

    def stop(tool):
        with stop_lock: out, _ = stopper.call(tool, {})
        return {'status': 'done', 'reply': f"{tool}: {out.get('decision')} {out.get('rule_id', '')}".strip(), 'log': []}

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw): super().__init__(*a, directory=str(PUBLIC), **kw)
        def log_message(self, *a): pass
        def end_headers(self):
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            super().end_headers()
        def json(self, obj, status=200):
            data = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(status); self.send_header('Content-Type', 'application/json'); self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/api/state':  # robot pose + geofence for the 3D view
                try:
                    with urllib.request.urlopen(twin_url + '/api/state', timeout=2) as r: s = json.load(r)
                    return self.json({k: s.get(k) for k in ('robot', 'enforce', 'geofence', 'keepout')})
                except OSError as e: return self.json({'error': f'twin not reachable: {e}'}, 502)
            if path.startswith('/api/'): return self.json({'error': 'not found'}, 404)
            if path in ('/', '/prompt.html'):
                data = (HERE / 'prompt.html').read_bytes()
                self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data))); self.end_headers(); return self.wfile.write(data)
            return super().do_GET()
        def do_POST(self):
            origin = self.headers.get('Origin')  # same-origin only: another site must not drive the robot
            if origin and urlsplit(origin).netloc != self.headers.get('Host'): return self.json({'error': 'origin not allowed'}, 403)
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= 4096: return self.json({'error': 'body too large'}, 413)
                if 'application/json' not in self.headers.get('Content-Type', ''): return self.json({'error': 'JSON required'}, 415)
                body = json.loads(self.rfile.read(n)); path = urlsplit(self.path).path
                if path == '/api/prompt':
                    prompt = str(body.get('prompt', '')).strip()
                    if not prompt: raise ValueError('empty prompt')
                    tool = ga.shortcut(prompt)
                    return self.json(stop(tool) if tool else session.start(prompt))
                if path == '/api/stop': return self.json(stop('estop' if body.get('estop') else 'stop'))
                if path == '/api/decide': return self.json(session.decide(body.get('approve')))
                return self.json({'error': 'not found'}, 404)
            except (ValueError, TypeError, AttributeError) as e: return self.json({'error': str(e)}, 400)

    srv = ThreadingHTTPServer((host, port), Handler)
    print(f'G1-D prompt page: http://{host}:{port}   (twin {twin_url}, LLM {llm_url})', flush=True)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally: srv.server_close(); session.agent.mcp.close(); stopper.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='G1-D prompt page (agent + live 3D workspace)')
    ap.add_argument('--host', default='127.0.0.1'); ap.add_argument('--port', type=int, default=3002)
    ap.add_argument('--twin_url', default='http://127.0.0.1:3000')
    ap.add_argument('--llm_url', default='http://127.0.0.1:8081/v1'); ap.add_argument('--model', default='local')
    ap.add_argument('--loop', choices=('command', 'model'), default='command')
    a = ap.parse_args(); serve(a.host, a.port, a.twin_url.rstrip('/'), a.llm_url, a.model, a.loop)
