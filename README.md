# muse-web-bridge

用你自己的 muse.ai 网页版账号写代码的本地桥接工具（非官方）。

## 先说清楚

- muse.ai 网页版本身**没有官方对外 API、没有 OAuth 授权入口、没有 API Key 页面**。
  官方给开发者的编程接口只有 dev.meta.ai 的 Meta Model API，那是另一条路。
- 本工具走的是**浏览器自动化**：在你本机用 Playwright 打开 muse.ai，
  你本人手动登录一次，登录态保存在本机一个独立浏览器 profile 里，
  之后工具复用这个 profile 发消息、等回复、把代码块落盘到你的项目目录。
- 所以“授权” = 你本人登录 + 本机 profile 持久化。工具不保存你的密码、
  不经手验证码，profile 目录不要上传、不要提交 git。

## 安装（在你自己的电脑上）

```bash
cd muse-web-bridge
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## 用法

```bash
# 1) 授权：打开浏览器，你手动登录 muse.ai，登录成功后回终端按 Enter
python -m muse_bridge auth

# 2) 提问（普通问答，输出打印到终端，并存到 last_reply.md）
python -m muse_bridge ask "帮我写一个快速排序，并解释复杂度"

# 3) 写代码模式：要求 Muse 按约定格式输出文件，工具自动落盘到目标目录
python -m muse_bridge code --out ./myproject "写一个带登录的 Flask 待办应用"

# 4) 页面结构变了 / 定位不到输入框时，dump 一份结构概要用于校准 selectors.yaml
python -m muse_bridge inspect
```

## API 模式：提供 URL + key + model 给你的工具填

先 `auth` 登录，再启动服务：

```bash
python -m muse_bridge serve --port 8765
# 想固定 key： MUSE_BRIDGE_API_KEY=我自己设的key python -m muse_bridge serve
# 不设 key 时，启动时会随机生成一个并打印出来
```

启动后打印的三要素，就是填到 Cursor / Continue / 自己的脚本里的值：

| 要填的项 | 值 |
|---|---|
| API base URL | `http://127.0.0.1:8765/v1` |
| API key | 启动时打印的 key（或你用 `MUSE_BRIDGE_API_KEY` 自设的） |
| Model | `muse-web`（也可用 `muse-spark-1.3`） |

说明：这个 key 只是你本机桥的门禁，不是 Muse 账号凭证；
model 是桥的别名，实际后端是你 muse.ai 账号当前运行的模型。
不要把 `--host` 改成 `0.0.0.0` 对外暴露 —— 那等于把你的 Muse
账号借给局域网/公网任何拿到 key 的人。

端点（OpenAI 兼容为主，另带 Anthropic 兼容和一个便捷代码端点）：

- `GET  /v1/models`
- `POST /v1/chat/completions`（支持 `stream: true`，桥会切块伪流式返回）
- `POST /v1/messages`（Anthropic 格式，Claude Code 类工具可指向这里）
- `POST /v1/code`，body `{"task": "..."}`，返回 `{files: [{path, content}], raw}`

curl 验证：

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer <启动时打印的key>" \
  -H "Content-Type: application/json" \
  -d '{"model":"muse-web","messages":[{"role":"user","content":"写一个快速排序"}]}'
```

Python（OpenAI SDK）：

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8765/v1", api_key="<启动时打印的key>")
r = client.chat.completions.create(model="muse-web",
        messages=[{"role": "user", "content": "写一个快速排序"}])
print(r.choices[0].message.content)
```

注意：网页会话同一时间只能跑一个请求，桥内部是串行排队的，
并发调用会一个个执行，慢是正常的，不要开高并发压它。

## 代码落盘约定

`code` 模式会给 Muse 一段固定指令，要求它只按下面格式输出文件：

    ```path=相对路径
    文件完整内容
    ```

工具解析后写入 `--out` 目录。安全处理：
- 拒绝绝对路径和 `..` 跳出目标目录的路径；
- 已存在文件默认不覆盖，加 `--force` 才覆盖；
- 落盘前会打印将写入的文件清单。

## 已知风险（用之前先接受）

1. **脆弱**：muse.ai 改版、改 DOM，工具就可能失效，需要按 `inspect`
   的输出更新 `selectors.yaml`。`selectors.yaml` 里的选择器是按常见
   聊天页面结构写的初始值，**还没在你的登录态页面上校准过**。
2. **会话过期**：登录态过期或触发二次验证时，重新跑 `auth` 即可，
   不要把账号密码写进任何脚本/配置。
3. **额度和规则**：调用消耗的是你 muse.ai 账号本身的用量；
   这是非官方自动化方式，是否符合你账号的使用条款由你自行判断，
   建议低频、自用，不要拿去做批量/转售服务。
4. **回复等待**：Muse 在网页里干活时可能弹审批卡（比如要用工具、
   写文件）。这类卡片工具无法替你批准，`code` 模式更适合让它
   “只输出代码文本”，由本工具在你本机落盘。

## 文件

- `muse_bridge/auth.py` — 授权登录、profile 管理
- `muse_bridge/client.py` — 打开会话、发送、等待流式回复结束、提取最后一条回复
- `muse_bridge/extract.py` — 解析 `path=` 代码块并安全落盘
- `muse_bridge/server.py` — 本地 API 服务（标准库实现，OpenAI/Anthropic 兼容）
- `muse_bridge/cli.py` — `auth / ask / code / inspect / serve` 命令
- `selectors.yaml` — 页面选择器，校准只改这个文件
