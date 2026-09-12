# mini-web-agent

A **200-line Python web agent**, including its CLI. Only two direct dependencies:
`playwright` and `openai`. Every core source line is at most 100 characters.

Chrome runs in a detached `subprocess.Popen(..., start_new_session=True)` process.
Playwright attaches over CDP. The callable loop sends an ARIA snapshot and viewport
screenshot to the **Responses API**, executes the returned tool calls, and repeats.
There is no Chat Completions adapter.

## Install

From this directory, with Python 3.10+:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'openai>=2.0,<4' 'playwright>=1.58,<2'
python -m playwright install chromium
```

The environment in this checkout is already installed. On a minimal Linux server,
Playwright may also need browser system libraries (`playwright install-deps chromium`).
To use an existing Chrome installation, set `CHROME_BIN` to its executable instead
of installing bundled Chromium. Headless is the default; `--headed` needs a display
(for example, an existing Xvfb session).

## Run with OpenRouter

Set your key in `OPENAI_API_KEY` without committing it. For example, `read` avoids
putting the key itself in shell history:

```bash
read -rsp 'OpenRouter key: ' OPENAI_API_KEY
export OPENAI_API_KEY
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
python agent.py --model google/gemini-3.8-flash \
  'Open https://example.com and tell me the heading.'
```

`--max-steps 20` bounds model turns. `--profile /absolute/path` chooses the persistent
browser profile (default: `.chrome` relative to the working directory). `--close`
closes Chrome after the task; otherwise it stays running for inspection or reuse.
Use a vision model with function calling enabled for your key.

For OpenAI itself, unset `OPENAI_BASE_URL`, set an OpenAI key and choose an available
model. The code uses `client.responses.create()` in both cases. OpenRouter requires
stateless requests, so the loop sends history explicitly with `store=False`.
Reasoning items and tool outputs are retained; only the latest screenshot is sent.

## Call it from Python

```python
from openai import OpenAI
from agent import WebAgent

agent = WebAgent(".chrome").start()
try:
    print(agent.act("page.goto('https://example.com')\nprint(page.title())"))
    text, screenshot_data_url = agent.observe()
    print(text)
    with OpenAI(timeout=60, max_retries=1) as client:
        answer = agent.run(
            "Read the page and report its heading.",
            client,
            model="google/gemini-3.8-flash",
            max_steps=10,
            on_step=lambda step, action, output: print(step, action, output),
        )
        print(answer)
finally:
    agent.stop()  # Disconnect; Chrome and its tabs survive this Python process.

# Reconnect later, then close the actual browser with a raw CDP Browser.close command.
WebAgent(".chrome").start().stop(close_browser=True)
```

`start()` returns the agent. `act(code)` returns printed output or a Python/Playwright
error, allowing the model to recover. `observe()` returns `(json_text, image_data_url)`.
`run()` returns the model's final answer and raises if the turn budget is exhausted.
`stop()` disconnects; `stop(close_browser=True)` requests browser shutdown and polls
for its CDP endpoint to disappear. Calls are synchronous and belong on one thread.

## Action space

`act()` executes Python with persistent `page`, `context`, `browser`, and `cdp`
variables. This exposes the Playwright API directly, including context managers,
callbacks, and raw CDP, without maintaining a fixed menu of browser actions.
Assign to `page` to change the tab used by the next observation.

| Capability | Code passed to `act()` |
| --- | --- |
| Navigation | `page.goto(url)`; `page.go_back()`; `page.reload()` |
| Accessible locators | `page.get_by_role('button', name='Submit').click()` |
| Form input | `page.get_by_label('Email').fill('me@example.com')` |
| Select/check | `page.locator('select').select_option('a')`; `loc.check()` |
| Mouse/keyboard | `page.mouse.click(100, 200)`; `page.keyboard.press('Enter')` |
| Drag | `loc.drag_to(other)` or mouse `move`, `down`, `move`, `up` |
| Scroll | `page.mouse.wheel(0, 600)`; `loc.scroll_into_view_if_needed()` |
| Read DOM/JS | `print(page.content())`; `print(page.evaluate('document.title'))` |
| Wait | `loc.wait_for()`; `page.wait_for_url('**/done')` |
| Frames | `page.frame_locator('iframe').get_by_text('Continue').click()` |
| Tabs | `page = context.new_page()`; `page = context.pages[0]`; `page.close()` |
| Upload | `page.locator('input[type=file]').set_input_files('/tmp/file.txt')` |
| Dialogs | `page.on('dialog', lambda dialog: dialog.accept())` |
| Console/network | `page.on('console', lambda message: print(message.text))` |
| Interception | `page.route('**/*.png', lambda route: route.abort())` |
| Raw CDP | `print(cdp.send('Runtime.evaluate', {'expression': 'location.href'}))` |
| Capture | `page.screenshot(path='page.png')`; `page.pdf(path='page.pdf')` |

Variables such as `url`, `loc`, and `other` in the table must be defined by your code.
Multi-line actions work, including downloads and popups:

```python
agent.act("""
with page.expect_download() as pending:
    page.get_by_text('Download').click()
pending.value.save_as('/tmp/download.csv')
""")

