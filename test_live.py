"""Opt-in paid smoke test: python test_live.py [model]. Uses OPENAI_* environment variables."""

from http.server import ThreadingHTTPServer
import sys
import tempfile
import threading

from openai import OpenAI

from agent import WebAgent
from test_agent import Fixture


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "google/gemini-3.8-flash"
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="mini-web-agent-live-") as profile:
            agent = WebAgent(profile).start()
            try:
                agent.page.goto(f"http://127.0.0.1:{server.server_port}")
                task = ("Using the visible form controls, register model@example.com for Robotics, "
                        "agree to the terms, and click Register. Read the confirmation and report "
                        "it. Do not modify the page using JavaScript or replace its HTML.")
                with OpenAI(timeout=60, max_retries=0) as client:
                    answer = agent.run(task, client, model, max_steps=25,
                                       on_step=lambda n, a, r: print(n, a, r, flush=True))
                actual = agent.page.get_by_role("status").inner_text()
                assert actual == "Registered: model@example.com / Robotics", actual
                assert "model@example.com" in answer and "Robotics" in answer, answer
                print("PASS:", answer)
            finally:
                agent.stop(close_browser=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
