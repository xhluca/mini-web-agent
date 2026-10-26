"""jstools.install(agent): native <select> clicks with the same prompt and tool definitions."""

import json
import tempfile
import unittest
from unittest.mock import patch

from playwright.sync_api import Error

import agent
from agent import (WebAgent, get_action_space, get_instructions, prepare_tools,
                   probe_browser_endpoint)
import jstools

PAGE = """<a href="#product" style="position:absolute;left:80px;top:80px;width:300px;height:300px;
    background:#ddd">Product</a>
<select id="sort" style="position:absolute;left:100px;top:100px;width:160px">
<option>Position</option><option disabled>Rating</option><option>Price</option></select>
<select id="groups" style="position:absolute;left:500px;top:100px;width:200px">
<option>Pick a city</option>
<optgroup label="France"><option>Paris</option><option>Lyon</option></optgroup>
<optgroup label="Closed region" disabled><option>Nowhere</option></optgroup>
<option hidden>Hidden option</option><option>Last</option></select>
<select id="long" style="position:absolute;left:800px;top:100px">%s</select>
<div style="height:3000px"></div>
<script>window.changes = [], window.presses = 0;
document.addEventListener('change', e => changes.push(e.target.value));
document.addEventListener('pointerdown', () => presses++, true);</script>""" % "".join(
    f"<option{' selected' if i == 40 else ''}>Country {i}</option>" for i in range(60))


class InstallTests(unittest.TestCase):
    def test_install_keeps_prompt_and_tool_definitions(self):
        original = agent.Actions.click
        tools = json.dumps(prepare_tools(get_action_space()))
        instructions = get_instructions()
        try:
            jstools.install(agent)
            jstools.install(agent)
            self.assertIs(get_action_space()["click"], jstools.click)
            self.assertEqual(json.dumps(prepare_tools(get_action_space())), tools)
            self.assertEqual(get_instructions(), instructions)
        finally:
            agent.Actions.click = original


class SelectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = agent.Actions.click
        jstools.install(agent)

    @classmethod
    def tearDownClass(cls):
        agent.Actions.click = cls.original

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="mini-web-agent-test-")
        self.agent = WebAgent(self.folder.name, action_space=get_action_space()).launch().connect()
        self.page = self.agent.get_page()
        self.page.set_content(PAGE)
        self.options = self.page.locator("mini-agent-select-list")

    def tearDown(self):
        if probe_browser_endpoint(self.agent.profile):
            self.agent.connect().shutdown()
        self.folder.cleanup()

    def click(self, locator=None, x=None, y=None):
        if locator is not None:
            box = locator.bounding_box()
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        result = self.agent.act("click", dict(x=x, y=y))
        self.assertEqual(result["state"], "success", result)

    def test_click_chooses_from_options_in_page(self):
        self.click(self.page.locator("#sort"))
        self.assertEqual(self.options.count(), 1)
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "sort")
        self.click(self.options.get_by_text("Rating", exact=True))  # disabled
        self.assertEqual(self.options.count(), 1)
        self.click(self.options.get_by_text("Price", exact=True))
        self.assertEqual(self.options.count(), 0)
        self.assertEqual(self.page.locator("#sort").input_value(), "Price")
        self.assertEqual(self.page.evaluate("changes"), ["Price"])
        self.assertNotIn("#product", self.page.url)

    def test_outside_clicks_only_close_the_list(self):
        for x, y in ((180, 110), (350, 350)):  # the select again, then the link outside the list
            self.click(self.page.locator("#sort"))
            self.click(x=x, y=y)
            self.assertEqual(self.options.count(), 0)
            self.assertEqual(self.page.evaluate("presses"), 0)
            self.assertNotIn("#product", self.page.url)
        self.click(x=350, y=350)  # with the list closed, the link opens
        self.assertEqual(self.page.evaluate("presses"), 1)
        self.page.wait_for_url("**#product")

    def test_scrolling_the_page_closes_the_list(self):
        self.click(self.page.locator("#sort"))
        self.assertEqual(self.agent.act("scroll", dict(dx=0, dy=200))["state"], "success")
        self.page.wait_for_function("!document.querySelector('mini-agent-select-list')")

    def test_option_groups(self):
        self.click(self.page.locator("#groups"))
        self.assertEqual(self.options.locator("div").all_inner_texts(),
                         ["Pick a city", "France", "Paris", "Lyon", "Closed region", "Nowhere",
                          "Last"])
        for label in ("France", "Nowhere"):  # a group label and an option in a disabled group
            self.click(self.options.get_by_text(label, exact=True))
            self.assertEqual(self.options.count(), 1)
        self.click(self.options.get_by_text("Lyon", exact=True))
        self.assertEqual(self.page.evaluate("changes"), ["Lyon"])

    def test_lists_show_at_most_20_rows_like_chromium(self):
        self.click(self.page.locator("#sort"))
        row = self.options.locator("div").first.bounding_box()["height"]
        self.assertAlmostEqual(self.options.bounding_box()["height"], 3 * row + 2, delta=1)
        self.click(self.page.locator("#sort"))
        self.click(self.page.locator("#long"))  # the current option sits at the bottom edge
        listed = self.options.bounding_box()
        self.assertAlmostEqual(listed["height"], 20 * row + 2, delta=1)
        chosen = self.options.get_by_text("Country 40", exact=True).bounding_box()
        self.assertAlmostEqual(chosen["y"] + chosen["height"], listed["y"] + listed["height"] - 1,
                               delta=1)

    def test_click_is_sent_when_the_check_fails(self):
        # page.evaluate raises this when a navigation replaces the page during the check.
        with patch.object(self.page, "evaluate", side_effect=Error(
                "Execution context was destroyed, most likely because of a navigation")):
            self.click(x=350, y=350)
        self.page.wait_for_url("**#product")


if __name__ == "__main__":
    unittest.main()
