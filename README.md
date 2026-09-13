# Frontier web agents in less than 400 lines

**mini-web-agent** puts a vision model in control of a real browser with a Python file
small enough to read in one sitting. Two dependencies: **Playwright** and **OpenAI**.

The model sees screenshots, chooses predefined actions, and checks the result.
Chrome runs as a detached process. Playwright controls it over CDP. The OpenAI SDK
handles model calls through OpenRouter's Responses API.

The aim is to keep the machinery around the model understandable. Browser control,
tool definitions, conversation history, and the agent loop all live in [agent.py](agent.py).
The implementation stays below 400 lines, with a 100-character line limit.

## The philosophy

- **Let the model work from what it sees.** Screenshots and tab metadata are its observations.
- **Make actions explicit.** Click, hover, drag, type, scroll, switch tabs, ask a question, finish.
  The model chooses from Python functions; it cannot submit code for execution.
- **Keep the loop visible.** Observe, request actions, execute them, return results, repeat.
- **Use the source as documentation.** The default prompt includes the script itself.
  Tool schemas come from function signatures and docstrings.
- **Keep state straightforward.** Playwright supplies the tab list. History grows by appending
  messages, preserving earlier screenshots and the prompt prefix for provider caching.

The line count applies to the complete agent and CLI. Tests live separately so the
implementation can stay small while its behavior remains independently checkable.

## Run it

Use Python 3.10+ on Linux or macOS:

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

Chromium is always headless and uses Playwright's bundled executable. Minimal Linux
installations may also need `python -m playwright install-deps chromium`.

The CLI prints actions and results. It closes Chrome when it launched it; with
`--connect`, it leaves the existing browser running.

| Option | Purpose |
| --- | --- |
| `--model` | OpenRouter model ID; required |
| `--max-steps` | Maximum model turns; defaults to 100 |
| `--profile` | Persistent Chrome profile; defaults to `.chrome` |
| `--cdp-port` | Port for a new launch; a new profile defaults to a random localhost port |
| `--connect` | Attach to Chrome already running with the selected profile |

## Use it from Python

Use the same environment variables as above:

```python
from openai import OpenAI
from agent import WebAgent, get_action_space, get_instructions, run

agent = WebAgent('.chrome', action_space=get_action_space())
try:
    agent.launch().connect()
    with OpenAI(timeout=60, max_retries=1) as client:
        answer = run(
            agent, 'Find the heading on https://example.com.', client,
            model='google/gemini-3.8-flash', instructions=get_instructions(),
            max_steps=20,
            on_action=lambda step, action, result: print(step, action, result),
        )
        print(answer)
finally:
    agent.shutdown()
```

`action_space` and `instructions` are required inputs. Supply your own dictionary or
prompt, or use the getters. The same action dictionary controls both tool schemas and dispatch.
`on_action` receives the model-turn index, action name/arguments, and result dictionary.
User-facing messages and replies use `on_message` and `on_reply`, defaulting to `print` and `input`.

Browser lifecycle and model execution are separate:

| Function | What it does |
| --- | --- |
| `agent.launch()` | Start detached Chrome |
| `agent.connect()` | Attach Playwright to Chrome |
| `agent.observe()` | Return tab metadata as JSON and a screenshot data URL |
| `agent.act(name, arguments)` | Execute an allowed action and return its result dictionary |
| `run(agent, task, client, model, instructions, ...)` | Run the model loop until completion or its limit |
| `agent.disconnect()` | Detach Playwright, leaving Chrome running |
| `agent.shutdown()` | Close Chrome and disconnect, even if Chrome was already running |

To retain a browser for a later task, explicitly call `disconnect()` instead of `shutdown()`.
A new agent can attach using the same profile. The profile records its CDP address.

## The action space

The 24 actions are ordinary functions grouped in `Actions`:

| Interaction | Actions |
| --- | --- |
| Navigation | `navigate`, `back`, `forward`, `reload` |
| Pointer | `click`, `double_click`, `right_click`, `hover`, `mouse_down`, `mouse_up`, `drag` |
| Scroll and keyboard | `scroll`, `type_text`, `press_key`, `key_down`, `key_up` |
| Tabs | `list_tabs`, `new_tab`, `switch_tab`, `close_tab` |
| Timing and conversation | `wait`, `send_message`, `wait_for_reply`, `finish` |

Coordinates match the 1280×800 screenshot viewport. Keyboard input goes to the focused
control. Tab indices come from the latest observation and can shift after a tab closes.
The source documents each action's arguments.

## A small loop, with recovery

The first screenshot is a user message. After an action batch, one new screenshot and
the current tab metadata accompany the last tool result. Each action gets its own result:

```python
{"state": "success", "output": ...}
{"state": "error", "output": "..."}
```

`run()` serializes these dictionaries when sending tool results. Errors give the model
feedback to recover. Missing tool calls receive a reminder; incomplete responses are
discarded and retried without executing or replaying partial actions. Every retry counts
against `max_steps`. A successful `finish` returns the final answer; exhausting the limit
returns an unfinished-task message.

History retains earlier screenshots and reasoning to preserve the cacheable prefix.
That also means context grows with the task; cache hits depend on the provider.

## Scope

This is a compact browser agent you can inspect and modify. It has no DOM-reading tool,
accessibility-tree parser, generated-code executor, upload/download manager, or OS controls.
The action dictionary limits the model's callable functions; it does not isolate the browser
from websites or prevent actions available through ordinary clicks and typing.

## Check it

```bash
python -m unittest discover -s tests -v
python -m tests.test_live  # Optional paid OpenRouter test using the configured environment.
```

The local suite uses real Chromium and a local Responses fixture to check browser actions,
tabs, process cleanup, tool results, and recovery. The live test asks the model to complete
a signup form, then verifies the form result independently through the DOM.
