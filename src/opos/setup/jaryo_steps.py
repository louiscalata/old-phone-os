# SPDX-License-Identifier: GPL-3.0-or-later
import html
from opos import notices

STYLE = """
#jaryo-setup{--jaryo-fg:#1a1a1a;--jaryo-bg:#f3f0e8;color:var(--jaryo-fg);background:var(--jaryo-bg);font-family:Georgia,'Times New Roman',serif;overflow-wrap:anywhere}
@media(prefers-color-scheme:dark){#jaryo-setup{--jaryo-fg:#e6e2d9;--jaryo-bg:#000}}
#jaryo-setup[data-theme=light]{--jaryo-fg:#1a1a1a;--jaryo-bg:#f3f0e8}
#jaryo-setup[data-theme=dark]{--jaryo-fg:#e6e2d9;--jaryo-bg:#000}
#jaryo-setup h3,#jaryo-setup legend{font-family:'Arial Narrow',system-ui,sans-serif;font-size:1.5rem}
#jaryo-setup fieldset{min-width:0;border:1px solid var(--jaryo-fg)}
#jaryo-setup label{display:flex;align-items:center;gap:8px;min-height:48px;min-width:48px;margin:8px 0}
#jaryo-setup button{background:var(--jaryo-bg);color:var(--jaryo-fg);border:1px solid var(--jaryo-fg);min-height:48px;min-width:48px;border-radius:0;font-size:1rem;white-space:normal}
#jaryo-setup select{background:var(--jaryo-bg);color:var(--jaryo-fg);border:1px solid var(--jaryo-fg);min-height:48px;min-width:48px;max-width:100%;padding:8px;border-radius:0;font-size:1rem}
#jaryo-setup .jaryo-size-label{flex-wrap:wrap}
#jaryo-setup button:active{background:var(--jaryo-fg);color:var(--jaryo-bg)}
#jaryo-setup :focus-visible{outline:3px solid var(--jaryo-fg);outline-offset:3px}
#jaryo-setup .row{display:flex;flex-wrap:wrap;gap:8px}
#jaryo-summary{white-space:pre-line}
#jaryo-setup #j6{padding:1rem;max-width:36rem;margin-inline:auto}
#jaryo-setup #j6 .jaryo-kicker{font-family:'Arial Narrow',system-ui,sans-serif;font-size:.875rem;text-transform:uppercase;letter-spacing:.04em;border-top:1px solid var(--jaryo-fg);border-bottom:1px solid var(--jaryo-fg);padding:.5rem 0}
#jaryo-setup #jaryo-radio{border:1px solid var(--jaryo-fg);padding:1rem;font-family:system-ui,sans-serif}
#jaryo-setup #j6 [data-jwifi]{width:100%;border:3px solid var(--jaryo-fg);font-weight:bold}
#jaryo-edition label{align-items:flex-start;margin:0;padding:8px 0;border-bottom:1px solid var(--jaryo-fg)}
#jaryo-edition .jaryo-edition-text{display:flex;flex-direction:column;gap:2px}
#jaryo-edition .jaryo-kicker{font-family:'Arial Narrow',system-ui,sans-serif;font-size:.875rem;text-transform:uppercase;letter-spacing:.04em}
#jaryo-edition .jaryo-edition-name{font-size:1.5rem;font-weight:700}
#jaryo-setup #jaryo-use-edition{background:var(--jaryo-fg);color:var(--jaryo-bg);border:3px solid var(--jaryo-fg)}
#jaryo-setup #jaryo-use-edition:active{background:var(--jaryo-bg);color:var(--jaryo-fg)}
"""

