import tempfile
import unittest
from pathlib import Path

from muse_bridge.extract import extract_blocks, write_blocks

REPLY = """好的：

```path=src/main.py
print("hello")
```

```path=docs/readme.txt
text body
```
"""


class ExtractBlocksTest(unittest.TestCase):
    def test_extracts_path_and_content(self):
        blocks = extract_blocks(REPLY)
        self.assertEqual([p for p, _ in blocks], ["src/main.py", "docs/readme.txt"])
        self.assertEqual(blocks[0][1], 'print("hello")\n')

    def test_no_blocks(self):
        self.assertEqual(extract_blocks("没有任何代码块"), [])


class WriteBlocksTest(unittest.TestCase):
    def test_writes_inside_out_dir(self):
        with tempfile.TemporaryDirectory() as d:
            written = write_blocks(REPLY, d)
            self.assertEqual(len(written), 2)
            self.assertEqual(Path(d, "src/main.py").read_text(encoding="utf-8"), 'print("hello")\n')

    def test_rejects_path_traversal(self):
        evil = "```path=../evil.py\nbad\n```\n```path=/abs.py\nbad\n```"
        with tempfile.TemporaryDirectory() as d:
            written = write_blocks(evil, d)
            self.assertEqual(written, [])
            self.assertFalse(Path(d).parent.joinpath("evil.py").exists())

    def test_does_not_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as d:
            write_blocks(REPLY, d)
            again = write_blocks(REPLY, d)
            self.assertEqual(again, [])
            forced = write_blocks(REPLY, d, force=True)
            self.assertEqual(len(forced), 2)


if __name__ == "__main__":
    unittest.main()
