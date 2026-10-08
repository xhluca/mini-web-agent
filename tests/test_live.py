"""Opt-in paid smoke test: python -m tests.test_live [model]. Uses OPENAI_* environment variables."""

import argparse
from functools import partial
from http.server import ThreadingHTTPServer
import tempfile
import threading

from openai import OpenAI

from agent import WebAgent, get_action_space, get_instructions, run
from tests.test_agent import Fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", nargs="?", default="google/gemini-3.8-flash")
    parser.add_argument("--coordinates", choices=("css", "normalized"), default="normalized")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--cursor", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="mini-web-agent-live-") as profile:
            agent = WebAgent(profile, action_space=get_action_space(), coordinates=args.coordinates)
            agent.launch(headed=args.headed).connect()
            try:
                agent.get_page().goto(f"http://127.0.0.1:{server.server_port}")
                task = ("Using the visible form controls, register model@example.com for Robotics, "
                        "agree to the terms, and click Register. Read the confirmation and report "
                        "it. Do not modify the page using JavaScript or replace its HTML.")
                with OpenAI(timeout=60, max_retries=0) as client:
                    callbacks = [dict(type="after", function=partial(print, flush=True))]
                    if args.cursor:
                        from callbacks.cursor import show_cursor
                        callbacks.append(dict(type="before", function=partial(show_cursor, agent)))
                    answer = run(agent, task, client, args.model, max_steps=25,
                        instructions=get_instructions(), callbacks=callbacks)
                actual = agent.get_page().get_by_role("status").inner_text()
                assert actual == "Registered: model@example.com / Robotics", actual
                assert "model@example.com" in answer and "Robotics" in answer, answer
                print("PASS:", answer)
            finally:
                agent.shutdown()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
