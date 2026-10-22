import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from muse_bridge.server import make_handler

KEY = "test-key"


def fake_ask(prompt: str) -> str:
    return "```path=hello.py\nprint('hi')\n``` 这是模拟回复"


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fake_ask, KEY))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, method, path, body=None, key=KEY):
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_models(self):
        status, body = self.req("GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertIn("muse-web", [m["id"] for m in body["data"]])

    def test_auth_required(self):
        status, _ = self.req("GET", "/v1/models", key="wrong")
        self.assertEqual(status, 401)

    def test_chat_completions(self):
        status, body = self.req("POST", "/v1/chat/completions",
                                {"model": "muse-web",
                                 "messages": [{"role": "user", "content": "写个hello"}]})
        self.assertEqual(status, 200)
        self.assertIn("模拟回复", body["choices"][0]["message"]["content"])

    def test_unknown_model_404(self):
        status, _ = self.req("POST", "/v1/chat/completions",
                             {"model": "gpt-x", "messages": []})
        self.assertEqual(status, 404)

    def test_anthropic_messages(self):
        status, body = self.req("POST", "/v1/messages",
                                {"model": "muse-spark-1.3",
                                 "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 200)
        self.assertEqual(body["content"][0]["type"], "text")

    def test_code_endpoint_extracts_files(self):
        status, body = self.req("POST", "/v1/code", {"task": "写hello.py"})
        self.assertEqual(status, 200)
        self.assertEqual(body["files"][0]["path"], "hello.py")


if __name__ == "__main__":
    unittest.main()
