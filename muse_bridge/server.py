"""本地 API 服务：把 muse.ai 网页桥接包成 OpenAI / Anthropic 兼容接口。

只用 Python 标准库（http.server），不加新依赖。
调用是串行的：网页自动化同一时间只能跑一个会话，用锁排队。

对外三要素（启动时也会打印）：
  API base URL: http://127.0.0.1:8765/v1
  API key: 启动参数 --api-key / 环境变量 MUSE_BRIDGE_API_KEY，
                 都不给则随机生成一个并打印（仅本机桥接用，不是 Muse 账号凭证）
  Model: muse-web（别名）；也接受 muse-spark-1.3
                 注意：这是桥的别名，不是 Meta 官方 Model API 的模型 ID；
                 实际后端是你 muse.ai 账号当前运行的模型。
"""
from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

MODELS = [
    {"id": "muse-web", "object": "model", "created": 0, "owned_by": "muse-web-bridge"},
    {"id": "muse-spark-1.3", "object": "model", "created": 0, "owned_by": "muse-web-bridge"},
]
MODEL_IDS = {m["id"] for m in MODELS}
DEFAULT_MODEL = "muse-web"

_ask_lock = threading.Lock()


def _flatten_messages(messages: list[dict]) -> str:
    """把 OpenAI/Anthropic 的 messages 拍平成一段发给网页版 Muse 的文本。"""
    parts: list[str] = []
    for m in messages or []:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list): # 多段 content，只取文本段
            content = "\n".join(
                c.get("text", "") for c in content if isinstance(c, dict) and c.get("type", "text") == "text"
            )
        parts.append(f"{role}: {content}")
    parts.append("\n请只回答最后一条 user 消息，前面的内容是上下文。")
    return "\n\n".join(parts)


def _est_tokens(text: str) -> int:
    return max(1, len(text) // 4) # 粗略估算，仅用于填充 usage 字段


def make_handler(ask_fn: Callable[[str], str], api_key: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # ---------- 基础设施 ----------
        def log_message(self, fmt, *args): # 安静一点，只打到 stderr 一行
            print(f"[bridge] {self.address_string()} {fmt % args}")

        def _send_json(self, code: int, obj: dict):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, code: int, message: str):
            self._send_json(code, {"error": {"message": message, "type": "bridge_error", "code": code}})

        def _authorized(self) -> bool:
            if not api_key:
                return True
            auth = self.headers.get("Authorization", "")
            xkey = self.headers.get("x-api-key", "")
            return auth == f"Bearer {api_key}" or xkey == api_key

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")

        def _ask(self, prompt: str) -> str:
            with _ask_lock: # 网页会话只能串行
                return ask_fn(prompt)

        # ---------- 路由 ----------
        def do_GET(self):
            if self.path in ("/", "/health"):
                return self._send_json(200, {
                    "name": "muse-web-bridge", "status": "ok",
                    "base_url": "/v1", "models": [m["id"] for m in MODELS],
                })
            if self.path == "/v1/models":
                if not self._authorized():
                    return self._error(401, "API key 不对。看桥启动时打印的 key。")
                return self._send_json(200, {"object": "list", "data": MODELS})
            if self.path.startswith("/v1/models/"):
                mid = self.path.rsplit("/", 1)[-1]
                for m in MODELS:
                    if m["id"] == mid:
                        return self._send_json(200, m)
                return self._error(404, f"没有这个 model：{mid}")
            return self._error(404, "not found")

        def do_POST(self):
            if not self._authorized():
                return self._error(401, "API key 不对。看桥启动时打印的 key。")
            try:
                payload = self._read_json()
            except Exception as e:
                return self._error(400, f"JSON 解析失败：{e}")
            if self.path == "/v1/chat/completions":
                return self._chat_completions(payload)
            if self.path == "/v1/messages":
                return self._anthropic_messages(payload)
            if self.path == "/v1/code":
                return self._code(payload)
            return self._error(404, "not found")

        # ---------- 端点 ----------
        def _check_model(self, payload: dict) -> str | None:
            model = payload.get("model") or DEFAULT_MODEL
            if model not in MODEL_IDS:
                self._error(404, f"没有这个 model：{model}，可用：{sorted(MODEL_IDS)}")
                return None
            return model

        def _chat_completions(self, payload: dict):
            model = self._check_model(payload)
            if model is None:
                return
            prompt = _flatten_messages(payload.get("messages") or [])
            try:
                reply = self._ask(prompt)
            except Exception as e:
                return self._error(502, f"调用 muse.ai 网页失败：{e}")
            cid = "chatcmpl-" + uuid.uuid4().hex[:24]
            created = int(time.time())
            if payload.get("stream"):
                return self._stream(cid, created, model, reply)
            self._send_json(200, {
                "id": cid, "object": "chat.completion", "created": created, "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": reply},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": _est_tokens(prompt),
                    "completion_tokens": _est_tokens(reply),
                    "total_tokens": _est_tokens(prompt) + _est_tokens(reply),
                },
            })

        def _stream(self, cid: str, created: int, model: str, reply: str):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def chunk(content: str | None, finish=None):
                obj = {
                    "id": cid, "object": "chat.completion.chunk",
                    "created": created, "model": model,
                    "choices": [{"index": 0, "delta": ({"content": content} if content else {}),
                                 "finish_reason": finish}],
                }
                self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()

            # 网页端本身是非流式取回的，这里把完整回复切块伪流式吐出，客户端体验一致
            for i in range(0, len(reply), 200):
                chunk(reply[i:i + 200])
            chunk(None, finish="stop")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True

        def _anthropic_messages(self, payload: dict):
            model = self._check_model(payload)
            if model is None:
                return
            system = payload.get("system") or ""
            msgs = payload.get("messages") or []
            if system:
                msgs = [{"role": "system", "content": system}] + msgs
            prompt = _flatten_messages(msgs)
            try:
                reply = self._ask(prompt)
            except Exception as e:
                return self._error(502, f"调用 muse.ai 网页失败：{e}")
            self._send_json(200, {
                "id": "msg_" + uuid.uuid4().hex[:24],
                "type": "message", "role": "assistant", "model": model,
                "content": [{"type": "text", "text": reply}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": _est_tokens(prompt), "output_tokens": _est_tokens(reply)},
            })

        def _code(self, payload: dict):
            """便捷端点：{task} 进，{files:[{path,content}], raw} 出，不直接写盘。"""
            from .extract import CODE_INSTRUCTION, extract_blocks

            task = payload.get("task") or ""
            if not task:
                return self._error(400, "缺 task")
            try:
                reply = self._ask(f"{task}\n\n{CODE_INSTRUCTION}")
            except Exception as e:
                return self._error(502, f"调用 muse.ai 网页失败：{e}")
            files = [{"path": p, "content": c} for p, c in extract_blocks(reply)]
            self._send_json(200, {"files": files, "raw": reply})

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765, api_key: str | None = None,
          ask_fn: Callable[[str], str] | None = None) -> None:
    if ask_fn is None:
        from .client import MuseWebClient

        client = MuseWebClient(headless=True)
        ask_fn = client.ask
    key = api_key or secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer((host, port), make_handler(ask_fn, key))
    print("=" * 60)
    print("muse-web-bridge 已启动，填到你的工具里：")
    print(f" API base URL: http://{host}:{port}/v1")
    print(f" API key: {key}")
    print(f" Model: {DEFAULT_MODEL}（也可用 muse-spark-1.3）")
    print(" 端点: POST /v1/chat/completions | POST /v1/messages | POST /v1/code | GET /v1/models")
    print("Ctrl+C 停止。调用前确保已跑过 auth 登录。")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
