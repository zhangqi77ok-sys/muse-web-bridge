#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""muse_bridge.py — 单文件版 muse.ai 网页桥接工具（非官方）

一个脚本解决：授权登录、提问、写代码落盘、本地 API 服务。
第一次运行会自动安装依赖（playwright）和 Chromium 浏览器，之后直接用。

用法：
    python muse_bridge.py auth                    # 打开浏览器，手动登录 muse.ai（只需一次）
    python muse_bridge.py ask "帮我写一个快速排序"
    python muse_bridge.py code --out ./myproject "写一个带登录的 Flask 待办应用"
    python muse_bridge.py serve                   # 启动本地 API，终端会打印 URL / key / model
    python muse_bridge.py inspect                 # 页面改版时 dump 结构，用于校准 SELECTORS

API 模式三要素（serve 启动时也会打印）：
    API base URL : http://127.0.0.1:8765/v1
    API key      : 启动时随机生成打印出来，或用环境变量 MUSE_BRIDGE_API_KEY 自设
    Model        : muse-web（也接受 muse-spark-1.3）

注意：muse.ai 网页版没有官方 API，这是浏览器自动化方案，复用你本人的登录态。
登录态只存在本机 profile 目录（~/.muse-web-bridge-profile），脚本不保存你的密码。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

# ============================== 配置 ==============================

BASE_URL = "https://muse.ai"
PROFILE_DIR = Path(
    os.environ.get("MUSE_BRIDGE_PROFILE", str(Path.home() / ".muse-web-bridge-profile"))
)
SETUP_MARKER = Path.home() / ".muse-bridge-setup-done"

# 页面选择器：按常见聊天页面结构填写的初始值，尚未在登录态页面校准。
# 如定位失败，先跑 inspect，按 dump 结果改这里。
SELECTORS = {
    "composer": ["div[contenteditable='true']", "textarea"],
    "send_button": ["button[aria-label*='Send']", "button[aria-label*='发送']"],
    "message": ["[data-message-author-role='assistant']", "article"],
    "streaming_indicator": ["button[aria-label*='Stop']", "button[aria-label*='停止']"],
}

MODELS = [
    {"id": "muse-web", "object": "model", "created": 0, "owned_by": "muse-web-bridge"},
    {"id": "muse-spark-1.3", "object": "model", "created": 0, "owned_by": "muse-web-bridge"},
]
MODEL_IDS = {m["id"] for m in MODELS}
DEFAULT_MODEL = "muse-web"

# ====================== 依赖自举（首次运行自动装） ======================


def ensure_deps() -> None:
    """缺 playwright 时自动 pip 安装，并安装 Chromium（只做一次）。"""
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("首次运行：正在安装 playwright …")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright>=1.44"])
    if not SETUP_MARKER.exists():
        print("首次运行：正在安装 Chromium 浏览器（约 150MB，只装一次）…")
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        SETUP_MARKER.write_text("ok", encoding="utf-8")


# ============================== 授权登录 ==============================


def _launch_context(pw, headless: bool):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        viewport={"width": 1280, "height": 900},
    )


def run_auth() -> None:
    ensure_deps()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = _launch_context(pw, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(BASE_URL)
        print("浏览器已打开 muse.ai，请在浏览器里完成登录。")
        print("登录成功、能看到聊天输入框后，回到这里按 Enter 保存登录态…")
        input()
        ctx.close()
    print(f"登录态已保存在 {PROFILE_DIR}，之后 ask/code/serve 会自动复用。")


# ============================== 网页调用 ==============================


class NotLoggedIn(RuntimeError):
    pass


def _first_visible(page, selectors, timeout_ms: int = 3000):
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout_ms)
            return loc
        except Exception:
            continue
    return None


