"""Check the Chat Completions wire format, recovery, and real browser CLI."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from openai import OpenAI

from agent import Actions, WebAgent, run
from chat_completions import ChatCompletions

IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"


def call(name, arguments, identifier):
    return {"id": identifier, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments)}}


def reply(calls=None, *, content=None, reason=None):
    return {"id": "chat_test", "object": "chat.completion", "created": 1, "model": "test",
            "choices": [{"index": 0, "finish_reason": reason or ("tool_calls" if calls else "stop"),
                         "message": {"role": "assistant", "content": content, "tool_calls": calls}}]}


class Fixture(BaseHTTPRequestHandler):
    requests, replies = [], []

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.requests.append((self.path, json.loads(
            self.rfile.read(int(self.headers["Content-Length"])))))
        body = json.dumps(self.replies.pop(0)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ChatCompletionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        Fixture.requests, Fixture.replies = [], []
        self.recorded = []
        self.agent = WebAgent(action_space={"record": self.record, "finish": Actions.finish})
        self.agent.observe = lambda: (json.dumps({"viewport": {"width": 1280, "height": 800}}), IMAGE)
        self.client = OpenAI(api_key="local-test", base_url=self.url, max_retries=0)
        self.addCleanup(self.client.close)

    def record(self, text: str) -> None:
        self.recorded.append(text)

    def run_task(self, steps=3):
        return run(self.agent, "Test", ChatCompletions(self.client), "test", "Instructions",
                   max_steps=steps, max_output_tokens=512)

    def test_tools_images_and_error_results_across_turns(self):
        Fixture.replies = [reply([
            call("record", {"text": "saved"}, "first"),
            call("unknown", {}, "second")], content="Trying two actions."),
            reply([call("finish", {}, "invalid")]),
            reply([call("finish", {"message": "Done"}, "last")])]
        self.assertEqual(self.run_task(), "Done")
        self.assertEqual(self.recorded, ["saved"])
        for path, request in Fixture.requests:
            self.assertEqual(path, "/v1/chat/completions")
            self.assertEqual(request["max_tokens"], 512)
            self.assertFalse(request["parallel_tool_calls"])
            self.assertNotIn("include", request)
            self.assertNotIn("store", request)
            self.assertNotIn("input", request)
            self.assertEqual(request["tools"][0]["function"]["name"], "record")
        messages = Fixture.requests[-1][1]["messages"]
        tools = [m for m in messages if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tools], ["first", "second", "invalid"])
        self.assertEqual([json.loads(m["content"].splitlines()[0])["state"] for m in tools],
                         ["success", "error", "error"])
        assistant = [m for m in messages if m.get("tool_calls")]
        self.assertEqual([c["id"] for c in assistant[0]["tool_calls"]], ["first", "second"])
        for message in messages:
            for part in message.get("content", []) or []:
                if isinstance(part, dict) and part["type"] == "image_url":
                    self.assertEqual(message["role"], "user")
                    self.assertEqual(part["image_url"], {"url": IMAGE, "detail": "auto"})
        self.assertIn({"role": "assistant", "content": "Trying two actions."}, messages)

    def test_truncated_tools_are_not_executed_or_replayed(self):
        Fixture.replies = [reply([call("record", {"text": "unsafe"}, "partial")], reason="length"),
                           reply([call("finish", {"message": "Done"}, "valid")])]
        self.assertEqual(self.run_task(2), "Done")
        self.assertEqual(self.recorded, [])
        messages = Fixture.requests[-1][1]["messages"]
        self.assertFalse(any(m.get("tool_calls") for m in messages))
        self.assertIn("Retry with a shorter response", messages[-1]["content"][0]["text"])

    def test_text_only_response_gets_feedback(self):
        Fixture.replies = [reply(content=[{"type": "text", "text": "Finished."}]),
                           reply([call("finish", {"message": "Verified"}, "last")])]
        self.assertEqual(self.run_task(2), "Verified")
        messages = Fixture.requests[-1][1]["messages"]
        self.assertEqual(messages[-2], {"role": "assistant", "content": "Finished."})
        self.assertIn("Call finish", messages[-1]["content"][0]["text"])

    def test_blocked_completion_reports_failure(self):
        Fixture.replies = [reply(reason="content_filter")]
        with self.assertRaisesRegex(RuntimeError, "content_filter"):
            self.run_task(1)
        self.assertEqual(self.recorded, [])

    def test_cli_selects_chat_api_with_real_browser(self):
        Fixture.replies = [reply([call("finish", {"message": "CLI works"}, "last")])]
        with tempfile.TemporaryDirectory(prefix="mini-chat-cli-") as profile:
            environment = dict(os.environ, OPENAI_API_KEY="local-test", OPENAI_BASE_URL=self.url)
            result = subprocess.run(
                [sys.executable, "agent.py", "--api", "chat-completions", "--model", "test",
                 "--profile", profile, "--max-steps", "1", "--cursor", "Test"],
                cwd=Path(__file__).resolve().parents[1], env=environment,
                text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CLI works", result.stdout)
        path, request = Fixture.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        image = request["messages"][2]["content"][1]["image_url"]["url"]
        self.assertTrue(image.startswith("data:image/jpeg;base64,"))


if __name__ == "__main__":
    unittest.main()
