"""recorder -- a stub inference endpoint that keeps the request and answers it.

WHY INTERCEPT AT ALL. Every other way of asking "what does this harness put in
front of the model" is a reconstruction: counting the files we *believe* are
injected, or trusting a doc that was true at some earlier version. Both drift
silently, and the 2026-09-01 plugin measurement is the proof -- counting the
plugin cache directory gave ~3,800 tokens against a real ~1,262, because the
same skill tree is cloned once per plugin (`knowledge/tools/claude-code.md`).
The request body is the only artefact that cannot be wrong: it is what the
model was actually shown.

WHAT THIS IS NOT. Not a proxy. Nothing is forwarded upstream, so an audit
costs zero API tokens and cannot leak a prompt to a third party -- which also
means the harness gets a canned answer and will end its turn immediately. One
request per audit is all we need; the fixed prefix is fully formed on turn one.

THREE DIALECTS, because the four harnesses do not agree:
  POST /v1/messages           Anthropic  -- Claude Code, opencode/anthropic
  POST /v1/chat/completions   OpenAI chat -- hermes, opencode/ollama
  POST /v1/responses          OpenAI responses -- codex, which as of 0.149.1
                              REFUSES `wire_api = "chat"` outright
Both answer with a single short assistant message, streamed as SSE when the
request asks for it. A harness that cannot parse the reply still counts: the
capture happens before we answer.

THE FIRST REQUEST IS NOT ALWAYS THE ONE YOU WANT. opencode's very first call
on a fresh session is a *title generator* -- a 2.4 KB prompt with no tools,
nothing to do with the agent's own context. Measured 2026-09-03; reporting it
as "opencode's overhead" would have understated the real prefix by an order of
magnitude. So the server records everything and `wait_settle()` keeps listening
after the first arrival; choosing among them is `harnesses.select_primary`,
which states its rule rather than assuming an order.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STUB_TEXT = "ok"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- capture -----------------------------------------------------------
    def do_POST(self):                                    # noqa: N802 (stdlib API)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {"_unparsed": raw.decode("utf-8", "replace")}
        self.server.captures.append({
            "path": self.path,
            "at": time.time(),
            "headers": {k.lower(): v for k, v in self.headers.items()
                        if k.lower() not in ("authorization", "x-api-key")},
            "bytes": len(raw),
            "body": body,
        })
        self.server.arrived.set()

        if "chat/completions" in self.path:
            self._answer_openai(body)
        elif "/responses" in self.path:
            self._answer_responses(body)
        else:
            self._answer_anthropic(body)

    def do_GET(self):                                     # noqa: N802
        # `/v1/models` and health probes: answer something shaped right.
        self._json(200, {"data": [{"id": "stub", "object": "model"}], "object": "list"})

    # -- replies -----------------------------------------------------------
    def _answer_anthropic(self, body):
        if not body.get("stream"):
            return self._json(200, {
                "id": "msg_stub", "type": "message", "role": "assistant",
                "model": body.get("model", "stub"),
                "content": [{"type": "text", "text": STUB_TEXT}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })
        msg = {"id": "msg_stub", "type": "message", "role": "assistant",
               "model": body.get("model", "stub"), "content": [],
               "stop_reason": None, "stop_sequence": None,
               "usage": {"input_tokens": 1, "output_tokens": 1}}
        self._sse([
            ("message_start", {"type": "message_start", "message": msg}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": STUB_TEXT}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta",
                               "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 1}}),
            ("message_stop", {"type": "message_stop"}),
        ])

    def _answer_openai(self, body):
        created, model = int(time.time()), body.get("model", "stub")
        if not body.get("stream"):
            return self._json(200, {
                "id": "chatcmpl-stub", "object": "chat.completion",
                "created": created, "model": model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": STUB_TEXT}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })

        def chunk(delta, finish=None):
            return {"id": "chatcmpl-stub", "object": "chat.completion.chunk",
                    "created": created, "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        self._sse([(None, chunk({"role": "assistant", "content": STUB_TEXT})),
                   (None, chunk({}, "stop"))], done=True)

    def _answer_responses(self, body):
        model = body.get("model", "stub")
        resp = {"id": "resp_stub", "object": "response", "model": model,
                "status": "completed", "output": [
                    {"id": "msg_stub", "type": "message", "role": "assistant",
                     "status": "completed",
                     "content": [{"type": "output_text", "text": STUB_TEXT,
                                  "annotations": []}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
        if not body.get("stream"):
            return self._json(200, resp)
        self._sse([
            ("response.created", {"type": "response.created",
                                  "response": dict(resp, status="in_progress", output=[])}),
            ("response.output_text.delta", {"type": "response.output_text.delta",
                                            "item_id": "msg_stub", "output_index": 0,
                                            "content_index": 0, "delta": STUB_TEXT}),
            ("response.completed", {"type": "response.completed", "response": resp}),
        ])

    # -- wire --------------------------------------------------------------
    def _json(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _sse(self, events, done=False):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for name, payload in events:
            head = f"event: {name}\n" if name else ""
            self.wfile.write((head + "data: " + json.dumps(payload) + "\n\n").encode())
            self.wfile.flush()
        if done:
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        self.close_connection = True

    def log_message(self, *_):
        """Silence. The audit's own output is the report."""


class Recorder:
    """`with Recorder() as rec:` -- rec.url is what a harness should be told."""

    def __init__(self, host="127.0.0.1", port=0):
        self.server = ThreadingHTTPServer((host, port), _Handler)
        self.server.captures = []
        self.server.arrived = threading.Event()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def captures(self) -> list:
        return self.server.captures

    def wait(self, timeout: float) -> bool:
        """True once a request has landed. The caller kills the harness then --
        waiting for it to exit cleanly is waiting for a turn we do not want."""
        return self.server.arrived.wait(timeout)

    def wait_settle(self, timeout: float, settle: float = 20.0) -> bool:
        """Wait for the first request, then go on listening while requests keep
        arriving, until `settle` seconds pass with none. A harness that fires a
        side-call first (see the module note) would otherwise be measured by
        its cheapest request."""
        if not self.server.arrived.wait(timeout):
            return False
        seen = -1
        while seen != len(self.captures):
            seen = len(self.captures)
            time.sleep(settle)
        return True
