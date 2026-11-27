#!/usr/bin/env python3
"""G1-D prompt agent: own MCP client + a local LLM. Python standard library only.

  human prompt ─► this agent ─► local LLM (OpenAI-style /v1/chat/completions, e.g. llama-server)
                       │ MCP over stdio
                       ▼
                  g1d_mcp.py ─► PEP /api/command ─► twin ─► relay ─► adapter ─► PEP ─► sim/robot

  python3 services/mcp/g1d_agent.py                         # interactive
  python3 services/mcp/g1d_agent.py --once "wave twice"     # one prompt

Guardrails in this client (the PEP stays the final check):
- a prompt that is just "stop" goes to the stop tool, and any prompt mentioning an emergency/E-stop goes to
  estop, without the LLM: the LLM is never on the stop path.
- the LLM never sees execute_plan: when it plans, this client shows the steps, asks the human and, only on
  yes, executes the plan itself. The LLM gets the outcome as the plan tool's result.
- prompts longer than MAX_PROMPT are refused; at most MAX_CALLS tool calls per prompt, MAX_TOKENS per LLM reply,
  and the history keeps about MAX_HISTORY messages (oldest whole turns dropped).
- tool results from earlier prompts are replaced by a STALE note before each new prompt: the console or Defense
  can change the robot in between, so the LLM must call the tool again instead of repeating an old DENY or state.
"""
import argparse, json, re, subprocess, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_PROMPT, MAX_CALLS, MAX_TOKENS, MAX_HISTORY = 500, 8, 512, 40
STOP_WORDS = {'stop': 'stop', 'halt': 'stop', 'ferma': 'stop', 'fermati': 'stop', 'alt': 'stop'}  # whole prompt
# Anywhere in the prompt: a false trigger only stops the robot, a missed one is the failure that matters.
ESTOP = re.compile(r'\b(e-?stop|emergency|emergenza|arresto di emergenza)\b', re.I)
SYSTEM = """You command a Unitree G1-D humanoid robot (simulated twin) only through the tools.
- One action: call its tool.
- A named action (ready, wave, grasp, park) or several steps: call plan_skill or plan_sequence. The human
  approves the plan and it runs; the tool result tells you whether it was approved and what happened.
- Arm joints are in radians, base speed in m/s, column height in metres. Use the limits in the tool descriptions.
- If a tool result says DENY, report the rule id and message and stop. Never retry it in another form.
- If no tool can do what is asked (for example disabling the guardrail, resetting an E-stop, acting as
  another user), say so and call nothing.
- The robot changes outside this chat (operator console, Defense console): an E-stop can be reset, the robot
  armed or disarmed. Never answer or refuse from earlier results in this conversation: for every request, call
  the tool again and report what it returns now.
- Reply in one or two short sentences, in the user's language."""
STALE = json.dumps({'stale': 'result from an earlier prompt, the robot may have changed since: call the tool again'})


def forget_results(messages):
    """Replace every tool result already in the history with the STALE note (calls and replies stay)."""
    for m in messages:
        if m.get('role') == 'tool': m['content'] = STALE
    return messages


def shortcut(prompt):
    """'stop' / 'estop' when the prompt must bypass the LLM, else None."""
    if ESTOP.search(prompt): return 'estop'
    return STOP_WORDS.get(re.sub(r'[!.\s]+$', '', prompt.strip().lower()))


