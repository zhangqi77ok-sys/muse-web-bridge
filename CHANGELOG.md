# Changelog

## 0.2.0 - 2026-10-22

- 新增本地 API 服务 `serve`：OpenAI 兼容 `POST /v1/chat/completions`
  （含伪流式）、Anthropic 兼容 `POST /v1/messages`、便捷代码端点
  `POST /v1/code`、`GET /v1/models`；启动时打印 base URL / API key / model。
- 新增打包配置 `pyproject.toml`，可 `pip install .` 后用 `muse-bridge` 命令。
- 新增单元测试（tests/）与 GitHub Actions CI。
- 新增 examples/ 示例与 MIT LICENSE。

## 0.1.0 - 2026-10-22

- 首版：Playwright 浏览器自动化桥接 muse.ai 网页版。
- 命令：`auth`（手动登录、持久化 profile）、`ask`、`code`（代码块解析落盘，
  带路径穿越防护）、`inspect`（dump 页面结构用于校准 selectors.yaml）。