OPTIONS = {
    'theme': [('auto', 'Follow the system'), ('light', 'Light'), ('dark', 'Dark')],
    'layout': [('phone', 'Phone'), ('messages', 'Messages'), ('helper', 'Helper'), ('settings', 'Settings')],
    'text_size': [('small', 'Small'), ('normal', 'Normal'), ('large', 'Large'), ('xlarge', 'Largest')],
    'device_type': [('auto', 'Auto'), ('slab', 'Slab'), ('foldable', 'Foldable'), ('slideout', 'Slide-out'), ('tablet', 'Tablet')],
    'language': [('en', 'English')],
    'wifi': [],
    'pin': [],
    'helper': [('on', 'On'), ('off', 'Off')],
}
TILE_SIZE_DEFAULTS = {'phone': 'medium', 'messages': 'medium', 'helper': 'small', 'settings': 'small'}
TILE_SIZES = (('small', 'Small'), ('medium', 'Medium'), ('wide', 'Wide'))
TITLES = ['Theme', 'Layout and favourite apps', 'Text size', 'Device type', 'Language', 'Wi-Fi', 'Lock PIN', 'Helper']
# Section ids are fixed per step, not per position: the approved order puts the lock PIN right after Wi-Fi
# (design decision 6), while j1-j7 keep the ids their tests already use. j8 is the JRY34 step.
IDS = {'theme': 'j1', 'layout': 'j2', 'text_size': 'j3', 'device_type': 'j4', 'language': 'j5', 'wifi': 'j6', 'helper': 'j7', 'pin': 'j8'}

# JRY32: "Choose your edition" (design decision 6). An edition is the existing device_type answer under its
# edition name, so prefs.STEPS, resume and skip-all are untouched: (value, name, kicker, one-line description).
EDITIONS = (
    ('slab', 'Pocket', 'Regular phones', 'A regular phone. One column, big buttons within thumb reach.'),
    ('foldable', 'Foldable', 'Folding phones', 'Two columns when the phone is open.'),
    ('slideout', 'Slide-out', 'Hardware keyboard', 'A landscape two-pane layout for a phone with a slide-out keyboard.'),
    ('tablet', 'Tablet', 'Big screens', 'A wider layout for big screens.'),
)
EDITION_DEFAULT = 'slab'
EDITION_INTRO = 'Each edition sets the layout for your device. The suggestion is based on the browser window, not hardware detection. You can change it later in Settings › Device type, where Pocket is called Slab.'


def _edition_panel():
    parts = ['<section class="step jaryo-step" id="jaryo-edition" data-jedition hidden>',
             '<fieldset aria-describedby="jaryo-error"><legend tabindex="-1">Choose your edition</legend>',
             f'<p>{html.escape(EDITION_INTRO)}</p>']
    for value, name, kicker, desc in EDITIONS:
        checked = ' checked' if value == EDITION_DEFAULT else ''
        shown = 'Suggested' if value == EDITION_DEFAULT else kicker
        parts.append(f'<label><input type="radio" name="edition" value="{html.escape(value, quote=True)}" data-jname="{html.escape(name, quote=True)}"{checked}> '
                     f'<span class="jaryo-edition-text"><span class="jaryo-kicker" data-jkicker="{html.escape(kicker, quote=True)}">{html.escape(shown)}</span>'
                     f'<span class="jaryo-edition-name">{html.escape(name)}</span><span class="jaryo-edition-desc">{html.escape(desc)}</span></span></label>')
    default_name = next(name for value, name, _, _ in EDITIONS if value == EDITION_DEFAULT)
    parts.extend(['</fieldset>',
                  '<div class="row"><button type="button" id="jaryo-customise">Customise instead</button>'
                  f'<button type="button" id="jaryo-use-edition">Use {html.escape(default_name)}</button></div>',
                  '</section>'])
    return parts