agent.act("""
with page.expect_popup() as pending:
    page.get_by_text('Open report').click()
page = pending.value
cdp = context.new_cdp_session(page)
""")
```

For inspection, `agent.scope` contains the actual Playwright objects. To record a
trace, call `context.tracing.start(screenshots=True, snapshots=True)` before actions,
then `context.tracing.stop(path='trace.zip')`. Open it with `playwright show-trace`.
Event listeners only collect events while this Python process is connected.

## Deliberate limits

Model-generated Python runs with your process's permissions: **this is not a sandbox**.
The broad action space includes imports and filesystem access. Use trusted tasks in
an appropriate environment. Browser content is labeled untrusted in the prompt, but
that is not an enforcement boundary.

This is the full browser API surface available through Playwright/CDP, not OS desktop
automation: native browser menus, OS dialogs, and extension popups are outside it.
CDP attachment has lower fidelity than Playwright's own browser protocol, so advanced
features can have limitations. Python actions have no hard execution timeout; the
default Playwright operation timeout is 10 seconds. The model turn budget cannot stop
an infinite Python loop. Text observations are truncated to 16,000 characters and
include top-frame ARIA; the model can inspect child frames or HTML through `act()`.

The profile's `DevToolsActivePort` identifies the random port and browser token;
both are checked before attaching. Use a separate profile and only one controller
at a time. Chrome startup diagnostics are in `<profile>/chrome.log`. The launcher
targets Linux/macOS; it does not implement Windows detached-process flags. Chrome
survives Python exit, but not a machine shutdown or a supervisor killing its cgroup.

## Test

```bash
python -m unittest -v test_agent.py
```

Tests use real Chromium, temporary profiles, and a local HTTP server. They exercise
form controls, keyboard/mouse input, frames, dialogs, uploads/downloads, popups,
tracing, CDP, reconnection after a separate Python process exits, graceful shutdown,
and serialized Responses requests with tool feedback and step limits. No API key is
needed. Run `python test_live.py` for an opt-in paid end-to-end model test using your
`OPENAI_API_KEY` and `OPENAI_BASE_URL` settings.

Validated with Python 3.13.11, OpenAI SDK 3.13.0, and Playwright 1.62.0: all four
integration tests pass. The live OpenRouter Responses test with
`google/gemini-3.8-flash` also passed: the model filled the email field, selected
Robotics, checked the terms, submitted the form, and read back the confirmation.
The test independently checked the resulting DOM and final answer. This took two
browser tool calls and three model turns. The supplied key was used only in the
test process environment and was not saved in the project.

## API references

- [OpenAI Responses guidance](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [OpenRouter Responses API](https://openrouter.ai/docs/api_reference/responses/overview)
- [Gemini model IDs](https://ai.google.dev/gemini-api/docs/models)
- [Playwright CDP attachment](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)
