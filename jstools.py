"""In-page JavaScript used by agent.py.

Chromium draws an open single-choice <select> in a popup outside the page. Screenshots show the
popup, but page.mouse events go to the page under it, so a click on an option closes the popup
without choosing and lands on whatever lies underneath.
"""

# Runs before a click at (x, y) in CSS pixels and returns true when the click must not be sent.
# On a single-choice <select>, it focuses the select, lists the options in the page where the
# popup would be, and returns true. A click on a row chooses that option and fires input and
# change, as the popup does. As in headed Chromium on Linux, a click anywhere else, including the
# select, only closes the list and does not reach the page. A key press closes it too.
SELECT_CLICK = """([x, y]) => {
    const open = document.querySelector('mini-agent-select-list');
    const target = document.elementFromPoint(x, y);
    if (open && target === open) return false;  // a row handles the click
    open?.close();
    if (open) return true;  // anything else only closes the list
    const select = target?.closest('select');
    if (!select || select.multiple || select.size > 1 || select.matches(':disabled')) return false;
    select.focus();
    const list = document.createElement('mini-agent-select-list');
    const types = ['mousedown', 'keydown'];  // keys close the list, then act on the select
    const outside = event => { if (event.target !== list) list.close(); };
    list.close = () => {
        list.remove();
        types.forEach(type => removeEventListener(type, outside, true));
    };
    types.forEach(type => addEventListener(type, outside, true));
    list.addEventListener('mousedown', event => event.preventDefault());  // keep the focus
    const style = getComputedStyle(select), box = select.getBoundingClientRect();
    list.style.cssText = 'all:initial;position:fixed;z-index:2147483646;overflow-y:auto;' +
        'box-sizing:border-box;background:#fff;color:#000;border:1px solid #767676';
    Object.assign(list.style, {font: style.font, left: `${box.left}px`,
                               minWidth: `${box.width}px`});
    const root = list.attachShadow({mode: 'open'});
    root.appendChild(document.createElement('style')).textContent = `
        div { padding: 4px calc(${style.paddingLeft} + 4px); min-height: 1lh; white-space: pre;
              cursor: default; }
        div:hover { background: #e8e8e8; }
        div.selected { background: #1967d2; color: #fff; }
        div.disabled { background: none; color: #8c8c8c; }`;
    for (const option of select.options) {
        const row = root.appendChild(document.createElement('div'));
        row.textContent = option.label;
        row.className = option.disabled ? 'disabled' : option.selected ? 'selected' : '';
        row.onclick = () => {
            if (option.disabled) return;
            list.close();
            if (option.selected) return;
            option.selected = true;
            select.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
            select.dispatchEvent(new Event('change', {bubbles: true}));
        };
    }
    document.documentElement.appendChild(list);
    // Open below the select, or above it when the list does not fit and there is more room.
    const below = innerHeight - box.bottom, up = list.offsetHeight > below && box.top > below;
    list.style.maxHeight = `${up ? box.top : below}px`;
    list.style.top = `${up ? box.top - list.offsetHeight : box.bottom}px`;
    root.querySelector('.selected')?.scrollIntoView({block: 'nearest'});
    return true;
}"""
