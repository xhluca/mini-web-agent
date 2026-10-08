"""Paid coordinate comparison: uv run tests/compare_prompts.py --repeats 2.

Models see identical screenshots and no target geometry. The browser supplies the scoring
oracle after each response. Historical source snapshots keep the action contracts consistent.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from http.server import ThreadingHTTPServer
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from openai import OpenAI
from agent import WebAgent, get_action_space, screenshot
from demo.record import QuietHandler

MODELS = ['google/gemini-3.8-flash', 'openai/gpt-6.1-sol',
          'anthropic/claude-sonnet-5.5', 'qwen/qwen3.8-max-0902']
REFS = {'css': '79408a8', 'normalized': 'd431b06'}
CASES = [
    ('time', 1280, 800, 'Click the 2:00 pm time slot for Robotics Lab.',
     'input[name=robotics][value="2:00 pm"]'),
    ('terms', 1280, 800, 'Click the checkbox agreeing to the workshop terms.',
     'input[name=terms]'),
    ('email', 960, 1200, 'Click the Email address input to focus it.',
     'input[name=email]'),
]


def load_snapshot(kind, folder):
    source = subprocess.run(['git', 'show', f'{REFS[kind]}:agent.py'], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout
    path = folder / f'agent_{kind}.py'
    path.write_text(source)
    spec = importlib.util.spec_from_file_location(f'agent_{kind}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prompt(module, variant, width, height):
    instructions = module.get_instructions()
    tools = module.prepare_tools({'click': module.Actions.click})
    if variant in ('explicit', 'examples'):
        instructions = instructions.replace(
            'Use screenshot coordinates in CSS pixels and current tab indices.',
            'Use CSS-pixel x,y coordinates, never 0–1000 normalized coordinates.\n'
                'Read the screenshot width and height from the viewport in the observation.\n'
                'If you locate a normalized point, convert x*width/1000 and y*height/1000 first.\n'
                'Use current tab indices.')
        if variant == 'examples':
            instructions = instructions.replace('Use current tab indices.',
                'Convert BOTH axes before calling a pointer tool. Worked examples:\n'
                '1280x800 image: normalized (250,750) means click(x=320,y=600).\n'
                '960x1200 image: normalized (750,250) means click(x=720,y=300).\n'
                'Do not pass the normalized point directly. Use current tab indices.', 1)
        for axis, dimension in (('x', 'width'), ('y', 'height')):
            tools[0]['parameters']['properties'][axis]['description'] = (
                f'{axis} coordinate in screenshot CSS pixels, from 0 to viewport {dimension}. '
                'Do not return normalized 0–1000 coordinates.')
    elif variant == 'pixel':
        instructions = instructions.replace(
            'Use screenshot coordinates in CSS pixels and current tab indices.',
            'Use x,y pixel coordinates measured from the top-left of the entire screenshot.\n'
            'The observation includes its pixel width and height. Use current tab indices.')
        tools[0]['description'] = (
            f'Click a point in the {width} by {height} pixel screenshot. '
            'x is the horizontal pixel position; y is the vertical pixel position.')
        for axis, limit in (('x', width), ('y', height)):
            tools[0]['parameters']['properties'][axis].update(
                minimum=0, maximum=limit - 1,
                description=f'{axis} position in screenshot pixels, between 0 and {limit - 1}.')
    return instructions, tools


def request(client, model, variant, repeat, scene, instructions, tools):
    name, width, height, task, selector, page, image, state = scene
    observation = dict(state)
    if variant in ('explicit', 'pixel', 'examples'):
        observation['viewport'] = dict(width=width, height=height)
        observation['coordinates'] = 'CSS pixels; origin is the top-left of this image'
        observation['image_center'] = dict(x=width / 2, y=height / 2)
    if variant == 'pixel':
        observation['image_corners'] = {
            'top_left': [0, 0], 'top_right': [width - 1, 0],
            'bottom_left': [0, height - 1], 'bottom_right': [width - 1, height - 1]}
    history = [dict(role='system', content=instructions), dict(role='user', content=task),
               dict(role='user', content=[dict(type='input_text', text=json.dumps(observation)),
                                         dict(type='input_image', image_url=image)])]
    record = dict(model=model, variant=variant, case=name, repeat=repeat,
                  viewport=dict(width=width, height=height))
    started = time.monotonic()
    try:
        response = client.responses.create(
            model=model, input=history, tools=tools, store=False, parallel_tool_calls=False,
            tool_choice=dict(type='function', name='click'), max_output_tokens=8192)
        calls = [item for item in response.output if item.type == 'function_call']
        if response.status != 'completed' or len(calls) != 1 or calls[0].name != 'click':
            raise RuntimeError(f'{response.status}, {len(calls)} tool calls')
        args = json.loads(calls[0].arguments)
        if not all(isinstance(args.get(k), (int, float)) and math.isfinite(args[k])
                   for k in ('x', 'y')):
            raise ValueError('Click did not contain finite numeric x and y')
        record.update(arguments=args, usage=response.usage.model_dump(exclude_none=True))
    except Exception as error:
        record['error'] = f'{type(error).__name__}: {error}'
    record['seconds'] = round(time.monotonic() - started, 2)
    return record


def hit(page, selector, x, y):
    return page.evaluate('''({selector, x, y}) => {
        const expected = document.querySelector(selector);
        const actual = document.elementFromPoint(x, y);
        return actual === expected || expected.contains(actual) ||
            actual?.closest('label')?.control === expected;
    }''', dict(selector=selector, x=x, y=y))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+', default=MODELS)
    parser.add_argument('--variants', nargs='+', default=['original', 'explicit', 'normalized'],
                        choices=['original', 'explicit', 'pixel', 'examples', 'normalized'])
    parser.add_argument('--cases', nargs='+', default=[case[0] for case in CASES],
                        choices=[case[0] for case in CASES])
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--output', type=Path,
                        default=Path('tests/results/coordinate-prompts.json'))
    args = parser.parse_args()
    output = dict(models=args.models, variants=args.variants, repeats=args.repeats,
                  source_refs=REFS, trials=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0),
                                partial(QuietHandler, directory=str(ROOT / 'demo')))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='mini-web-agent-prompts-') as temporary:
            folder = Path(temporary)
            modules = {kind: load_snapshot(kind, folder) for kind in REFS}
            agent = WebAgent(folder / 'chrome', action_space=get_action_space()).launch().connect()
            try:
                scenes = []
                for name, width, height, task, selector in CASES:
                    if name not in args.cases:
                        continue
                    page = agent.ctx.new_page()
                    page.set_viewport_size(dict(width=width, height=height))
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    if name != 'time':
                        page.locator('input[name=robotics][value="2:00 pm"]').check()
                        page.get_by_role('button', name='Book workshop').nth(1).click()
                        page.locator('input[name=name]').fill('Alex Chen')
                    image = screenshot(page)
                    state = dict(active_tab=agent.ctx.pages.index(page),
                                 tabs=modules['css'].Actions.list_tabs(page))
                    scenes.append((name, width, height, task, selector, page, image, state))
                with OpenAI(timeout=90, max_retries=0) as client:
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        jobs = {}
                        for variant in args.variants:
                            module = modules['normalized' if variant == 'normalized' else 'css']
                            for model in args.models:
                                for repeat in range(args.repeats):
                                    for scene in scenes:
                                        instructions, tools = prompt(
                                            module, variant, scene[1], scene[2])
                                        job = pool.submit(request, client, model, variant, repeat,
                                                          scene, instructions, tools)
                                        jobs[job] = scene
                        for job in as_completed(jobs):
                            record = job.result()
                            name, width, height, task, selector, page, image, state = jobs[job]
                            if 'arguments' in record:
                                x, y = (record['arguments'][key] for key in ('x', 'y'))
                                record['css_hit'] = hit(page, selector, x, y)
                                record['normalized_hit'] = hit(
                                    page, selector, x * width / 1000, y * height / 1000)
                                record['pass'] = record[
                                    'normalized_hit' if record['variant'] == 'normalized'
                                    else 'css_hit']
                            output['trials'].append(record)
                            args.output.write_text(json.dumps(output, indent=2) + '\n')
                            print(record['model'], record['variant'], record['case'],
                                  record.get('arguments'), record.get('pass', record.get('error')),
                                  flush=True)
            finally:
                agent.shutdown()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    for model in args.models:
        for variant in args.variants:
            trials = [r for r in output['trials']
                      if r['model'] == model and r['variant'] == variant]
            print(model, variant, f"{sum(r.get('pass', False) for r in trials)}/{len(trials)}",
                  'errors:', sum('error' in r for r in trials))


if __name__ == '__main__':
    main()
