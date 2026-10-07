"""Optional animated cursor visualization for the agent's action callback."""

import json
from playwright.sync_api import Error

POINTER_ACTIONS = {
    "click", "double_click", "right_click", "hover", "drag", "mouse_down", "mouse_up", "scroll",
}

CURSOR_SCRIPT = """async ({name, args}) => {
    let cursor = document.querySelector('mini-agent-cursor');
    if (!cursor?.shadowRoot?.querySelector('.aura')) {
        const left = cursor?.style.left || '24px', top = cursor?.style.top || '24px';
        cursor?.remove();
        cursor = document.createElement('mini-agent-cursor');
        cursor.style.cssText = `all:initial;position:fixed;left:${left};top:${top};` +
            'width:30px;height:30px;pointer-events:none;z-index:2147483647;';
        const root = cursor.attachShadow({mode: 'open'});
        root.innerHTML = `<style>
            :host { pointer-events: none !important; }
            .aura { position:absolute;left:-32px;top:-32px;width:64px;height:64px;opacity:.7;
                    background:radial-gradient(circle,#bba2b633,#bba2b614 40%,transparent 70%); }
            svg { position:absolute;left:-4px;top:-2px;overflow:visible;
                  filter:drop-shadow(0 1px 2px #05040a40) drop-shadow(0 0 6px #a08c9c26);
                  transform-origin:4px 2px; }
            .pulse { position:absolute;left:-12px;top:-12px;width:24px;height:24px;
                     border:1px solid #b9aeb799;border-radius:50%;box-sizing:border-box; }
        </style><span class="aura" aria-hidden="true"></span>
        <svg width="30" height="30" viewBox="0 0 30 30" aria-hidden="true">
            <defs><linearGradient id="fill" x1="0" y1="0" x2="1" y2="1">
                <stop stop-color="#756a72" stop-opacity=".28"/>
                <stop offset="1" stop-color="#242128" stop-opacity=".58"/>
            </linearGradient></defs>
            <path d="M4 2 Q3.6 2 4.1 3.7 L10.4 24.4 Q11.1 26.8 12.6 24.8 L16.5 17.2
                     Q16.9 16.5 17.6 16.2 L25.2 12.7 Q27.4 11.4 24.9 10.4 L5.8 2.5 Q4.4 2 4 2 Z"
                  fill="url(#fill)" stroke="#b9aeb7" stroke-opacity=".7" stroke-width="1.8"
                  stroke-linejoin="round"/>
        </svg>`;
        document.documentElement.appendChild(cursor);
    }
    const root = cursor.shadowRoot;
    const arrow = root.querySelector('svg');
    const aura = root.querySelector('.aura');
    const oldX = parseFloat(cursor.style.left), oldY = parseFloat(cursor.style.top);
    const x = args.x2 ?? args.x ?? oldX, y = args.y2 ?? args.y ?? oldY;
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const frames = [{left: `${oldX}px`, top: `${oldY}px`}];
    const targets = name === 'drag' ? [[args.x1, args.y1], [x, y]] : [[x, y]];
    let fromX = oldX, fromY = oldY;
    for (const [toX, toY] of targets) {
        const dx = toX - fromX, dy = toY - fromY, distance = Math.hypot(dx, dy) || 1;
        for (let i = 1; i < 16; i++) {
            const t = i / 16, bend = Math.sin(t * Math.PI * 2) * Math.min(distance * .08, 24);
            frames.push({left: `${fromX + dx * t - dy / distance * bend}px`,
                         top: `${fromY + dy * t + dx / distance * bend}px`});
        }
        frames.push({left: `${toX}px`, top: `${toY}px`});
        [fromX, fromY] = [toX, toY];
    }
    cursor.style.left = `${x}px`;
    cursor.style.top = `${y}px`;
    if (!reduced && (x !== oldX || y !== oldY || name === 'drag')) {
        await cursor.animate(frames, {duration: name === 'drag' ? 360 : 180,
                                     easing: 'cubic-bezier(.2,.8,.2,1)'}).finished;
    }
    arrow.style.transform = name === 'mouse_down' ? 'scale(.9)' : '';
    aura.style.transform = name === 'mouse_down' ? 'scale(.85)' : '';
    if (['click', 'double_click', 'right_click'].includes(name) && !reduced) {
        const pulse = document.createElement('div');
        pulse.className = 'pulse';
        root.appendChild(pulse);
        const timing = {duration: 280, iterations: name === 'double_click' ? 2 : 1};
        const ripple = pulse.animate([{transform: 'scale(.35)', opacity: .65},
                                      {transform: 'scale(1.8)', opacity: 0}], timing);
        arrow.animate([{transform: 'scale(1)'}, {transform: 'scale(.87)', offset: .25},
                       {transform: 'scale(1.03)', offset: .7},
                       {transform: 'scale(1)'}], timing);
        aura.animate([{transform: 'scale(1)', opacity: .7},
                      {transform: 'scale(.82)', opacity: 1, offset: .25},
                      {transform: 'scale(1.3)', opacity: .4, offset: .7},
                      {transform: 'scale(1)', opacity: .7}], timing);
        await ripple.finished;
        pulse.remove();
    }
}"""


def show_cursor(agent, step: int, action: dict, result: dict | None) -> None:
    """Animate the pointer; use partial(show_cursor, agent) as the callback."""
    if (result and result["state"] != "success") or action["name"] not in POINTER_ACTIONS:
        return
    try:
        arguments = action["arguments"]
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        agent.get_page().evaluate(CURSOR_SCRIPT, {"name": action["name"], "args": arguments})
    except (Error, ValueError, TypeError):
        pass  # A closing or navigating page should not let optional visuals interrupt the task.
