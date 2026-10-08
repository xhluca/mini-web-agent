# Coordinate prompting comparison

On October 7, 2026, we tested whether clearer prompts could make screenshot clicks work
with the original CSS-pixel actions, without converting coordinates in the agent.

The key permitted only Gemini models. OpenRouter rejected GPT-6.1 Sol, Claude Sonnet 5.5,
and Qwen 3.8 Max under its model/provider guardrails. Those requests are access errors,
not grounding failures. This comparison cannot establish a default for other providers.

## Isolated clicks

Each model saw the same three screenshots: a time selector and a terms checkbox at
1280×800, and an email field at 960×1200. Each request required one `click` tool call.
Browser hit-testing checked whether the proposed coordinates reached the intended input
or its associated label. Target geometry was never sent to the model.

| Model | Original CSS | Conversion instructions | Pixel bounds and corners | Worked examples | 0–1000 contract |
| --- | ---: | ---: | ---: | ---: | ---: |
| Gemini 3.8 Flash | 0/6 | 2/6 | 2/6 | 1/3 | 6/6 |
| Gemini 3.7 Flash | 0/3 | — | 1/3 | 1/3 | 3/3 |
| Gemini 3.5 Flash | 0/3 | — | 0/3 | 0/3 | 3/3 |

These are target hits, not merely tool calls that returned successfully. The table excludes
the eight-request access pilot; its two Gemini results and six access errors are retained
in the logs. There were 51 subsequent trials. Repeats used provider defaults, without a seed.

The prompt variants were:

- **Original CSS:** the original instructions and function schemas.
- **Conversion instructions:** explicit CSS units in the prompt and x/y descriptions,
  viewport dimensions in observations, and the formula for converting both axes.
- **Pixel bounds and corners:** pixel-only wording, numeric bounds in the schema, and
  image corners and center in the observation. No normalized coordinates were mentioned.
- **Worked examples:** conversion instructions plus examples for landscape and portrait
  screenshots. Example points were unrelated to the controls being scored.
- **0–1000 contract:** the existing normalized action contract and coordinate conversion.

Gemini repeatedly returned normalized coordinates despite CSS instructions. Some responses
converted x but left y normalized. The checkbox and portrait email field exposed mistakes
that could still hit a wide button by chance.

## Complete agent runs

We also ran the booking task through the existing `run()` loop, with all browser actions
available and no forced tool choice. Each run had a fresh browser profile and a 16-turn cap.
The task requested mouse clicks for controls. Native pointer events were logged separately;
the agent received only its normal screenshots, observations, and action results.

| Model | CSS with worked examples | 0–1000 contract |
| --- | --- | --- |
| Gemini 3.8 Flash | Unfinished after 16 turns | Verified in 10 turns |
| Gemini 3.7 Flash | Verified in 15 turns | Verified in 10 turns |
| Gemini 3.5 Flash | Unfinished after 16 turns | Verified in 12 turns |

Success required the visible confirmation to contain the requested workshop, time, name,
and email. A model's claim that it finished was insufficient. The successful CSS run recovered
from three clicks on non-control areas. All three normalized runs had zero non-control clicks.

One CSS trial was repeated after the test's click audit was lost on a reload. Unfinished
runs also exposed a scorer timeout when the confirmation placeholder was hidden. Both test
issues are fixed; the raw excluded trial and verification errors remain in the logs.

## What this supports

Prompting helped one model recover and complete the task. These trials did not establish
a reliable prompt-only replacement for coordinate conversion. They also do not prove that
prompting cannot solve it: the sample covers three scenes, one website, and one model family.

We left the runtime implementation unchanged. Testing GPT, Claude, and Qwen with the same
scenes requires an OpenRouter key that permits those models and providers.

## Reproduce

These scripts make paid API requests using `OPENAI_API_KEY` and `OPENAI_BASE_URL`. They use
only the project's existing dependencies and a full Playwright Chromium installation.
Historical source snapshots keep CSS actions (`79408a8`) and normalized actions (`d431b06`)
consistent with the source shown to the model. Run them from a Git checkout.

```bash
uv run tests/compare_prompts.py --models google/gemini-3.8-flash \
  --variants original explicit pixel examples normalized --repeats 2

uv run tests/compare_runs.py --models google/gemini-3.8-flash \
  --variants examples normalized --max-steps 16
```

Pass additional model IDs to `--models` to extend the comparison. The default output paths
can be overridden with `--output` to preserve the recorded results.

The [isolated-click logs](results/coordinate-prompts.json) include coordinates, token usage,
timings, and hits under both interpretations. The [booking logs](results/booking-prompts.json)
include native clicks, action results, and independently checked confirmations.
