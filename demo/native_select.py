"""Compare clicks on a native <select> before and after the fix: python demo/native_select.py

Chromium draws an open <select> in a popup outside the page. Screenshots show the popup, but
page.mouse events go to the page under it. This script makes the same two clicks on a store's
"Sort By" select, on Sort By and then on Price in its open list. It runs them with
page.mouse.click (before the fix) and with Actions.click (after the fix), prints the select
value and URL after each click, and saves screenshots with the cursor next to this file.
"""

from functools import partial
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import Actions, WebAgent, get_action_space
from callbacks.cursor import show_cursor

ROOT = Path(__file__).resolve().parent
CLICKS = [("Sort By", (710, 100), "_open"), ("Price", (710, 176), "")]  # CSS pixels


def main():
    with tempfile.TemporaryDirectory(prefix="mini-agent-select-") as profile:
        agent = WebAgent(profile, w=800, h=360, coordinates="css", action_space=get_action_space())
        try:
            agent.launch().connect()
            page = agent.get_page()
            runs = [("before_fix", "page.mouse.click", page.mouse.click),
                    ("after_fix", "Actions.click", partial(Actions.click, page))]
            for name, method, click in runs:
                print(f"{name.replace('_', ' ').capitalize()} ({method}):")
                page.goto((ROOT / "native_select.html").as_uri())
                for label, (x, y), suffix in CLICKS:
                    show_cursor(agent, 0, dict(name="click", arguments=dict(x=x, y=y)), None)
                    click(x, y)
                    page.wait_for_timeout(300)
                    page.screenshot(path=ROOT / f"native_select_{name}{suffix}.png")
                    value = page.locator("#sorter").input_value()
                    print(f"  click {label} at ({x}, {y}): value={value}, "
                          f"url={page.url.rsplit('/', 1)[-1]}")
        finally:
            agent.shutdown()


if __name__ == "__main__":
    main()
