# Frontier web agents in less than 400 lines

**mini-web-agent** lets a vision model use a real browser from a single Python file.
Screenshots in, browser actions out.

- **Small:** Browser control, tool schemas, and the agent loop in [agent.py](agent.py).
  Under 400 lines, with a 100-character line limit.
- **Two dependencies:** Playwright for the browser, OpenAI for OpenRouter's Responses API.
- **Visual:** The model works from screenshots and current tab metadata.
- **Explicit actions:** 24 functions for navigation, pointer input, typing, tabs, and conversation.
- **Persistent browser:** Detached Chrome controlled over CDP; launch it or attach to a running session.
- **Easy to adapt:** Supply your own actions, instructions, and callbacks.

<details>
<summary>Why keep it small?</summary>

The goal is to make the machinery around the model easy to read and change.
The model chooses actions and checks their effects; the Python loop handles observations,
model calls, and results.

The default prompt includes the source code, so the model can read the functions it may call.
Tool schemas come from their signatures and docstrings. History grows by appending messages,
preserving earlier screenshots and the prompt prefix for provider caching.

The line count covers the complete agent and CLI. The optional cursor, demo recorder, and
tests live separately.

</details>

![Gemini uses screenshots and browser actions to book a Robotics Lab workshop](demo/demo.gif)

*Gemini 3.8 Flash books a workshop through OpenRouter. Thinking pauses shortened.
[Action log](demo/demo.json).*

<details>
<summary>Record your own demo</summary>