def fragment():
    parts = ['<section id="jaryo-setup" data-theme="auto" hidden>',
             '<p id="jaryo-error" role="alert" hidden></p>',
             '<button type="button" id="jaryo-retry" hidden>Try loading choices again</button>',
             '<div id="jaryo-disclaimer">', f'<p>{html.escape(notices.DISCLAIMER)}</p>', f'<p>{html.escape(notices.INSTALL_SCOPE)}</p>',
             '<details><summary>About this build</summary><dl>']
    for key, val in notices.notices().items():
        if key == 'disclaimer':
            continue
        text = '; '.join(map(str, val)) if isinstance(val, (list, tuple)) else str(val)
        parts.append(f'<dt data-notice="{html.escape(key, quote=True)}">{html.escape(notices._TITLES[key])}</dt><dd>{html.escape(text)}</dd>')
    parts.append('</dl></details></div>')
    parts.extend(_edition_panel())
    for i, (name, choices) in enumerate(OPTIONS.items()):
        title = html.escape(TITLES[i])
        parts.append(f'<section class="step jaryo-step" id="{IDS.get(name, f"j{i+1}")}" data-jstep="{html.escape(name, quote=True)}" hidden>')
        if name == 'wifi':
            parts.append('<p class="jaryo-kicker">First run · Connection</p>')
        if not choices:
            parts.append(f'<h3 tabindex="-1">{title}</h3>')
        if choices:
            parts.append(f'<fieldset aria-describedby="jaryo-error"><legend tabindex="-1">{title}</legend>')
            if name == 'layout':
                parts.append('<p id="jaryo-layout-note">Tile sizes set the starting layout; existing screen choices are kept. Favourite choices are recorded only and do not hide or reorder Home tiles.</p>')
            for value, label in choices:
                kind = 'checkbox' if name == 'layout' else 'radio'
                parts.append(f'<label><input type="{kind}" name="{html.escape(name, quote=True)}" value="{html.escape(value, quote=True)}"> {html.escape(label)}</label>')
                if name == 'layout':
                    control = 'jaryo-size-' + value
                    parts.append(f'<label class="jaryo-size-label" for="{control}">{html.escape(label)} tile size</label>'
                                 f'<select id="{control}" name="layout-size-{value}" data-jtile-size="{value}" aria-describedby="jaryo-layout-note">')
                    for size, size_label in TILE_SIZES:
                        selected = ' selected' if size == TILE_SIZE_DEFAULTS[value] else ''
                        parts.append(f'<option value="{size}"{selected}>{size_label}</option>')
                    parts.append('</select>')
            parts.append('</fieldset>')
        if name == 'language':
            parts.append('<p>More languages are not available yet</p>')
        if name == 'helper':
            parts.append('<p>You can change this in the Shade, the quick settings panel opened with the Shade button on the Home screen.</p>')
        if name == 'wifi':
            parts.append('<p id="jaryo-radio" role="status">Wi-Fi radio: not detected</p><p>Radio detection does not show whether you are connected.</p><p>Joining a network is not available in this build. Continue or skip without changing your connection.</p><button type="button" data-jwifi>Set up later in Settings › Network</button>')
        if name == 'pin':
            # JRY34: record-only, like Wi-Fi. The lock screen cannot check a PIN yet, so no digits are asked for or stored.
            parts.append('<p id="jaryo-pin-note">A lock PIN is not available in this build. The lock screen opens with the Unlock button.</p>'
                         '<p>No PIN is requested or saved. Continue or skip to save this choice for later.</p>'
                         '<button type="button" data-jpin>Set up later in Settings › Device</button>')
        parts.append('<div class="row"><button type="button" data-jskip>Skip</button><button type="button" data-jback>Back</button><button type="button" data-jnext>Next</button></div>')
        if i == 0:
            parts.append('<button type="button" id="jaryo-skip-all">Skip all</button>')
        parts.append('</section>')
    parts.extend(['<div id="jaryo-summary" tabindex="-1" hidden></div><button type="button" id="jaryo-summary-back" hidden>Back</button>',
                  f'<p id="jaryo-disclaimer-end" hidden>{html.escape(notices.DISCLAIMER)} {html.escape(notices.INSTALL_SCOPE)}</p>', '</section>'])
    return ''.join(parts)
