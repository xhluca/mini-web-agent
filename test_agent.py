"""Real Chromium integration tests and a local Responses API server; no API key needed."""

import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

from openai import OpenAI
from playwright.sync_api import Error

from agent import Actions, WebAgent, browser_endpoint, build_tools

HTML = """<!doctype html><html><body>
<h1>Workshop signup</h1>
<form onsubmit="event.preventDefault(); document.querySelector('#result').textContent =
 'Registered: ' + this.email.value + ' / ' + this.track.value;">
<label>Email <input name="email" required></label>
<label>Track <select name="track"><option>Art</option><option>Robotics</option></select></label>
<label><input type="checkbox" required>Agree to terms</label>
<button>Register</button></form><p id="result" role="status"></p>
<input type="file" aria-label="Upload">
<button onclick="alert('Hello')">Dialog</button>
<a href="/popup" target="_blank">Popup</a>
<a href="/download" download="note.txt">Download</a>
<iframe title="Frame" srcdoc="<button onclick='this.textContent=42'>Inside</button>"></iframe>
<div style="height:1500px">Scroll space</div>
</body></html>"""


class Fixture(BaseHTTPRequestHandler):
    requests = []
    replies = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = b"downloaded" if self.path == "/download" else HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain" if self.path == "/download" else "text/html")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append((self.path, request))
        output = self.replies.pop(0)
        response = dict(id="resp_test", object="response", created_at=1, status="completed",
                        model="test", output=output, error=None, incomplete_details=None)
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def tool_call(name, arguments=None, call_id="call_1"):
    return dict(type="function_call", id="fc_" + call_id, call_id=call_id,
                name=name, arguments=json.dumps(arguments or {}), status="completed")


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="mini-web-agent-test-")
        self.agent = WebAgent(self.folder.name).launch().connect()
        Actions.navigate(self.agent.page, self.url)

    def tearDown(self):
        if browser_endpoint(self.agent.profile):
            self.agent.connect().shutdown()
        self.folder.cleanup()

    def act(self, name, **arguments):
        result = self.agent.act(name, arguments)
        self.assertFalse(isinstance(result, dict) and "error" in result, result)
        return result

    def point(self, locator):
        box = locator.bounding_box()
        return dict(x=box["x"] + box["width"] / 2, y=box["y"] + box["height"] / 2)

    def test_coordinate_form_keyboard_and_observation(self):
        page = self.agent.page
        self.act("click", **self.point(page.get_by_label("Email")))
        self.act("type_text", text="agent@example.com")
        self.act("press_key", key="Tab")
        self.act("press_key", key="ArrowDown")
        self.act("press_key", key="Tab")
        self.act("press_key", key="Space")
        self.act("click", **self.point(page.get_by_role("button", name="Register")))
        self.assertEqual(page.get_by_role("status").inner_text(),
                         "Registered: agent@example.com / Robotics")
        self.act("click", **self.point(page.get_by_label("Email")))
        self.act("key_down", key="Shift")
        self.act("press_key", key="ArrowLeft")
        self.act("key_up", key="Shift")
        self.act("press_key", key="ControlOrMeta+A")
        self.act("type_text", text="replaced@example.com")
        self.assertEqual(page.get_by_label("Email").input_value(), "replaced@example.com")
        self.act("click", **self.point(page.frames[1].get_by_role("button")))
        self.assertEqual(page.frames[1].get_by_role("button").inner_text(), "42")
        self.act("hover", x=100, y=500)
        self.act("scroll", dx=0, dy=400)
        page.wait_for_function("scrollY > 0")
        state, image = self.agent.observe()
        self.assertEqual(set(json.loads(state)), {"active_tab", "tabs", "viewport"})
        self.assertTrue(base64.b64decode(image.split(",")[1]).startswith(b"\xff\xd8"))
        self.assertIn("Screenshot follows", self.act("screenshot"))

    def test_mouse_events(self):
        self.agent.page.set_content("""<div style='width:600px;height:600px'>Target</div>
        <script>window.events=[];
        for (const name of ['dblclick','contextmenu','mousemove','mousedown','mouseup'])
          document.addEventListener(name, e => {events.push(name); e.preventDefault()});
        </script>""")
        self.act("hover", x=50, y=50)
        self.act("double_click", x=50, y=50)
        self.act("right_click", x=50, y=50)
        self.act("mouse_down")
        self.act("hover", x=100, y=100)
        self.act("mouse_up")
        self.act("drag", x1=100, y1=100, x2=200, y2=200)
        events = self.agent.page.evaluate("events")
        self.assertTrue({"dblclick", "contextmenu", "mousemove", "mousedown", "mouseup"}
                        .issubset(events))
        self.assertEqual(events.count("mousedown"), events.count("mouseup"))

    def test_tabs_popups_indices_and_navigation(self):
        self.assertEqual(self.act("list_tabs")[0]["index"], 0)
        self.assertEqual(self.act("new_tab", url=self.url + "/second"), 1)
        self.assertEqual(self.act("new_tab", url=self.url + "/third"), 2)
        tabs = self.act("close_tab", index=1)
        self.assertEqual([t["index"] for t in tabs], [0, 1])
        self.assertTrue(tabs[1]["url"].endswith("/third"))
        self.assertEqual(json.loads(self.agent.observe()[0])["active_tab"], 1)
        self.act("switch_tab", index=0)
        self.act("click", **self.point(self.agent.page.get_by_text("Popup", exact=True)))
        self.act("wait", seconds=0.1)
        tabs = self.act("list_tabs")
        popup = next(t["index"] for t in tabs if t["url"].endswith("/popup"))
        self.assertEqual(next(t["index"] for t in tabs if t["active"]), 0)
        self.act("switch_tab", index=popup)
        self.act("navigate", url=self.url + "/next")
        self.act("back")
        self.assertTrue(self.agent.page.url.endswith("/popup"))
        self.act("forward")
        self.act("reload")
        self.assertTrue(self.agent.page.url.endswith("/next"))
        for _ in range(3):
            tabs = self.act("close_tab", index=0)
        self.assertEqual(len(tabs), 1)
        self.assertEqual(tabs[0]["index"], 0)
        self.assertEqual(tabs[0]["url"], "about:blank")
        self.agent.page.close()
        self.assertEqual(len(json.loads(self.agent.observe()[0])["tabs"]), 1)

    def test_restricted_dispatch(self):
        invalid = [
            ("__getattribute__", {"name": "page"}), ("shutdown", {}), ("launch", {}),
            ("connect", {}), ("tools", {}),
            ("evaluate", {"expression": "document.title"}), ("run_browser", {"code": "1+1"}),
            ("navigate", {"url": "javascript:alert(1)"}),
            ("navigate", {"url": "file:///etc/passwd"}),
            ("new_tab", {"url": "data:text/html,hello"}),
            ("click", {"x": True, "y": 1}), ("click", {"x": "1", "y": 1}),
            ("click", {"x": float("nan"), "y": 1}), ("click", {"x": -1, "y": 1}),
            ("click", {"x": 1280, "y": 1}), ("click", {"x": 1}),
            ("click", {"x": 1, "y": 1, "page": "forbidden"}), ("click", []),
            ("wait", {"seconds": 11}), ("wait", {"seconds": -1}),
            ("screenshot", {"path": "/tmp/forbidden.png"}), ("switch_tab", {"index": 99}),
            ("switch_tab", {"index": -1}), ("switch_tab", {"index": "0"}),
            ("switch_tab", {"index": True}),
        ]
        for name, arguments in invalid:
            with self.subTest(name=name, arguments=arguments):
                self.assertIn("error", self.agent.act(name, arguments))
        self.assertEqual(len(Actions.list_tabs(self.agent.page)), 1)
        from agent import ACTIONS
        self.assertEqual({t["name"] for t in build_tools()}, set(ACTIONS))
        for tool in build_tools():
            self.assertNotIn("page", tool["parameters"]["properties"])
            self.assertNotIn("self", tool["parameters"]["properties"])
        self.assertNotIn("exec(", Path(__file__).with_name("agent.py").read_text())

    def test_launch_and_connect_are_separate(self):
        self.agent.shutdown()
        with self.assertRaises(Error):
            self.agent.connect()
        self.assertIsNone(browser_endpoint(self.agent.profile))
        self.agent.launch()
        self.assertIsNone(self.agent.browser)
        self.assertTrue(browser_endpoint(self.agent.profile))
        self.agent.connect()
        self.agent.disconnect()
        self.assertTrue(browser_endpoint(self.agent.profile))
        self.agent.connect().shutdown()
        self.assertIsNone(browser_endpoint(self.agent.profile))

    def test_random_and_explicit_ports(self):
        from urllib.request import urlopen

        port = self.agent.cdp_port
        self.assertGreater(port, 0)
        with urlopen(f"http://127.0.0.1:{port}/json/version") as response:
            self.assertEqual(response.status, 200)
        self.agent.shutdown()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            chosen = probe.getsockname()[1]
        self.agent = WebAgent(self.folder.name, cdp_port=chosen).launch().connect()
        self.assertEqual(self.agent.cdp_port, chosen)
        self.agent.disconnect()
        other = WebAgent(self.agent.profile).connect()
        try:
            self.assertEqual(other.cdp_port, chosen)
            self.assertIn(f":{chosen}/", browser_endpoint(other.profile))
        finally:
            other.shutdown()
            self.agent.process.wait(timeout=5)

    def test_invalid_or_occupied_port(self):
        self.agent.shutdown()
        for port in [-1, 65536, True, "9222"]:
            with self.assertRaises(ValueError):
                WebAgent(self.folder.name, cdp_port=port)
        with self.assertRaises(OSError):
            WebAgent(self.folder.name, cdp_port=self.server.server_port).launch()
        self.assertIsNone(browser_endpoint(self.agent.profile))

    def test_browser_survives_separate_python_process(self):
        self.agent.shutdown()
        code = ("from agent import Actions, WebAgent; "
                f"a=WebAgent({self.folder.name!r}).launch().connect(); "
                f"Actions.navigate(a.page, {self.url!r}); Actions.press_key(a.page, 'Tab'); "
                "Actions.type_text(a.page, 'survived@example.com'); a.disconnect()")
        subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent,
                       check=True, timeout=30)
        self.assertTrue(browser_endpoint(self.agent.profile))
        self.agent.connect()
        self.assertEqual(self.agent.page.get_by_label("Email").input_value(),
                         "survived@example.com")
        self.agent.shutdown()
        self.assertIsNone(browser_endpoint(self.agent.profile))

    def test_responses_wire_format_and_error_recovery(self):
        Fixture.requests = []
        self.agent.page.get_by_label("Email").focus()
        Fixture.replies = [
            [tool_call("run_browser", {"code": "1 / 0"})],
            [tool_call("type_text", {"text": "model@example.com"}, "call_2")],
            [tool_call("finish", {"message": "Done"}, "call_3")],
        ]
        with OpenAI(api_key="local-test", base_url=self.url + "/v1", max_retries=0) as client:
            result = self.agent.run("Fill the email", client, "test", max_steps=3)
        self.assertEqual(result, "Done")
        self.assertEqual(self.agent.page.get_by_label("Email").input_value(), "model@example.com")
        self.assertEqual(len(Fixture.requests), 3)
        for path, request in Fixture.requests:
            self.assertEqual(path, "/v1/responses")
            self.assertFalse(request["store"])
            self.assertIn(Path(__file__).with_name("agent.py").read_text(),
                          request["input"][0]["content"])
            images = [part for item in request["input"] if isinstance(item.get("content"), list)
                      for part in item["content"] if part["type"] == "input_image"]
            self.assertEqual(len(images), 1)
        outputs = [item for item in Fixture.requests[-1][1]["input"]
                   if item.get("type") == "function_call_output"]
        self.assertIn("Unknown action", outputs[0]["output"])
        self.assertEqual(outputs[1]["output"], "null")

    def test_messages_wait_for_reply_and_finish(self):
        Fixture.requests = []
        Fixture.replies = [
            [tool_call("send_message", {"message": "Which track?"}, "message_1")],
            [tool_call("wait_for_reply", call_id="wait_1")],
            [tool_call("send_message", {"message": "Working on Robotics."}, "message_2")],
            [tool_call("finish", {"message": "All done."}, "finish_1"),
             tool_call("navigate", {"url": self.url + "/must-not-run"}, "late_1")],
        ]
        messages, replies = [], []

        def reply():
            self.assertEqual(messages, ["Which track?"])
            replies.append("Robotics")
            return "Robotics"

        self.agent.on_message = messages.append
        self.agent.on_reply = reply
        with OpenAI(api_key="local-test", base_url=self.url + "/v1", max_retries=0) as client:
            result = self.agent.run("Sign me up", client, "test", max_steps=4)
        self.assertEqual(result, "All done.")
        self.assertEqual(messages, ["Which track?", "Working on Robotics."])
        self.assertEqual(replies, ["Robotics"])
        self.assertEqual(self.agent.page.url, self.url + "/")
        outputs = [item for item in Fixture.requests[2][1]["input"]
                   if item.get("type") == "function_call_output"]
        self.assertEqual(json.loads(outputs[-1]["output"]),
                         {"type": "user_reply", "text": "Robotics"})

    def test_conversation_actions_work_without_run(self):
        messages = []
        agent = WebAgent(on_message=messages.append, on_reply=lambda: "Robotics")
        agent.act("send_message", {"message": "Which track?"})
        self.assertEqual(messages, ["Which track?"])
        self.assertEqual(agent.act("wait_for_reply", {}),
                         {"type": "user_reply", "text": "Robotics"})
        self.assertIn("error", agent.act("finish", {"message": 42}))
        self.assertFalse(agent.done)
        agent.act("finish", {"message": "Done"})
        self.assertTrue(agent.done)
        self.assertEqual(agent.final_message, "Done")

        agent.on_reply = lambda: None
        self.assertIn("error", agent.act("wait_for_reply", {}))

    def test_invalid_finish_does_not_end_run(self):
        Fixture.replies = [
            [tool_call("finish", {"message": 42}, "invalid")],
            [tool_call("finish", {"message": "Done"}, "valid")],
        ]
        with OpenAI(api_key="local-test", base_url=self.url + "/v1", max_retries=0) as client:
            self.assertEqual(self.agent.run("Finish", client, "test", max_steps=2), "Done")

    def test_step_limit(self):
        Fixture.replies = [[tool_call("wait", {"seconds": 0})]]
        with OpenAI(api_key="local-test", base_url=self.url + "/v1", max_retries=0) as client:
            with self.assertRaisesRegex(RuntimeError, "unfinished"):
                self.agent.run("Keep going", client, "test", max_steps=1)


if __name__ == "__main__":
    unittest.main()
