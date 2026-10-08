"""Paid booking runs: uv run tests/compare_runs.py --models MODEL --variants examples normalized.

Auditing stays outside the agent: native pointer events and the booking confirmation are
checked independently. The model receives only normal observations and action results.
"""

import argparse
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from openai import OpenAI
from demo.record import QuietHandler
from tests.compare_prompts import load_snapshot, prompt

TASK = ('Book the 2:00 pm Robotics Lab on October 10 for Alex Chen, alex@example.com. '
        'Agree to the terms, confirm the booking, and verify the visible confirmation. '
        'Use mouse clicks to select controls and type_text to enter the name and email. '
        'Call finish when the booking is verified.')


def trial(module, model, variant, folder, url, max_steps):
    agent = module.WebAgent(folder, action_space=module.get_action_space()).launch().connect()
    record = dict(model=model, variant=variant, actions=[], clicks=[])
    started = time.monotonic()
    try:
        page = agent.get_page()
        agent.ctx.add_init_script('''(() => {
            window.auditClicks = [];
            document.addEventListener('pointerdown', e => {
                const control = e.target.closest('input,button,label');
                auditClicks.push({x:e.clientX, y:e.clientY,
                    control:control ? (control.name || control.innerText.trim()) : null});
            }, true);
        })()''')
        page.goto(url)
        instructions, click_tool = prompt(module, variant, agent.w, agent.h)
        original_tools, original_observe = module.prepare_tools, agent.observe
        module.prepare_tools = lambda actions: [
            click_tool[0] if tool['name'] == 'click' else tool for tool in original_tools(actions)]

        def observe():
            text, image = original_observe()
            if variant in ('explicit', 'pixel', 'examples'):
                state = json.loads(text)
                state.update(viewport=dict(width=agent.w, height=agent.h),
                             coordinates='CSS pixels; origin is the top-left of this image',
                             image_center=dict(x=agent.w / 2, y=agent.h / 2))
                text = json.dumps(state)
            return text, image

        def after(step, action, result):
            record['actions'].append(dict(step=step, action=action, result=result))
            record['clicks'].extend(agent.get_page().evaluate('auditClicks.splice(0)'))
            print(model, variant, step, action['name'], action['arguments'], flush=True)

        agent.observe = observe
        with OpenAI(timeout=90, max_retries=0) as client:
            record['answer'] = module.run(agent, TASK, client, model, instructions,
                                         max_steps=max_steps,
                                         callbacks=[dict(type='after', function=after)])
        confirmation = agent.get_page().get_by_role('status', include_hidden=True)
        record['confirmation'] = confirmation.inner_text()
        record['pass'] = confirmation.is_visible() and all(
            value in record['confirmation'] for value in
            ('Robotics Lab', '2:00 pm', 'Alex Chen', 'alex@example.com'))
    except Exception as error:
        record['error'] = f'{type(error).__name__}: {error}'
    finally:
        agent.shutdown()
    record['seconds'] = round(time.monotonic() - started, 2)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+', default=['google/gemini-3.8-flash'])
    parser.add_argument('--variants', nargs='+', default=['examples', 'normalized'],
                        choices=['original', 'explicit', 'pixel', 'examples', 'normalized'])
    parser.add_argument('--max-steps', type=int, default=16)
    parser.add_argument('--output', type=Path, default=Path('tests/results/booking-prompts.json'))
    args = parser.parse_args()
    results = dict(task=TASK, max_steps=args.max_steps, trials=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0),
                                partial(QuietHandler, directory=str(ROOT / 'demo')))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='mini-web-agent-runs-') as temporary:
            folder = Path(temporary)
            for variant in args.variants:
                for model in args.models:
                    module = load_snapshot(
                        'normalized' if variant == 'normalized' else 'css', folder)
                    record = trial(module, model, variant, folder / (model.replace('/', '-')),
                                   f'http://127.0.0.1:{server.server_port}', args.max_steps)
                    results['trials'].append(record)
                    args.output.write_text(json.dumps(results, indent=2) + '\n')
                    print(model, variant, record.get('pass', record.get('error')), flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    main()
