# SPDX-License-Identifier: GPL-3.0-or-later
"""Install notices, readable before setup applies any changes."""
import html

from opos import notices

STYLE = """
#jaryo-notices{--notice-fg:#000;--notice-bg:#fff;background:var(--notice-bg);color:var(--notice-fg);padding:1rem;font-family:Georgia,'Times New Roman',serif;overflow-wrap:anywhere;line-height:1.5}
@media(prefers-color-scheme:dark){#jaryo-notices{--notice-fg:#fff;--notice-bg:#000}}
#jaryo-notices header{border-block:3px double var(--notice-fg);padding:.5rem 0;font-family:system-ui,sans-serif;font-size:.875rem}
#jaryo-notices h2{font-size:2.25rem;line-height:1.1;margin:1rem 0}
#jaryo-notices p,#jaryo-notices dd{max-width:36rem}
#jaryo-notices .notice-warning{border:3px double var(--notice-fg);padding:.75rem}
#jaryo-notices details{border-top:1px solid var(--notice-fg)}
#jaryo-notices summary{cursor:pointer;min-height:48px;padding:.75rem 0;font:inherit;font-weight:bold}
#jaryo-notices dt{border-top:1px solid var(--notice-fg);padding-top:.75rem;font-weight:bold}
#jaryo-notices dd{margin:.25rem 0 1rem}
#jaryo-notices ul{padding-left:1.25rem}
#jaryo-notices footer{position:sticky;bottom:0;background:var(--notice-bg);padding:.5rem 0;padding-bottom:max(.5rem,env(safe-area-inset-bottom));border-top:3px solid var(--notice-fg)}
#jaryo-notices a{display:block;min-height:48px;padding:.75rem;text-align:center;background:var(--notice-fg);color:var(--notice-bg);font:inherit;box-sizing:border-box}
#jaryo-notices :focus-visible{outline:3px solid var(--notice-fg);outline-offset:3px}
"""


def fragment():
    data = notices.notices()
    parts = ['<section id="jaryo-notices" aria-labelledby="jaryo-notices-title">',
             '<header>Old Phone OS · First run</header>',
             '<h2 id="jaryo-notices-title" tabindex="-1">About this software</h2>',
             '<p>Read before you continue. This setup runs within your existing operating system.</p>',
             f'<p class="notice-warning">{html.escape(data["disclaimer"])}</p>',
             f'<p>{html.escape(notices.INSTALL_SCOPE)}</p>']
    # Risks remain visible; native disclosures need no script or network.
    for title, fields in (
        ('Before installing', ('risks', 'restore_stock')),
        ('Privacy and tested devices', ('privacy', 'tested_devices')),
        ('About this build', ('version', 'build', 'license', 'source_url',
                              'third_party', 'affiliation', 'contact', 'copyright')),
    ):
        opened = ' open' if fields[0] == 'risks' else ''
        parts.append(f'<details{opened}><summary>{title}</summary><dl>')
        for key in fields:
            value = data[key]
            text = ('<ul>' + ''.join(f'<li>{html.escape(str(v))}</li>' for v in value) + '</ul>'
                    if isinstance(value, (list, tuple)) else html.escape(str(value)))
            parts.append(f'<dt data-notice="{key}">{html.escape(notices._TITLES[key])}</dt><dd>{text}</dd>')
        parts.append('</dl></details>')
    parts.append('<footer><a id="notices-continue" href="#profile">Continue to setup</a></footer></section>')
    return ''.join(parts)