def ask_muse(prompt: str, headless: bool = True, timeout_s: int = 600) -> str:
    """在已登录的 muse.ai 页面发一条消息，等回复结束，返回最后一条回复文本。"""
    ensure_deps()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = _launch_context(pw, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(BASE_URL)
        composer = _first_visible(page, SELECTORS["composer"], timeout_ms=10000)
        if composer is None:
            ctx.close()
            raise NotLoggedIn("找不到消息输入框：可能未登录或登录已过期。先运行：python muse_bridge.py auth")
        composer.click()
        if composer.evaluate("e => e.tagName === 'TEXTAREA'"):
            composer.fill(prompt)
        else:
            page.keyboard.type(prompt)
        send = _first_visible(page, SELECTORS["send_button"], timeout_ms=1500)
        if send is not None:
            send.click()
        else:
            page.keyboard.press("Enter")
        reply = _wait_reply(page, timeout_s)
        ctx.close()
        return reply


def _wait_reply(page, timeout_s: int) -> str:
    """等流式结束：回复文本连续两次不变且无 Stop 按钮，视为完成。"""
    deadline = time.time() + timeout_s
    last_text, stable = "", 0
    while time.time() < deadline:
        streaming = _first_visible(page, SELECTORS["streaming_indicator"], timeout_ms=300)
        msgs = page.locator(", ".join(SELECTORS["message"]))
        text = ""
        if msgs.count():
            try:
                text = msgs.last.inner_text(timeout=2000)
            except Exception:
                text = ""
        if text and text == last_text and streaming is None:
            stable += 1
            if stable >= 2:
                return text
        else:
            stable = 0
        last_text = text
        time.sleep(2)
    if last_text:
        return last_text
    raise TimeoutError(f"{timeout_s}s 内没有等到回复，请用 inspect 校准 SELECTORS。")


def inspect_page() -> dict:
    """打开已登录页面，dump 输入框/按钮/消息容器结构概要，用于校准 SELECTORS。"""
    ensure_deps()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = _launch_context(pw, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(BASE_URL)
        page.wait_for_timeout(5000)
        dump = page.evaluate(
            """() => {
              const pick = (els, n=30) => Array.from(els).slice(0, n).map(e => ({
                tag: e.tagName,
                role: e.getAttribute('role'),
                aria: e.getAttribute('aria-label'),
                data: Array.from(e.attributes).filter(a => a.name.startsWith('data-'))
                        .map(a => a.name + '=' + a.value),
                cls: (e.className && e.className.baseVal !== undefined)
                       ? e.className.baseVal : String(e.className).slice(0, 80),
                editable: e.getAttribute('contenteditable'),
              }));
              return {
                url: location.href, title: document.title,
                textareas: pick(document.querySelectorAll('textarea')),
                editables: pick(document.querySelectorAll("[contenteditable='true']")),
                buttons: pick(document.querySelectorAll('button'), 50),
                articles: pick(document.querySelectorAll('article')),
              };
            }"""
        )
        ctx.close()
        return dump


# ============================== 代码落盘 ==============================

BLOCK = re.compile(r"```path=([^\n`]+)\n(.*?)```", re.DOTALL)

CODE_INSTRUCTION = """请直接输出完整代码，不要解释。每个文件用一个代码块，格式严格如下：

```path=相对路径
文件完整内容
```

不要输出格式之外的任何文字。路径必须是相对路径，不要用绝对路径。"""


def extract_blocks(reply: str):
    return [(m.group(1).strip(), m.group(2)) for m in BLOCK.finditer(reply)]


def write_blocks(reply: str, out_dir, force: bool = False):
    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    written = []
    for rel, content in extract_blocks(reply):
        target = (root / rel).resolve()
        # 安全：拒绝跳出目标目录的路径（../、符号链接等）
        if root not in target.parents and target != root:
            print(f"跳过不安全路径：{rel}")
            continue
        if target.exists() and not force:
            print(f"跳过已存在文件（加 --force 才覆盖）：{rel}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)
        print(f"已写入：{target}")
    return written


# ============================== 本地 API 服务 ==============================

_ask_lock = threading.Lock()


def _flatten_messages(messages) -> str:
    """把 OpenAI/Anthropic 的 messages 拍平成一段发给网页版 Muse 的文本。"""
    parts = []
    for m in messages or []:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):  # 多段 content，只取文本段
            content = "\n".join(
                c.get("text", "") for c in content
                if isinstance(c, dict) and c.get("type", "text") == "text"
            )
        parts.append(f"{role}: {content}")
    parts.append("\n请只回答最后一条 user 消息，前面的内容是上下文。")
    return "\n\n".join(parts)


def _est_tokens(text: str) -> int:
    return max(1, len(text) // 4)  # 粗略估算，仅用于填充 usage 字段


def make_handler(ask_fn: Callable[[str], str], api_key: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
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
            with _ask_lock:  # 网页会话只能串行
                return ask_fn(prompt)

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

        def _check_model(self, payload: dict):
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

            def chunk(content, finish=None):
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


def serve(host: str = "127.0.0.1", port: int = 8765, api_key: str | None = None) -> None:
    ensure_deps()
    key = api_key or secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer((host, port), make_handler(ask_muse, key))
    print("=" * 60)
    print("muse-web-bridge 已启动，填到你的工具里：")
    print(f"  API base URL : http://{host}:{port}/v1")
    print(f"  API key      : {key}")
    print(f"  Model        : {DEFAULT_MODEL}（也可用 muse-spark-1.3）")
    print("  端点         : POST /v1/chat/completions | POST /v1/messages | POST /v1/code | GET /v1/models")
    print("Ctrl+C 停止。调用前确保已跑过 auth 登录。")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


# ============================== 命令行入口 ==============================

HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser(prog="muse_bridge.py", description="muse.ai 网页桥接（非官方，单文件版）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth", help="打开浏览器，手动登录 muse.ai 并保存登录态（首次自动装依赖）")

    p_ask = sub.add_parser("ask", help="发一条消息，打印回复")
    p_ask.add_argument("prompt")
    p_ask.add_argument("--show-browser", action="store_true", help="显示浏览器窗口")

    p_code = sub.add_parser("code", help="让 Muse 输出代码并落盘到 --out 目录")
    p_code.add_argument("task", help="开发任务描述")
    p_code.add_argument("--out", default="./out", help="代码输出目录")
    p_code.add_argument("--force", action="store_true", help="允许覆盖已存在文件")
    p_code.add_argument("--show-browser", action="store_true")

    sub.add_parser("inspect", help="dump 页面结构概要到 inspect_dump.json，用于校准 SELECTORS")

    p_serve = sub.add_parser("serve", help="启动本地 API 服务（OpenAI/Anthropic 兼容）")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--api-key", default=None,
                         help="自定义 API key；不填则随机生成并打印（也可用环境变量 MUSE_BRIDGE_API_KEY）")

    args = ap.parse_args()

    if args.cmd == "auth":
        run_auth()
    elif args.cmd == "ask":
        reply = ask_muse(args.prompt, headless=not args.show_browser)
        (HERE / "last_reply.md").write_text(reply, encoding="utf-8")
        print(reply)
    elif args.cmd == "code":
        reply = ask_muse(f"{args.task}\n\n{CODE_INSTRUCTION}", headless=not args.show_browser)
        (HERE / "last_reply.md").write_text(reply, encoding="utf-8")
        files = write_blocks(reply, args.out, force=args.force)
        print(f"完成：共写入 {len(files)} 个文件到 {args.out}")
        if not files:
            print("回复里没有解析到 path= 代码块，原文已存 last_reply.md，请检查格式。")
    elif args.cmd == "inspect":
        dump = inspect_page()
        out = HERE / "inspect_dump.json"
        out.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结构已存 {out}，按它修改脚本里的 SELECTORS 后再试 ask。")
    elif args.cmd == "serve":
        serve(host=args.host, port=args.port,
              api_key=args.api_key or os.environ.get("MUSE_BRIDGE_API_KEY"))


if __name__ == "__main__":
    main()
