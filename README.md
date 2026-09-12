# mini-web-agent

A small screenshot-driven Python web agent with **predefined browser functions**.
Only two direct dependencies: `playwright` and `openai`. The core and CLI live in
`agent.py`; lines are at most 100 characters. Explicit functions and argument
validation replace the original 200-line implementation's unrestricted Python executor.

Chrome runs in a detached `subprocess.Popen(..., start_new_session=True)` process.
Playwright attaches over CDP. The loop sends viewport screenshots and tab metadata
through the **Responses API**, dispatches named function calls, and observes again.
There are no accessibility trees, DOM observations, or model-generated code execution.

## Install and run

From this directory, with Python 3.10+:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'openai>=2.0,<4' 'playwright>=1.58,<2'
python -m playwright install chromium
read -rsp 'OpenRouter key: ' OPENAI_API_KEY
export OPENAI_API_KEY
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
python agent.py --model google/gemini-3.8-flash \
  'Open https://example.com and tell me the heading.'
```

`--max-steps 30` bounds model turns. `--profile /absolute/path` chooses the persistent
Chrome profile (default `.chrome` in the working directory). `--close` shuts Chrome
down after the task; otherwise it stays running for inspection and reuse.
Use `--connect` to attach to an already running browser instead of launching a new one.
Chrome chooses a random localhost CDP port by default. Use `--cdp-port 9222` to request a
specific port when launching. The CLI prints the CDP URL after connecting.

```python
agent = WebAgent('.chrome', cdp_port=0).launch()  # Random port by default.
print(agent.cdp_port)                          # Actual assigned port.
agent.connect()
```

Choose a fixed port with `WebAgent('.chrome', cdp_port=9222).launch()`.
`agent.cdp_port` starts at `0` by default and holds the actual port after launch or
connect. Set it back to `0` before relaunching to choose a new random port.
An occupied explicitly requested port raises an error. CDP remains bound to localhost.

The launcher always uses Playwright's bundled Chromium executable.
Chrome launches with `--headless=new` hardcoded; there is no headed mode or flag override.
Minimal Linux installations may need `playwright install-deps chromium`.
For OpenAI itself, unset `OPENAI_BASE_URL` and use an OpenAI key and available vision model.
Both providers use `client.responses.create()`; no Chat Completions adapter is included.

## Callable interface

```python
from openai import OpenAI
from agent import WebAgent

agent = WebAgent('.chrome', on_message=print, on_reply=input).launch().connect()
try:
    agent.act('navigate', {'url': 'https://example.com'})
    text, screenshot_data_url = agent.observe()
    print(text)  # Active tab index, tab titles/URLs, viewport dimensions.
    with OpenAI(timeout=60, max_retries=1) as client:
        print(agent.run(
            'Read the page and report its heading.',
            client,
            model='google/gemini-3.8-flash',
            max_steps=15,
            on_step=lambda step, action, result: print(step, action, result),
        ))
finally:
    agent.disconnect()  # Disconnect; Chrome survives Python exit.

# Reconnect later and close the actual browser.
WebAgent('.chrome').connect().shutdown()
```

The lifecycle is explicit:

- `launch()` starts detached Chrome and returns the agent; it does not attach Playwright.
- `connect()` attaches to running Chrome and returns the agent; it never launches Chrome.
- `disconnect()` detaches Playwright and leaves Chrome running.
- `shutdown()` closes the connected Chrome and disconnects. Connect first to close a browser.

Use `WebAgent(profile).launch().connect()` for a new browser and
`WebAgent(profile).connect()` to reuse one. Launch/connect do not preflight browser
liveness. Lifecycle failures include an explicit message and retain the original exception
as their cause; profile-file errors propagate directly.

All model actions live in the `Actions` namespace before the helper functions. Its
methods are made static by one `@namespace` decorator; no instance is needed.
Page actions take an explicit Playwright page:

```python
from agent import Actions

Actions.click(agent.page, 100, 200)
Actions.double_click(agent.page, 100, 200)
Actions.hover(agent.page, 300, 400)
Actions.type_text(agent.page, 'hello')
```

Navigation, keyboard, scrolling, dragging, waiting, and screenshots follow the same
pattern. Tab operations use the same namespace:

```python
from agent import Actions

