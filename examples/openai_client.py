"""示例：启动桥的 API 服务后，用 OpenAI SDK 通过本机桥调用 muse.ai 网页版。

先在另一个终端：
    python -m muse_bridge auth # 首次登录
    MUSE_BRIDGE_API_KEY=dev-key python -m muse_bridge serve --port 8765

再运行本文件：pip install openai && python examples/openai_client.py
"""
import os

from openai import OpenAI

client = OpenAI(
    base_url=os.environ.get("MUSE_BRIDGE_URL", "http://127.0.0.1:8765/v1"),
    api_key=os.environ.get("MUSE_BRIDGE_API_KEY", "dev-key"),
)

resp = client.chat.completions.create(
    model="muse-web",
    messages=[{"role": "user", "content": "用 Python 写一个快速排序，并给出完整代码"}],
)
print(resp.choices[0].message.content)
