"""把 Muse 回复里的 ```path=相对路径 代码块安全地写到目标目录。"""
from __future__ import annotations

import re
from pathlib import Path

BLOCK = re.compile(r"```path=([^\n`]+)\n(.*?)```", re.DOTALL)

CODE_INSTRUCTION = """请直接输出完整代码，不要解释。每个文件用一个代码块，格式严格如下：

```path=相对路径
文件完整内容
```

不要输出格式之外的任何文字。路径必须是相对路径，不要用绝对路径。"""


def extract_blocks(reply: str) -> list[tuple[str, str]]:
    return [(m.group(1).strip(), m.group(2)) for m in BLOCK.finditer(reply)]


def write_blocks(reply: str, out_dir: str | Path, force: bool = False) -> list[Path]:
    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
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
