"""Real Chromium integration tests and a local Responses API server; no API key needed."""

import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from openai import OpenAI

from agent import WebAgent

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


def tool_call(code, call_id="call_1", name="run_browser"):
    return dict(type="function_call", id="fc_" + call_id, call_id=call_id,
                name=name, arguments=json.dumps({"code": code}), status="completed")


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
        self.agent = WebAgent(self.folder.name).start()
        self.agent.scope["page"].goto(self.url)

    def tearDown(self):
        self.agent.start().stop(close_browser=True)
        self.folder.cleanup()

    def act(self, code):
        result = self.agent.act(code)
        self.assertNotIn("Error:", result)
        return result

    def test_actions_observation_and_events(self):
        self.act("page.get_by_label('Email').fill('agent@example.com')")
        self.act("page.get_by_label('Track').select_option(label='Robotics')")
        self.act("page.get_by_label('Agree to terms').check()")
        self.act("page.get_by_role('button', name='Register').click()")
        self.assertIn("agent@example.com / Robotics", self.act(
            "print(page.get_by_role('status').inner_text())"))
        self.act("page.frame_locator('iframe').get_by_role('button').click()")
        self.assertIn("42", self.act("print(page.frames[1].locator('button').inner_text())"))
        self.act("page.get_by_label('Email').focus()\npage.keyboard.press('ControlOrMeta+A')\n"
                 "page.keyboard.type('typed@example.com')")
        self.assertIn("typed@example.com", self.act(
            "print(page.locator('input').first.input_value())"))
        self.act("box = page.get_by_role('button', name='Register').bounding_box()\n"
                 "page.mouse.move(box['x'] + 5, box['y'] + 5, steps=5)\n"
                 "page.mouse.down()\npage.mouse.up()\npage.mouse.wheel(0, 400)")
        self.act("page.wait_for_function('scrollY > 0')")
        self.act("page.get_by_label('Upload').set_input_files("
                 "{'name': 'note.txt', 'mimeType': 'text/plain', 'buffer': b'hello'})")
        self.assertIn("note.txt", self.act(
            "print(page.get_by_label('Upload').evaluate('(el) => el.files[0].name'))"))
        self.act("dialogs = []\ndef handle(dialog):\n"
                 "    dialogs.append(dialog.message)\n    dialog.accept()\n"
                 "page.on('dialog', handle)\npage.get_by_text('Dialog', exact=True).click()")
        self.assertIn("Hello", self.act("print(dialogs)"))
        self.act("with page.expect_download() as pending:\n"
                 "    page.get_by_text('Download', exact=True).click()\n"
                 f"pending.value.save_as({str(Path(self.folder.name) / 'note.txt')!r})")
        self.assertEqual((Path(self.folder.name) / "note.txt").read_text(), "downloaded")
        self.act("with page.expect_popup() as pending:\n"
                 "    page.get_by_text('Popup', exact=True).click()\npage = pending.value\n"
                 "page.wait_for_load_state()")
        state, screenshot = self.agent.observe()
        self.assertEqual(len(json.loads(state)["tabs"]), 2)
        self.assertTrue(base64.b64decode(screenshot.split(",")[1]).startswith(b"\xff\xd8"))
        self.act("page.close()")
        self.assertEqual(len(json.loads(self.agent.observe()[0])["tabs"]), 1)
        self.assertIn("Workshop signup", self.act(
            "print(cdp.send('Runtime.evaluate', {'expression': 'document.body.innerText'}))"))
        self.act("context.tracing.start(screenshots=True, snapshots=True)\npage.reload()\n"
                 f"context.tracing.stop(path={str(Path(self.folder.name) / 'trace.zip')!r})")
        self.assertTrue((Path(self.folder.name) / "trace.zip").is_file())
        self.assertIn("ZeroDivisionError", self.agent.act("1 / 0"))

    def test_browser_survives_separate_python_process(self):
        self.agent.stop(close_browser=True)
        code = ("from agent import WebAgent; "
                f"a=WebAgent({self.folder.name!r}).start(); "
                f"a.scope['page'].goto({self.url!r}); "
                "a.act(\"page.evaluate('window.survived = 42')\"); a.stop()")
        subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent,
                       check=True, timeout=30)
        self.assertTrue(self.agent._endpoint())
        self.agent.start()
        self.assertIn("42", self.act("print(page.evaluate('window.survived'))"))
        self.agent.stop(close_browser=True)
        self.assertIsNone(self.agent._endpoint())

    def test_responses_wire_format_and_error_recovery(self):
        Fixture.requests = []
        Fixture.replies = [
            [tool_call("1 / 0")],
            [tool_call("page.get_by_label('Email').fill('model@example.com')\n"
                       "print(page.get_by_label('Email').input_value())", "call_2")],
            [dict(type="message", id="msg_1", role="assistant", status="completed",
                  content=[dict(type="output_text", text="Done", annotations=[])])],
        ]
        with OpenAI(api_key="local-test", base_url=self.url + "/v1", max_retries=0) as client:
            result = self.agent.run("Fill the email", client, "test", max_steps=3)
        self.assertEqual(result, "Done")
        self.assertEqual(self.agent.scope["page"].get_by_label("Email").input_value(),
                         "model@example.com")
        self.assertEqual(len(Fixture.requests), 3)
        for path, request in Fixture.requests:
            self.assertEqual(path, "/v1/responses")
            self.assertFalse(request["store"])
            images = [part for item in request["input"] if isinstance(item.get("content"), list)
                      for part in item["content"] if part["type"] == "input_image"]
            self.assertEqual(len(images), 1)
        outputs = [item for item in Fixture.requests[-1][1]["input"]
                   if item.get("type") == "function_call_output"]
        self.assertIn("ZeroDivisionError", outputs[0]["output"])
        self.assertIn("model@example.com", outputs[1]["output"])

    def test_step_limit(self):
        Fixture.replies = [[tool_call("print('still working')")]]
        with OpenAI(api_key="local-test", base_url=self.url + "/v1", max_retries=0) as client:
            with self.assertRaisesRegex(RuntimeError, "unfinished"):
                self.agent.run("Keep going", client, "test", max_steps=1)


if __name__ == "__main__":
    unittest.main()
