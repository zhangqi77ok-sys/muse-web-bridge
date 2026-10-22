from __future__ import annotations

import argparse
import json
from pathlib import Path

from .auth import run_auth
from .client import MuseWebClient, inspect_page, load_config
from .extract import CODE_INSTRUCTION, write_blocks

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(prog="muse_bridge", description="muse.ai 网页桥接（非官方）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth", help="打开浏览器，手动登录 muse.ai 并保存登录态")

    p_ask = sub.add_parser("ask", help="发一条消息，打印回复")
    p_ask.add_argument("prompt")
    p_ask.add_argument("--show-browser", action="store_true", help="显示浏览器窗口")

    p_code = sub.add_parser("code", help="让 Muse 输出代码并落盘到 --out 目录")
    p_code.add_argument("task", help="开发任务描述")
    p_code.add_argument("--out", default="./out", help="代码输出目录")
    p_code.add_argument("--force", action="store_true", help="允许覆盖已存在文件")
    p_code.add_argument("--show-browser", action="store_true")

    sub.add_parser("inspect", help="dump 页面结构概要，用于校准 selectors.yaml")

    p_serve = sub.add_parser("serve", help="启动本地 API 服务（OpenAI/Anthropic 兼容）")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--api-key", default=None,
                         help="自定义 API key；不填则随机生成并打印（也可用环境变量 MUSE_BRIDGE_API_KEY）")

    args = ap.parse_args()
    cfg = load_config()

    if args.cmd == "auth":
        run_auth(cfg["base_url"])
    elif args.cmd == "ask":
        reply = MuseWebClient(headless=not args.show_browser).ask(args.prompt)
        (ROOT / "last_reply.md").write_text(reply, encoding="utf-8")
        print(reply)
    elif args.cmd == "code":
        prompt = f"{args.task}\n\n{CODE_INSTRUCTION}"
        reply = MuseWebClient(headless=not args.show_browser).ask(prompt)
        (ROOT / "last_reply.md").write_text(reply, encoding="utf-8")
        files = write_blocks(reply, args.out, force=args.force)
        print(f"完成：共写入 {len(files)} 个文件到 {args.out}")
        if not files:
            print("回复里没有解析到 path= 代码块，原文已存 last_reply.md，请检查格式。")
    elif args.cmd == "serve":
        import os

        from .server import serve

        serve(host=args.host, port=args.port,
              api_key=args.api_key or os.environ.get("MUSE_BRIDGE_API_KEY"))
    elif args.cmd == "inspect":
        dump = inspect_page()
        out = ROOT / "inspect_dump.json"
        out.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结构已存 {out}，按它修改 selectors.yaml 后再试 ask。")


if __name__ == "__main__":
    main()