Use the environment variables from the quick start below and install
[FFmpeg](https://ffmpeg.org/download.html), then run:

```bash
python demo/record.py --model google/gemini-3.8-flash
```

The recorder runs the actual agent on a local booking page, captures Chrome frames through
CDP, and independently checks the confirmation. It writes `demo/demo.gif` and `demo/demo.json`.
FFmpeg is only needed for recording.

</details>

## Quick start

Requires Python 3.10+ on Linux or macOS.

```bash
git clone https://github.com/xhluca/mini-web-agent.git
cd mini-web-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'openai>=2.0,<4' 'playwright>=1.58,<2'
python -m playwright install chromium

export OPENAI_API_KEY="your-openrouter-key"
export OPENAI_BASE_URL=https://openrouter.ai/api/v1

python -u agent.py --headed --cursor --model google/gemini-3.8-flash \
  'Open https://example.com and tell me the heading.'
```

Chrome opens on your desktop, with an animated cursor showing the agent's pointer actions.
The agent runs in the foreground: actions, results, and questions appear in your terminal.
Omit `--headed` for headless Chrome, or `--cursor` to hide the overlay.
Press **Ctrl+C** to interrupt.

<details>
<summary>CLI options and connecting to Chrome</summary>

| Option | Purpose |
| --- | --- |
| `--model` | OpenRouter model ID; required |
| `--max-steps` | Maximum model turns; defaults to 100 |
| `--profile` | Persistent Chrome profile; defaults to `.chrome` |
| `--port` | Launch port; a new profile defaults to a random localhost port |
| `--connect` | Attach to Chrome already running with the selected profile |
| `--headed` | Show Chrome; new launches are headless by default |
| `--cursor` | Animate pointer actions before execution |

To attach to a browser previously launched with this profile:

```bash
python -u agent.py --connect --profile .chrome --model google/gemini-3.8-flash \
  'Tell me what is open in the current tab.'
```

`--connect` preserves existing tabs and browser visibility. Adding `--headed` prints a
warning and continues; it cannot change how an existing browser was launched.
The CLI closes Chrome when it launched it and only disconnects with `--connect`.

New CLI launches open one blank tab and close restored tabs, retaining profile data.
Use `--port 9222` for a fixed launch port. With `--port 0`, a new profile lets Chrome
choose a port; an existing profile reuses its recorded port. `--connect` reads the
profile's address and cannot be combined with a nonzero `--port`.
The CLI prints the CDP address after connecting.

Chromium comes from Playwright's bundled executable. Minimal Linux installations may need
`python -m playwright install-deps chromium`.

</details>

## Use it from Python

With the same environment variables:

```python
from openai import OpenAI
from agent import WebAgent, get_action_space, get_instructions, run

agent = WebAgent(".chrome", action_space=get_action_space())
try:
    agent.launch().connect()
    with OpenAI(timeout=60, max_retries=1) as client:
        answer = run(
            agent, "Find the heading on https://example.com.", client,
            model="google/gemini-3.8-flash", instructions=get_instructions(),
            max_steps=20, callbacks=[dict(type="after", function=print)],
        )
        print(answer)
finally:
    agent.shutdown()
```

Use `agent.launch(headed=True)` for a visible browser. To retain Chrome for another task,
call `agent.disconnect()` instead of `agent.shutdown()`.

<details>
<summary>Browser lifecycle</summary>

| Function | What it does |
| --- | --- |
| `agent.launch()` | Start detached Chrome |
| `agent.connect()` | Attach Playwright to Chrome |
| `agent.get_page()` | Get the active page, recover a closed tab, and set its viewport |
| `agent.observe()` | Return tab metadata as JSON and a screenshot data URL |
| `agent.act(name, arguments)` | Execute an allowed action and return its result |
| `run(agent, task, client, model, instructions, ...)` | Run the model loop |
| `agent.disconnect()` | Detach Playwright, leaving Chrome running |
| `agent.shutdown()` | Close Chrome and disconnect, including an attached browser |

Pass `port=9222` to `WebAgent` for a fixed port; read `agent.port` after launch for the
actual port. The default is `port=0`. Another agent can reconnect using the same profile.
Playwright discovers the current WebSocket endpoint over HTTP.

</details>

<details>
<summary>Customize actions, instructions, and callbacks</summary>

`action_space` and `instructions` are required. Supply your own action dictionary and
prompt, or use the getters. The same dictionary controls both tool schemas and dispatch.

Callbacks receive `(step, action, result)`, where `step` is the model-turn index,
`action` contains the name and arguments, and `result` is `None` before execution.
Use `type="before"` or `type="after"` to choose when each callback runs:

```python
from functools import partial
from cursor import show_cursor

callbacks = [
    dict(type="before", function=partial(show_cursor, agent)),
    dict(type="after", function=print),
]
```

The optional [cursor.py](cursor.py) overlay glides between targets, traces drags, and
pulses on clicks. It respects reduced-motion preferences and does not intercept clicks.
The CLI loads it only with `--cursor`.

User messages and replies use `on_message` and `on_reply`, defaulting to `print` and `input`.

</details>

## How it works

1. Capture a screenshot and the current tab list.
2. Request actions through OpenRouter's Responses API.
3. Execute the chosen functions and return their results.
4. Observe again after the action batch; repeat until the model calls `finish`.

<details>
<summary>Available actions</summary>

The 24 actions are ordinary functions grouped in `Actions`:

| Interaction | Actions |
| --- | --- |
| Navigation | `navigate`, `back`, `forward`, `reload` |
| Pointer | `click`, `double_click`, `right_click`, `hover`, `mouse_down`, `mouse_up`, `drag` |
| Scroll and keyboard | `scroll`, `type_text`, `press_key`, `key_down`, `key_up` |
| Tabs | `list_tabs`, `new_tab`, `switch_tab`, `close_tab` |
| Timing and conversation | `wait`, `send_message`, `wait_for_reply`, `finish` |

Coordinates use screenshot CSS pixels. The viewport defaults to 1280×800; set `w` and `h`
on `WebAgent` to change it. Typing targets the focused control, with 10 ms between characters.
Tab indices come from the latest observation and can shift after closing a tab.

</details>

<details>
<summary>History and error recovery</summary>

The first screenshot is a user message. After each action batch, one screenshot and
the current tab metadata accompany the last tool result. Each action returns:

```python
{"state": "success", "output": ...}
{"state": "error", "output": "..."}
```

Errors give the model feedback to recover. Missing tool calls receive a reminder.
Incomplete responses are discarded and retried without executing partial actions.
Retries count against `max_steps`; reaching the limit stops an unfinished task.
A successful `finish` returns the final answer.

History retains earlier screenshots and reasoning to preserve the cacheable prefix.
Context grows with the task, and cache hits depend on the provider.

</details>

<details>
<summary>Scope</summary>

The agent has no DOM-reading tool, accessibility-tree parser, generated-code executor,
upload/download manager, or OS controls. The action dictionary limits callable functions;
it does not isolate the browser from websites or restrict ordinary clicks and typing.

</details>

## Development

Start with [agent.py](agent.py), [cursor.py](cursor.py), or the [demo recorder](demo/record.py).
Tests live in [tests/](tests/).

```bash
python -m unittest discover -s tests -v
python -m tests.test_live  # Optional paid OpenRouter test.
```

The local suite uses Chromium and a local Responses fixture to check actions, tabs, cleanup,
tool results, and recovery. The live test completes a signup form and checks its result.