Actions.list_tabs(agent.page)
agent.page = Actions.new_tab(agent.page, 'https://example.com')
agent.page = Actions.switch_tab(agent.page, 0)
agent.page = Actions.close_tab(agent.page, 1)
```

Functions that change the active tab return a Playwright Page. The agent stores that
page and returns an index or updated tab list to the model. All model calls go through
`act(name, arguments)`; browser actions receive the current page, and the model cannot
choose or override that Python object.
These calls are synchronous and belong on one thread. `act` returns a JSON-serializable
result or an `error` object; `run` feeds errors back to the model for recovery.

## Model-callable functions

| Category | Functions |
| --- | --- |
| Navigation | `navigate(url)`, `back()`, `forward()`, `reload()` |
| Clicks | `click(x, y)`, `double_click(x, y)`, `right_click(x, y)` |
| Pointer | `hover(x, y)`, `mouse_down()`, `mouse_up()`, `drag(x1, y1, x2, y2)` |
| Scrolling | `scroll(dx, dy)`; positive values scroll right/down at the pointer |
| Keyboard | `type_text(text)`, `press_key(key)`, `key_down(key)`, `key_up(key)` |
| Timing | `wait(seconds)`; between 0 and 10 seconds |
| Conversation | `send_message(message)`, `wait_for_reply()`, `finish(message)` |
| Observation | `screenshot()`, `list_tabs()` |
| Tabs | `new_tab(url='about:blank')`, `switch_tab(index)`, `close_tab(index)` |

Coordinates are CSS pixels within a 1280×800 viewport. Screenshots use the same CSS
scale, including on high-DPI displays. Keyboard actions target the focused control.
`press_key` accepts Playwright key names/chords such as `Tab`, `ArrowDown`, `Enter`,
and `ControlOrMeta+A`. Mouse down/up hold/release the left button; `right_click` is a
complete right-button click. Release held keys/buttons before switching tabs.
Visible controls inside frames are reachable by coordinates without selecting a frame.

Tabs use zero-based indices from Playwright's `context.pages`, with no separate
registry or counter. Each observation includes the latest indices, titles, URLs, and
active flag. Closing a tab shifts later indices, so use the newest list. Popups appear
automatically without changing the agent's active tab. `new_tab` activates its new tab.
Closing the active tab selects a remaining one; closing the last creates a blank tab.

`screenshot()` returns a data URL to host callers. When called as a model tool, it
requests the next observation's screenshot instead of returning image bytes as text.
The loop captures an observation before each model turn. Only the latest image is
sent; prior textual metadata, reasoning, and tool results stay in history. Requests
use `store=False` and explicit history for OpenRouter's stateless Responses endpoint.

`send_message(message)` calls `on_message` and the loop continues. `wait_for_reply()`
blocks in `on_reply` until it returns a string, then supplies that reply to the model.
In the CLI these default to `print` and `input`. Neither action clicks or types into
the website. `wait(seconds)` remains a separate browser-delay action.

`finish(message)` ends the current run immediately and returns its final message;
later actions in the same model response are skipped. Browser shutdown remains the
caller's choice. Plain assistant text is displayed as a message and the loop continues
until `finish` or the turn limit. Configure `on_message` and `on_reply` on `WebAgent`.
Calling `act()` performs these actions directly, including delivery and waiting.
`finish` sets `agent.done` and `agent.final_message`; `run()` checks that state after
each action. Each new run resets completion state.

## Restriction and self-documentation

The system prompt includes `Path(__file__).read_text()` so the implementation itself
documents the functions. Tool schemas are derived from their signatures/docstrings. Endpoint discovery, browser
readiness waits, URL/argument validation, and schema construction are module-level
helpers with explicit inputs. Page actions are module-level functions too; the class
holds browser/tab state, dispatches calls, and runs the agent loop.
The `ACTIONS` dictionary is derived from `Actions` static methods and drives both
tool schemas and dispatch; seeing a
function in the source does not make it callable. `launch`, `connect`, `disconnect`, `shutdown`, `run`, and internal methods are host-only.

Before dispatch, the agent checks the name, argument object, signature, types,
finite numbers, and viewport coordinate bounds. Extra arguments are rejected.
Navigation/new-tab tools allow HTTP(S) and `about:blank`; `file:`, `data:`, and
`javascript:` URLs are rejected. There is no model-facing Python executor, JavaScript
evaluator, raw CDP command, selector API, arbitrary callback, or filesystem tool.
Screenshots stay in memory. The model cannot supply executable source or output paths.

This restricts the model's tool interface; it is not browser isolation or a website
allowlist. Clicks and keyboard input can still submit forms, trigger downloads, and
perform authenticated actions. Remote pages still execute their own JavaScript.
The source code and screenshots are sent to the configured model provider.

## Deliberate limits

Uploads/download management, explicit dialog handling, DOM reading, and native OS
controls are outside the model action set. Playwright's default dialog behavior applies.
Use screenshots plus `wait` for loading/animation state. Coordinate targeting can be
less reliable than locators; native menus may not be represented in page screenshots.

A dedicated profile's `DevToolsActivePort` provides the random localhost port and
browser token; both are checked before attaching. Use one controller per profile.
Startup diagnostics are in `<profile>/chrome.log`. CDP attachment has lower fidelity
than Playwright's own protocol, so advanced browser features can have limitations.
The launcher targets Linux/macOS. Chrome survives Python exit, but not machine shutdown
or a supervisor killing its cgroup.

## Tests

```bash
python -m unittest -v test_agent.py
python test_live.py  # Opt-in paid Gemini test using OPENAI_* environment variables.
```

The integration suite uses real Chromium and a local HTTP/Responses fixture. It checks
coordinate form input, keyboard and mouse events, iframe interaction, screenshots,
navigation, popup discovery, updated tab indices, closing the last tab, rejected tool
names/arguments, persistence across Python processes, source self-documentation,
Responses serialization, error recovery, and turn limits. Test code uses DOM assertions
to verify outcomes independently; the model receives no DOM information.

The live test asks Gemini 3.8 Flash to complete a signup form using the restricted tools,
then verifies both the resulting DOM and the model's reported confirmation.

Validated with Python 3.13.11, OpenAI SDK 3.13.0, and Playwright 1.62.0: all twelve
integration tests pass. The screenshot-only Gemini 3.8 Flash test through OpenRouter's
Responses API also passed, using 14 predefined actions across 15 model turns. The API
key was used only in the test process environment and was not saved in the project.

## References

- [OpenAI Responses guidance](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [OpenRouter Responses API](https://openrouter.ai/docs/api_reference/responses/overview)
- [Playwright Page API](https://playwright.dev/python/docs/api/class-page)
