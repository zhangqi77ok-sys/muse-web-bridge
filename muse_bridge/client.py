"""调用：在已登录的 muse.ai 页面里发一条消息，等流式回复结束，提取文本。

所有页面定位都走 selectors.yaml。页面改版时先用 inspect 命令看结构，
只改 yaml，不要改这里的逻辑。
"""
from __future__ import annotations

import time
from pathlib import Path

import yaml
from playwright.sync_api import Page

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    with open(ROOT / "selectors.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _first_visible(page: Page, selectors: list[str], timeout_ms: int = 3000):
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout_ms)
            return loc
        except Exception:
            continue
    return None


class NotLoggedIn(RuntimeError):
    pass


class MuseWebClient:
    def __init__(self, headless: bool = True):
        self.cfg = load_config()
        self.headless = headless

    def ask(self, prompt: str, timeout_s: int = 600) -> str:
        from playwright.sync_api import sync_playwright

        from .auth import launch_context

        cfg = self.cfg
        with sync_playwright() as pw:
            ctx = launch_context(pw, headless=self.headless)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(cfg["base_url"])
            composer = _first_visible(page, cfg["composer"], timeout_ms=10000)
            if composer is None:
                ctx.close()
                raise NotLoggedIn(
                    "找不到消息输入框：可能未登录或登录已过期。"
                    "先运行：python -m muse_bridge auth"
                )
            composer.click()
            composer.fill(prompt) if composer.evaluate(
                "e => e.tagName === 'TEXTAREA'"
            ) else page.keyboard.type(prompt)

            send = _first_visible(page, cfg.get("send_button") or [], timeout_ms=1500)
            if send is not None:
                send.click()
            else:
                page.keyboard.press("Enter")

            reply = self._wait_reply(page, timeout_s)
            ctx.close()
            return reply

    def _wait_reply(self, page: Page, timeout_s: int) -> str:
        """等流式结束：回复文本连续两次不变且无 Stop 按钮，视为完成。"""
        cfg = self.cfg
        deadline = time.time() + timeout_s
        last_text, stable = "", 0
        while time.time() < deadline:
            streaming = _first_visible(
                page, cfg.get("streaming_indicator") or [], timeout_ms=300
            )
            msgs = page.locator(", ".join(cfg["message"]))
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
        raise TimeoutError(f"{timeout_s}s 内没有等到回复，请用 inspect 校准选择器。")


def inspect_page() -> dict:
    """打开已登录页面，dump 输入框/按钮/消息容器的结构概要，用于校准。"""
    from playwright.sync_api import sync_playwright

    from .auth import launch_context

    cfg = load_config()
    with sync_playwright() as pw:
        ctx = launch_context(pw, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(cfg["base_url"])
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
