"""In-page JavaScript used by agent.py.

Chromium draws an open single-choice <select> in a popup outside the page. Screenshots show the
popup, but page.mouse events go to the page under it, so a click on an option closes the popup
without choosing and lands on whatever lies underneath.
"""

# Runs before a click at (x, y) in CSS pixels and returns true when the click must not be sent.
# On a single-choice <select>, it focuses the select, lists the options in the page where the
# popup would be, and returns true. A click on an option row chooses it and fires input and
# change, as the popup does. As in headed Chromium on Linux, a click anywhere else, including the
# select, only closes the list and does not reach the page. A key press, a scroll that moves the
# select, and resizing or leaving the window close it too.
SELECT_CLICK = """([x, y]) => {
    const open = document.querySelector('mini-agent-select-list');
    const target = document.elementFromPoint(x, y);
    if (open && target === open) return false;  // a row handles the click
    open?.close();
    if (open) return true;  // anything else only closes the list
    const select = target?.closest('select');
    if (!select || select.multiple || select.size > 1 || select.matches(':disabled')) return false;
    select.focus({preventScroll: true});
    const list = document.createElement('mini-agent-select-list');
    const types = ['mousedown', 'keydown', 'scroll', 'resize', 'blur'];
    const close = ({type, target: source}) => {
        if (source === list || type === 'scroll' && !source.contains(select)) return;
        if (type !== 'blur' || source === window) list.close();  // keys then act on the select
    };
    list.close = () => {
        list.remove();
        types.forEach(type => removeEventListener(type, close, true));
    };
    types.forEach(type => addEventListener(type, close, true));
    list.addEventListener('mousedown', event => event.preventDefault());  // keep the focus
    const style = getComputedStyle(select), box = select.getBoundingClientRect();
    list.style.cssText = 'all:initial;position:fixed;left:0;z-index:2147483646;overflow-y:auto;' +
        'overscroll-behavior:contain;box-sizing:border-box;background:#fff;color:#000;' +
        'border:1px solid #767676';
    Object.assign(list.style, {font: style.font, minWidth: `${box.width}px`});
    const root = list.attachShadow({mode: 'open'});
    root.appendChild(document.createElement('style')).textContent = `
        div { padding: 4px calc(${style.paddingLeft} + 4px); min-height: 1lh; white-space: pre;
              cursor: default; }
        div:hover { background: #e8e8e8; }
        div.group { background: none; font-weight: bold; }
        div.grouped { padding-left: calc(${style.paddingLeft} + 24px); }
        div.selected { background: #1967d2; color: #fff; }
        div.disabled { background: none; color: #8c8c8c; }`;
    const row = (text, ...classes) => {
        const div = root.appendChild(document.createElement('div'));
        div.textContent = text;
        div.className = classes.filter(Boolean).join(' ');
        return div;
    };
    let group = null;
    for (const option of select.options) {  // group labels are rows that cannot be chosen
        const parent = option.closest('optgroup');
        if (parent && parent !== group) row(parent.label, 'group', parent.disabled && 'disabled');
        group = parent;
        if (option.hidden) continue;
        const disabled = option.matches(':disabled');  // also inside a disabled optgroup
        const state = disabled ? 'disabled' : option.selected && 'selected';
        row(option.label, parent && 'grouped', state).onclick = () => {
            if (disabled) return;
            list.close();
            if (option.selected) return;
            option.selected = true;
            select.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
            select.dispatchEvent(new Event('change', {bubbles: true}));
        };
    }
    document.documentElement.appendChild(list);
    // Open below the select, or above it when the list does not fit and there is more room.
    // When it is too wide for the window, line it up with the select's right edge instead.
    const below = innerHeight - box.bottom, up = list.offsetHeight > below && box.top > below;
    list.style.maxHeight = `${up ? box.top : below}px`;
    list.style.top = `${up ? box.top - list.offsetHeight : box.bottom}px`;
    const width = list.offsetWidth, fits = box.left + width <= innerWidth;
    list.style.left = `${fits ? box.left : Math.max(0, box.right - width)}px`;
    const chosen = root.querySelector('.selected');  // as in Chromium: in view, at the bottom edge
    if (chosen) list.scrollTop = chosen.offsetTop + chosen.offsetHeight - list.clientHeight;
    return true;
}"""