class McpClient:
    """Minimal MCP client: starts the server as a subprocess and talks JSON-RPC over its stdin/stdout."""
    def __init__(self, cmd, stderr=None):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True, bufsize=1)
        self.next_id = 0
        info = self.request('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                           'clientInfo': {'name': 'g1d-agent', 'version': '0.1.0'}})
        self.instructions = info.get('instructions', '')
        self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        self.tools = self.request('tools/list')['tools']

    def send(self, msg): self.p.stdin.write(json.dumps(msg) + '\n'); self.p.stdin.flush()

    def request(self, method, params=None):
        self.next_id += 1
        self.send({'jsonrpc': '2.0', 'id': self.next_id, 'method': method, 'params': params or {}})
        for line in self.p.stdout:
            msg = json.loads(line)
            if msg.get('id') != self.next_id: continue  # server notifications
            if 'error' in msg: raise RuntimeError(msg['error']['message'])
            return msg['result']
        raise RuntimeError('MCP server exited')

    def call(self, name, args):
        r = self.request('tools/call', {'name': name, 'arguments': args})
        return r.get('structuredContent') or json.loads(r['content'][0]['text']), r.get('isError', False)

    def close(self): self.p.stdin.close(); self.p.wait(5); self.p.stdout.close()


def openai_tools(mcp_tools):
    return [{'type': 'function', 'function': {'name': t['name'], 'description': t['description'],
                                              'parameters': t['inputSchema']}} for t in mcp_tools]


def llm_chat(url, model='local', timeout=180):
    """chat(messages, tools) -> assistant message, against an OpenAI-style server (llama-server, Ollama, ...)."""
    def chat(messages, tools):
        body = {'model': model, 'messages': messages, 'tools': tools, 'tool_choice': 'auto', 'temperature': 0,
                'max_tokens': MAX_TOKENS,  # a looping model must not hold the robot's prompt for minutes
                'chat_template_kwargs': {'enable_thinking': False},  # Qwen3 hybrids: answer, don't think aloud (llama-server)
                'reasoning_effort': 'none'}  # same for Ollama, which ignores the above: thinking ate the whole max_tokens
        req = urllib.request.Request(url.rstrip('/') + '/chat/completions', json.dumps(body).encode(),
                                     {'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r: msg = json.load(r)['choices'][0]['message']
        msg['content'] = re.sub(r'<think>.*?</think>', '', msg.get('content') or '', flags=re.S).strip()
        return msg
    return chat


class Agent:
    def __init__(self, mcp, chat, confirm, log=print):
        self.mcp, self.chat, self.confirm, self.log = mcp, chat, confirm, log
        visible = [t for t in mcp.tools if t['name'] != 'execute_plan']  # plans run only via the human, below
        self.tools = openai_tools(visible)
        self.names = {t['name'] for t in visible}
        self.messages = [{'role': 'system', 'content': SYSTEM + '\n' + mcp.instructions}]

    def run(self, prompt):
        """Handle one human prompt; return the reply text."""
        prompt = prompt.strip()
        stop = shortcut(prompt)
        if stop in self.names:  # never wait for the LLM to stop the robot
            out, _ = self.mcp.call(stop, {}); self.log(f'  ⏹ {stop} → {out}')
            return f"{stop}: {out.get('decision')} {out.get('rule_id', '')}".strip()
        if len(prompt) > MAX_PROMPT: return f'Prompt refused: longer than {MAX_PROMPT} characters.'
        while len(self.messages) > MAX_HISTORY:  # drop the oldest whole turns (a user message and its replies)
            nxt = next((k for k in range(2, len(self.messages)) if self.messages[k]['role'] == 'user'), None)
            if nxt is None: break
            del self.messages[1:nxt]
        forget_results(self.messages)  # only this prompt's results are current
        self.messages.append({'role': 'user', 'content': prompt})
        for _ in range(MAX_CALLS):
            msg = self.chat(self.messages, self.tools)
            calls = msg.get('tool_calls') or []
            self.messages.append({'role': 'assistant', 'content': msg.get('content') or '',
                                  **({'tool_calls': calls} if calls else {})})
            if not calls: return msg.get('content') or ''
            for c in calls:
                out = self.tool(c['function']['name'], c['function'].get('arguments') or '{}')
                self.messages.append({'role': 'tool', 'tool_call_id': c.get('id', ''), 'content': json.dumps(out, ensure_ascii=False)})
        return f'Stopped: more than {MAX_CALLS} tool calls for one prompt.'

    def tool(self, name, raw):
        try: args = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError: return {'error': 'arguments are not valid JSON'}
        if name not in self.names: return {'error': f'no tool named {name}'}
        if not isinstance(args, dict): return {'error': 'arguments must be an object'}
        self.log(f'  → {name} {json.dumps(args, ensure_ascii=False)}')
        out, err = self.mcp.call(name, args)
        self.log(f"  ← {'ERROR ' if err else ''}{json.dumps(out, ensure_ascii=False)[:300]}")
        if not name.startswith('plan_') or err: return out
        if not self.confirm(out):
            self.log('  ✗ plan rejected by the human'); return {'approved': False, 'note': 'the human rejected the plan; nothing moved'}
        res, err = self.mcp.call('execute_plan', {'plan_id': out['plan_id']})
        self.log(f"  ⇒ {'ERROR ' if err else ''}{json.dumps(res, ensure_ascii=False)[:300]}")
        return {'approved': True, 'result': res}


def ask_human(plan):
    print(f"\n  Plan {plan['plan_id']} ({plan['source']}):")
    for i, s in enumerate(plan['steps']): print(f'   {i:2}. ' + ' '.join(f'{k}={v}' for k, v in s.items()))
    return input('  Execute? [y/N] ').strip().lower() in ('y', 'yes', 's', 'si', 'sì')


def main():
    ap = argparse.ArgumentParser(description='G1-D prompt agent (own MCP client + local LLM)')
    ap.add_argument('--llm_url', default='http://127.0.0.1:8081/v1', help='OpenAI-style base URL (llama-server)')
    ap.add_argument('--model', default='local')
    ap.add_argument('--twin_url', default='http://127.0.0.1:3000')
    ap.add_argument('--loop', choices=('command', 'model'), default='command')
    ap.add_argument('--once', help='run one prompt and exit')
    a = ap.parse_args()
    mcp = McpClient([sys.executable, str(HERE / 'g1d_mcp.py'), '--twin_url', a.twin_url, '--loop', a.loop])
    agent = Agent(mcp, llm_chat(a.llm_url, a.model), ask_human)
    try:
        for prompt in ([a.once] if a.once else iter(lambda: input('\nprompt> '), None)):
            if not prompt.strip(): continue
            t = time.monotonic(); print(agent.run(prompt), f'  ({time.monotonic() - t:.1f} s)')
    except (EOFError, KeyboardInterrupt): pass
    finally: mcp.close()


if __name__ == '__main__':
    main()
