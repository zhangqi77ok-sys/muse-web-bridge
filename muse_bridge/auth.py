"""授权：打开可见浏览器，用户本人手动登录 muse.ai。

登录态保存在本机独立 profile 目录（默认 ~/.muse-web-bridge-profile），
工具不保存密码、不处理验证码。profile 目录切勿上传或提交 git。
"""
from __future__ import annotations

import os
from pathlib import Path

PROFILE_DIR = Path(
    os.environ.get(
        "MUSE_BRIDGE_PROFILE",
        str(Path.home() / ".muse-web-bridge-profile"),
    )
)


def launch_context(playwright, headless: bool = False):
    """用持久化 profile 启动 Chromium，登录态因此能跨次复用。"""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        viewport={"width": 1280, "height": 900},
    )


def run_auth(base_url: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = launch_context(pw, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(base_url)
        print("浏览器已打开 muse.ai，请在浏览器里完成登录。")
        print("登录成功、能看到聊天输入框后，回到这里按 Enter 保存登录态…")
        input()
        ctx.close()
    print(f"登录态已保存在 {PROFILE_DIR}，之后 ask/code 会自动复用。")
