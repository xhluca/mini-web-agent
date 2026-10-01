"""Record an actual model-driven agent run: python demo/record.py --model MODEL.

Uses the agent's dependencies and a local FFmpeg executable. Frames are captured through CDP;
long pauses between changing frames are shortened in the GIF. No browser actions are scripted.
"""

import argparse
import base64
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openai import OpenAI
from agent import WebAgent, get_action_space, get_instructions, run
from cursor import show_cursor

ROOT = Path(__file__).resolve().parent
TASK = ("Book the 2:00 pm Robotics Lab on October 10 for Alex Chen, alex@example.com. "
        "Agree to the terms, confirm the booking, and verify the visible confirmation. "
        "The screenshot viewport is 1280 by 800 CSS pixels. Use only the provided browser "
        "actions. Call finish when the booking is verified.")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Recorder:
    def __init__(self, agent, folder, model):
        self.agent, self.folder, self.model = agent, folder, model
        self.frames, self.actions = [], []
        self.cdp = agent.get_page().context.new_cdp_session(agent.get_page())
        self.cdp.on("Page.screencastFrame", self.capture)

    def capture(self, event):
        path = self.folder / f"frame-{len(self.frames):05}.jpg"
        path.write_bytes(base64.b64decode(event["data"]))
        self.frames.append((path, event["metadata"]["timestamp"]))
        self.cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    def before(self, step, action, result):
        args = json.loads(action["arguments"])
        label = f"{action['name']}({', '.join(f'{k}={v!r}' for k, v in args.items())})"
        self.status(label)
        show_cursor(self.agent, step, action, result)

    def after(self, step, action, result):
        self.actions.append(dict(step=step, action=action, result=result))
        print(step, action["name"], result, flush=True)
        self.agent.get_page().wait_for_timeout(300)

    def status(self, label):
        self.agent.get_page().evaluate("""({label, count, model}) => {
            document.querySelector('#model').textContent = model;
            document.querySelector('#action').textContent = label;
            document.querySelector('#counter').textContent = `${count} actions completed`;
        }""", dict(label=label, count=len(self.actions), model=self.model))

    def start(self):
        self.cdp.send("Emulation.setVisibleSize", dict(width=self.agent.w, height=self.agent.h))
        self.agent.get_page().wait_for_timeout(500)
        self.status("Observing the page. Choosing the first action.")
        self.cdp.send("Page.startScreencast", dict(format="jpeg", quality=90,
                      maxWidth=1280, maxHeight=800, everyNthFrame=1))
        self.agent.get_page().wait_for_timeout(1200)

    def save(self, output):
        self.agent.get_page().wait_for_timeout(800)
        self.cdp.send("Page.stopScreencast")
        if not self.frames:
            raise RuntimeError("Chrome did not produce any recording frames")
        entries = []
        for index, (path, timestamp) in enumerate(self.frames):
            gap = self.frames[index + 1][1] - timestamp if index + 1 < len(self.frames) else 2.5
            duration = min(max(gap, 1 / 15), 0.6) if index + 1 < len(self.frames) else gap
            entries.extend([f"file '{path.as_posix()}'", f"duration {duration:.4f}"])
        entries.append(f"file '{self.frames[-1][0].as_posix()}'")
        playlist = self.folder / "frames.txt"
        playlist.write_text("\n".join(entries) + "\n")
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i",
            str(playlist), "-filter_complex",
            "fps=15,scale=960:-1:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=128:stats_mode=diff[p];"
            "[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
            "-loop", "0", str(output),
        ], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="google/gemini-3.8-flash")
    parser.add_argument("--output", type=Path, default=ROOT / "demo.gif")
    args = parser.parse_args()
    if not shutil.which("ffmpeg"):
        parser.error("Install FFmpeg to encode the demo GIF")
    handler = partial(QuietHandler, directory=str(ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="mini-agent-demo-") as temporary:
            folder = Path(temporary)
            agent = WebAgent(folder / "chrome", action_space=get_action_space())
            try:
                agent.launch().connect()
                agent.get_page().goto(f"http://127.0.0.1:{server.server_port}")
                recorder = Recorder(agent, folder, args.model)
                recorder.start()
                with OpenAI(timeout=90, max_retries=1) as client:
                    answer = run(agent, TASK, client, args.model, get_instructions(), max_steps=30,
                                 callbacks=[dict(type="before", function=recorder.before),
                                            dict(type="after", function=recorder.after)])
                actual = agent.get_page().get_by_role("status").inner_text()
                expected = ("Robotics Lab", "2:00 pm", "Alex Chen", "alex@example.com")
                if not isinstance(answer, str) or not all(value in actual for value in expected):
                    raise RuntimeError(f"Agent did not complete the booking: {actual!r}")
                recorder.status("Verified. Booking complete.")
                args.output.parent.mkdir(parents=True, exist_ok=True)
                recorder.save(args.output)
                log = dict(model=args.model, task=TASK, answer=answer,
                           confirmation=actual, actions=recorder.actions)
                args.output.with_suffix(".json").write_text(json.dumps(log, indent=2) + "\n")
                print(f"Saved {args.output}: {len(recorder.actions)} model-selected actions")
            finally:
                agent.shutdown()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
