# SPDX-License-Identifier: GPL-3.0-or-later
"""The shared document for an everyday app. Same tokens and type as the first-run page.

Home sits in the thumb pane. The list pane is the left column, and Hand: Left swaps the two.
Nothing is placed in the hinge column.
"""
from html import escape

from opos import secure
from opos.firstrun_page import CSS as BASE_CSS

EXTRA_CSS = """
#gap{display:none}
#app-list,#rows{list-style:none;margin:0;padding:0}
#app-list a,#rows button,#rows a{display:flex;align-items:center;min-height:48px;width:100%;color:inherit;text-decoration:underline;background:var(--bg);text-align:left}
.card{margin:4px 0 12px}
.nav .btn,button.main{min-height:56px}
p,li,h1,h2,a,button{overflow-wrap:anywhere}
button{white-space:normal}
main{max-width:1392px;margin:0 auto;transition:opacity .2s cubic-bezier(0,0,.2,1)}
@media(prefers-reduced-motion:reduce){main{transition:none}}
@media(min-width:1000px){
main{display:grid;grid-template-columns:minmax(0,1fr) var(--hinge) minmax(0,1fr)}
#gap{display:block;grid-column:2}
html[data-hand=left] .l{grid-column:3}
html[data-hand=left] .r{grid-column:1}
}
"""

_DOC = """<!doctype html>
<html lang="en" data-opos-app>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="opos-token" content="@@TOKEN@@">
<title>@@TITLE@@ - Old Phone OS Interface</title>
<style>@@CSS@@</style>
<script>try{var t=localStorage.getItem('jaryo_theme');if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t);var h=localStorage.getItem('jaryo_hand');if(h==='left')document.documentElement.setAttribute('data-hand','left')}catch(e){}</script>
</head>
<body>
<main>
<section class="l" id="list">
@@LIST@@
</section>
<div id="gap" aria-hidden="true"></div>
<section class="r" id="thumb">
@@THUMB@@
<div class="nav"><a id="home" class="btn" href="/">Home</a></div>
</section>
</main>
<script>
(function(){
document.addEventListener('keydown',function(e){
if(e.key!=='Escape')return;
var t=e.target;if(t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'))return;
location.href='/';
});
@@SCRIPT@@
})();
</script>
</body>
</html>
"""

ERROR_LIST = '<h1>This app could not open</h1><p>The phone and messages still work.</p>'
ERROR_THUMB = '<p>Go back to the front page.</p>'


def render(title, token, nonce, list_html, thumb_html, script=""):
    """A full page. list_html and thumb_html are trusted markup from the app, not from the owner."""
    html = (_DOC.replace("@@TITLE@@", escape(title))
            .replace("@@TOKEN@@", escape(token, quote=True))
            .replace("@@CSS@@", BASE_CSS + EXTRA_CSS)
            .replace("@@LIST@@", list_html)
            .replace("@@THUMB@@", thumb_html)
            .replace("@@SCRIPT@@", script))
    return secure.add_nonce(html, nonce)


def error_page(token, nonce):
    return render("This app could not open", token, nonce, ERROR_LIST, ERROR_THUMB)
