# Jaryo — the Old Phone OS Interface design language

> Historical design specification, drafted November 1, 2026. Sections 1–8 keep the original proposal and are not a current implementation checklist. Section 9 names the files that implement it, and the notes in section 13 record later changes. See the README for current behavior.

Status: draft 1, 2026-11-01. Normative words: MUST, MUST NOT, SHOULD. Applies to the kiosk page served by `opos.web.page()` and to the install wizard served by `opos.setup.wizard_page.page()`.

## 1. Name, intent, summary

**Jaryo** is the UI/UX language of OPOS: black-and-white text buttons, Windows-Phone-8 placement (big left headings, labelled tiles, A–Z list, pivots, bottom action row), a newspaper layout (masthead, serif headlines, thin rules, columns), and zoom-into-article transitions. It exists so a web kiosk on a 2 GB phone reads like a printed front page, works with one thumb or a keyboard, and never depends on colour, icons, downloads or a GPU.

Principles (in priority order; a lower one never overrides a higher one):

1. **Simple, but works.** Every screen works with the existing inline shell alone. Jaryo is an additive layer; if it throws, the phone still calls and texts (section 9, fail-open).
2. **Text only.** Every control is a `<button>`, `<a>`, `<input>`, `<select>` or `<label>` whose meaning is in its text. No icons, images, SVG glyphs, background images or mystery symbols.
3. **Two colours.** Exactly `#000` and `#fff`. Light mode is black on white; dark mode swaps them. Nothing else, ever, including greys, tints, shadows, gradients, filters and opacity used as colour.
4. **Editorial.** Masthead, serif headlines, condensed subheads, 1 px rules between sections, columns on wide screens, generous whitespace. Tiles read as section headlines.
5. **Zoom.** Home is the newspaper page; opening an app zooms into that article; back zooms out to the same tile; moving between pages zooms through. Snappy but graceful, transform/opacity only, interruptible, reduced motion honoured.

## 2. Palette and theme

### 2.1 Tokens

```css
:root{--jaryo-fg:#000;--jaryo-bg:#fff}
html[data-theme="dark"]{--jaryo-fg:#fff;--jaryo-bg:#000}
@media (prefers-color-scheme:dark){html:not([data-theme]){--jaryo-fg:#fff;--jaryo-bg:#000}}
```

- The ONLY colour literals permitted in `jaryo.css` are `#000`, `#fff`, `transparent`, `currentColor`, and the two variables. Tests grep for anything else (section 8).
- `html[data-theme]` MUST be `light` or `dark`. Absent attribute = follow `prefers-color-scheme`. `jaryo.js` MUST set the attribute before first paint from `localStorage['jaryo_theme']` (values `light|dark`; absent = system).
- Settings › Display MUST offer a text button `#jaryo-theme` whose label is `Theme: Light` / `Theme: Dark` / `Theme: System`; tapping cycles and persists to `localStorage['jaryo_theme']`.
- At install, the wizard writes the chosen default to `jaryo-setup.json` (section 6) and the server MUST render `<html data-theme="…">` from it so the first paint is right before any JS runs. If no install choice exists, the server omits the attribute.
- `color-scheme: light dark` MUST be set on `html` so native controls (select, checkbox, time input) follow the theme without custom painting.

### 2.2 Expressing state without colour

| State | Expression |
|---|---|
| Default button | 1px solid fg border, fg text on bg |
| Hover (pointer only) | underline |
| Focus (`:focus-visible`) | `outline:3px solid var(--jaryo-fg);outline-offset:3px` |
| Pressed / active / `[aria-pressed="true"]` | inverted fill: bg text on fg |
| Disabled / `[aria-disabled="true"]` | 1px dashed fg border, text keeps full fg (no opacity), label ends " unavailable" (existing `available()` in phone-a.js already appends it) |
| Toggle on / off | label text `…: On` / `…: Off` plus inverted fill when On |
| Low battery (≤15 %, `#sb-batt[data-low="true"]`) | text `LOW 12%` and `border:3px double var(--jaryo-fg)` |
| Charging | text `Charging` after the percent; no animation |
| Primary action in a row | 3px solid fg border (weight, not colour) |
| Error / notice | text in a box with 3px double border |
| Section boundary | 1px solid fg rule |
| Masthead boundary | 3px solid fg rule |

### 2.3 Neutralising existing colours (ids are kept, only paint changes)

`jaryo.css` is inlined LAST (section 9) so plain id selectors win by source order; `!important` is allowed only in this file and only for these overrides.

| Existing selector (web.py / phone-*.css) | Jaryo override |
|---|---|
| `html,body{background:#000;color:#fff}` | `background:var(--jaryo-bg);color:var(--jaryo-fg)` |
| `button{background:#2a2a2a;border:1px solid #444;border-radius:8px}` | `background:var(--jaryo-bg);color:var(--jaryo-fg);border:1px solid var(--jaryo-fg);border-radius:0` |
| `#phone{background:#111}` | `background:var(--jaryo-bg)` |
| `#meter`, `#meter-fill`, `#meter[data-tone=…]`, `pulse` keyframes, box-shadows | `#meter{background:transparent;border-bottom:1px solid var(--jaryo-fg)}#meter-fill{background:var(--jaryo-fg);box-shadow:none}#meter[data-tone]{animation:none}`; tone is shown as text in `#sb-batt` instead |
| `#meter-hit::after`, `#sheet-handle::after`, `#sheet-grip::after` (grey pills) | `display:none`; the shade opens from the visible `#sb` button (section 4.3) |
| `#scrim{background:rgba(0,0,0,.85)}` | `background:var(--jaryo-bg);opacity:1` (a solid page, the sidebar column is a page) |
| `#sheet{background:#1b1b1b}` | `background:var(--jaryo-bg);border-right:1px solid var(--jaryo-fg)` |
| `#sheet-title[data-tone="low"]{color:#e5484d}` etc. | `color:var(--jaryo-fg)`; low = `::after{content:" LOW"}` plus double border on `#sheet-title` |
| `#sb-batt[data-low="true"]{color:#ff7075}` | `color:inherit;border:3px double var(--jaryo-fg);padding:0 6px` |
| `#home-notice{background:#1e1e1e;border-left:4px solid #f2c14e}` | `background:var(--jaryo-bg);border:0;border-top:1px solid var(--jaryo-fg)` |
| `#goal-note{color:#f2c14e}`, `#goal-row label{color:#c8ccd2}`, `#notice{color:#8a8f98}`, `#notice[data-tone=call]{color:#30a46c}`, `#answer a{color:#9ecbff}` | `color:var(--jaryo-fg)`; links underlined |
| `#screen{background:#ddd;color:#000}` and `sc.style.filter='brightness(…)'` (web.py:507) | CSS: `background:var(--jaryo-bg);color:var(--jaryo-fg);border:1px solid var(--jaryo-fg);filter:none!important`. The `style.filter` line MUST also be deleted so the inline style is gone, not merely overridden. |
| `.box{background:#222}`, `#demo`, `.paused-row{border-bottom:#333}`, `.feature-bar/.feature-fill`, `#textsize{background:#111;border:#444}`, `#askbtn{background:#fff;color:#111}`, `#history polyline{stroke:#fff}` | bg/fg variables; `.feature-fill` becomes fg; the SVG polyline gets `stroke:currentColor` via CSS |
| `#sb{background:#000}`, `#sb-engine{opacity:.8}`, `#time-left{opacity:.85}`, `#history-label{opacity:.6}`, `#why{opacity:.9}`, `#events{opacity:.8}` | `background:var(--jaryo-bg);border-bottom:1px solid var(--jaryo-fg)`; all opacities `1` |
| `:focus-visible{outline:2px solid #fff}` (web.py:399, phone-b.css:42,53) | `outline:3px solid var(--jaryo-fg);outline-offset:3px` |
| Wizard: `#meter-fill{background:#30a46c}`, `.dot.*`, `#error{color:#e5484d}`, `a{color:#30a46c}` | wizard gets the same tokens; progress dots become text `pending/running/ok/failed/skipped` in a condensed face |

The `palette` CDP test (section 8) MUST verify that no visible element computes to anything but `rgb(0,0,0)`/`rgb(255,255,255)` after the overrides.

## 3. Type

System stacks only; nothing downloaded (`@font-face` is forbidden in Jaryo files).

```css
:root{
 --jaryo-serif:Georgia,'Iowan Old Style','Times New Roman',serif;
 --jaryo-cond:'Arial Narrow','Roboto Condensed',sans-serif-condensed,sans-serif;
 --jaryo-body:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
}
```

| Role | Face | Size | Weight / case | Used by |
|---|---|---|---|---|
| Masthead | serif | `2.25rem`, line-height 1.05 | 700 | `#lock-time`, `#jaryo-masthead` (Home title "Old Phone OS Interface"), `#sheet-title` |
| Headline | serif | `1.5rem`, lh 1.15 | 700 | tile labels (`#home button`), `#settings h2`, `#headline` |
| Subhead | condensed | `1.125rem`, lh 1.2 | 400, uppercase, `letter-spacing:.04em` | pivot headers, `#settings h3`, `summary`, A–Z letters, dateline `#lock-date` |
| Body | body | `1rem`, lh 1.45 | 400 | everything else |
| Caption | condensed | `.875rem`, lh 1.3 | 400 | `#sb`, `#notice`, `#history-label`, `#helper-where` |

- All sizes MUST be `rem`. The root size stays the existing `html[data-text="n|l|xl"]{font-size:100%|125%|150%}` (phone-b.css:48). Jaryo MUST NOT set `font-size` on `html` except through that attribute.
- **200 % rule.** With `html{font-size:200%}` at 360×640 nothing clips or scrolls horizontally: no fixed heights on text containers, `overflow-wrap:anywhere` on labels, tiles grow in height (`min-height`, never `height`), the bottom action row wraps to two rows rather than shrinking text.
- Line length: body text columns MUST be capped at `max-width:36rem` (≈65–75 characters).
- Rules: `1px solid var(--jaryo-fg)` between sections (`#home-favourites > *`, `#settings section`, `#qs` groups, `#apps-list li`); `3px solid var(--jaryo-fg)` under the masthead (`#jaryo-masthead`, `#lock-time`, `#sheet-title`).
- Columns: ≥840 px wide, Home and All apps use `column-count` style grids of 3–4 tracks with a 1px fg vertical rule drawn by `border-left` on every column but the first; Settings/Helper keep the existing two-pane grids (phone-b.css) and only restyle them.

## 4. Layout per screen

Global rules: every gesture has a visible button; every target ≥48×48 CSS px with ≥8 px gaps; DOM order = reading order = Tab order (CSS changes column count only; `order:` is forbidden, test_uxp12_home enforces it); a bottom action row (`.jaryo-actions`, `position:sticky;bottom:0`, padded by `env(safe-area-inset-bottom)`) sits in thumb reach on every screen; status bar `#sb` stays 28 px + inset and is a `<button>` row, not a decoration.

### 4.1 Lock (`#lock`, existing ids `#lock-time #lock-date #lock-batt #lock-notice #unlock #lock-ask #lock-call #lock-hangup`, new `#lock-power`)

```
360x640 portrait                         1280x720 landscape
+----------------------------------+     +---------------------------------------------+
| 14:32                 (masthead) |     | 14:32                 | About 30 min left   |
| ================================ |     | Sunday, 1 November    | Saving power for    |
| Sunday, 1 November  (dateline)   |     | ===================== | calls, messages     |
|                                  |     | 23%  LOW              | New notice          |
| 23%  LOW            (dbl border) |     |                       |                     |
| About 30 min left · Saving power |     |                       | [ Unlock          ] |
| for calls, messages (#lock-power)|     |                       | [ Phone ] [ Helper] |
| New notice            (#lock-notice)   +---------------------------------------------+
|                                  |     Foldable: left pane = masthead/dateline/power,
| [ Unlock                       ] |     right pane = buttons (phone-b.css:58 already
| [ Phone ]  [ Helper ]            |     puts #lock>button in column 2).
+----------------------------------+
```

- Masthead = time (`#lock-time`, serif 2.25rem on phones, 40px rule at landscape ≤520 px stays), dateline `#lock-date` in condensed uppercase.
- Public power line `#lock-power` = `s.battery.time_left` + `s.battery.headline` joined by ` · ` (already redacted when the helper is hidden: dashboard.py:235). Never shows `why`, paused names or model names.
- Buttons: `#unlock` primary (3px border). `#lock-call` MUST be relabelled "Phone" (it unlocks and opens the Phone app, phone-a.js:178). `#lock-ask` ("Helper") stays `hidden` by default; privacy rationale: the lock screen shows only redacted public state, and the helper's answers are private detail, so Helper from lock = Unlock then the Helper tile. Showing `#lock-ask` is a Settings › Device choice (`localStorage['jaryo_lock_helper']='1'`), off by default.
- Swipe-up unlock stays as a shortcut; the button is the contract.

### 4.2 Home = the front page (`#phone[data-app="home"] #home`, `#home-favourites`, `#home-notice`, `#allapps`, `#apps-list`, `#apps-close`, `#back`)

```
360x640                                    1280x720 (4 tracks, 1px rules between)
+----------------------------------+       +-------------------------------------------+
| 14:32  Offline  Helper on   23%  | #sb   | 14:32 ............................. 23%   |
| [ Shade ]  [ Lock ]        (btn row)     | Old Phone OS Interface           Sunday 1 November  |
| OLD PHONE OS         (masthead)  |       |===========================================|
|==================================|       | Phone    | Messages | Helper   | Settings |
| Phone            | Messages      | wide? | Calls... | 2 new    | Ask...   | Display..|
| Calls and texts  | 2 new         |       |----------|----------|----------|----------|
|------------------+---------------|       | Calls and messages are on.      | Favour..|
| Helper           | Settings      |       | [ All apps ]                               |
| Ask a question   | Display, ...  |       +-------------------------------------------+
|------------------+---------------|
| Calls and messages are on.  (#home-notice as a one-column "brief")
|----------------------------------|
| [ All apps ]              (wide) |
+----------------------------------+
```

- Status button row `#jaryo-status-row` (new, inserted by `jaryo.js` right under `#sb`): `[ Shade ]` (`#jaryo-shade`, calls `window.opos.open()`), `[ Lock ]` (`oposUI.lock()`). This is the visible button for the pull-down gesture; `#meter-hit` keeps working as the shortcut.
- Masthead `#jaryo-masthead` = "Old Phone OS Interface" serif + dateline, 3px rule below.
- Tiles = the existing `#home button[data-open]` elements, restyled as articles: headline label + one-line standfirst (`data-jaryo-sub`, filled from state: Phone → "Calls and texts" / "In call"; Messages → thread count from `s.threads.length`; Helper → "Ask a question" or "Resting to save power" when `s.mode==='CRITICAL'`; Settings → "Display, Battery, Device"). Sizes via `data-size="small|medium|wide"`: small = 1 track × 1 row (min 96 px), medium = 1 track × 2 rows, wide = all tracks. Defaults: Phone medium, Messages medium, Helper small, Settings small, favourites small; `#allapps` wide. The install wizard may change sizes (section 6); stored in `localStorage['jaryo_tiles']` as `{"phone":"medium",…}`. DOM order never changes.
- `#home-notice` becomes a one-column brief under the tiles (`margin-top:0`), and `#intro` is hidden on home.
- **All apps** (`#allapps` → `#apps-list`): an A–Z list; `jaryo.js` inserts a condensed uppercase letter heading (`<li class="jaryo-letter" aria-hidden="true">`) before the first entry of each letter and a letter-jump row `#jaryo-letters` of ≥48 px text buttons (only letters that exist; others omitted, not greyed). Jumping = `scrollIntoView` on the heading. Contacts/Threads entries are dropped. `#apps-close` and `#back` MUST not both be visible.
- Launcher = Home + All apps; there is no third surface.
- Bottom action row on Home: `[ All apps ]` (existing `#allapps`) plus `[ Shade ]`.

### 4.3 Shade = sidebar column (`#sheet`, `#qs`, `#qs-*`, `#qs-notices`, `#sheet-title`, `#time-left`, `#headline`, `#goal-row`, `#paused-list`, `#saving-now` new, `#shade-technical`)

Opened by the `[ Shade ]` button (and the existing meter pull). Jaryo renders `#sheet` as a column that slides in from the left edge (`transform:translateX(-100%)` → `0`), width `min(100%, 24rem)`, full height, 1px fg right rule; on ≥840 px it is a true sidebar beside the page (page stays visible, `#scrim` is the page itself).

```
+--------------------------+-----------
| [ Close ]                | (page)
| 23%  LOW      (masthead) |
| ======================== |
| About 30 min left        |
| Saving power for calls,  |
| messages      (#headline)|
| Saving now: dim screen,  |
| batch sync. Screen at 40%|  #saving-now
|--------------------------|
| [ Ask the helper       ] |  #qs-ask (closes shade, openApp('helper'))
| [ Helper details: On   ] |  #qs-helper (POST visible; inverted when On)
| [ Battery details      ] |  #qs-batt
| [ Lock ] [ Settings    ] |  #qs-lock #qs-settings
|--------------------------|
| Last until [23:00] Clear |  #goal-row (POST goal)
| Paused: email   [Undo]   |  #paused-list (POST undo)
|--------------------------|
| NOTICES       (subhead)  |
| Notice 14:20 New message |  #qs-notices li + Dismiss
|   [ Dismiss ]            |
| Technical details  ▸     |  #shade-technical (details/summary, text only)
+--------------------------+
```

- All `#qs` buttons are labelled toggles or actions; states per section 2.2. "Battery: 5%" is relabelled "Battery details". The empty history box is hidden with `toggleAttribute('hidden')` on the SVG, and the `router:…;` prefix is stripped from `#why`.
- The sparkline `#history` stays (it is data, not an icon) with `stroke:currentColor`.

### 4.4 Settings = pivots (`#settings`, `#ff`, `#ff-detected`, `#ff-saved`, `#textsize`, `#about`; new `#jaryo-pivots`, `#set-battery`, `#set-device`, `#set-language`, `#set-network`)

```
+----------------------------------+
| [ Home ]                   (#back)
| SETTINGS            (masthead)   |
|==================================|
| DISPLAY  BATTERY  DEVICE  LANG.. |  #jaryo-pivots: ≥48px text buttons, current = inverted
|----------------------------------|
| Theme: Light          [button]   |  #jaryo-theme
| Text size  [Normal      ▾]       |  #textsize (existing)
| Show Helper on lock: Off         |  #jaryo-lock-helper
+----------------------------------+
```

Pivots in order: **Display** (theme, text size, tile sizes), **Battery** (`#headline`, `#time-left`, Last until `goal`, paused + Undo, savings steps, worn note), **Device** (existing `#ff` radios, `#ff-detected` → "Auto (detected: Surface Duo)" from `s.device.name`), **Language** (one line: "English only for now"), **Network** (read-only radios present, see section 7), **About** (`#about` + memory free + helper route). Pivot change = `jaryo.go(pivot)`; sections are siblings in the DOM, only one `[hidden]` at a time; `#settings h2` becomes the masthead. Each pivot's controls are native and wired as in section 7.

### 4.5 Helper = pivots (`#helper`, `#ask`, `#q`, `#webq`, `#askbtn`, `#planbtn`, `#helperreset`, `#helperrestart`, `#answer`, `#helper-where`, and the chat-style helper screen's `#hs-log #hs-status #hs-bar #hs-send #hs-cancel #hs-retry #hs-clear`)

```
+----------------------------------+
| [ Home ]                         |
| HELPER               (masthead)  |
|==================================|
| ASK   PLAN   WHY     (pivots)    |
|----------------------------------|
| Answers are made on this phone   |  #helper-where
| You: how long will it last?      |  #hs-log (role=log)
| Helper: About 30 minutes...      |
|                                  |
| [ Ask a question_____________ ]  |  #q
| [x] Search Wikipedia (sends...)  |  #webq
| [ Send ] [ Cancel ] [ Retry ] [Clear]  #hs-bar (bottom row, sticky)
+----------------------------------+
```

- Ask = `#hs-bar` Send/Cancel/Retry/Clear (the chat-style helper screen). Plan = `#planbtn` (POST `plan`). Why = the existing "Why?" button (POST `explain`) relabelled "Why is power being saved?" with `s.battery.headline` as its headline. `#helper` technical lines live under a `<details>` in Why.
- Rest state: when `s.mode === 'CRITICAL'` the server refuses (assist.py:106); the UI MUST show "Resting to save power. Calls and messages still work." in `#hs-status`, set `#hs-send[disabled]` (dashed border) and keep Plan/Why usable (they are rule-based). The Helper tile standfirst says the same.
- Wide screens keep the existing two-column helper grid (phone-b.css:70–75).

### 4.6 Phone and Messages (`#screen #contacts #threads #thread #number #msg #controls`)

Unchanged behaviour; restyled: `#screen` is the article lede box (1px border, no filter), contact/thread buttons are full-width rows with 1px rules, `#controls` is the sticky bottom action row (Call / Hang up / Send). On slide-out landscape the existing sticky `#controls` rule stays.

## 5. Motion

Budgets: tap feedback 100 ms; shade/sheet, pivot, zoom-through 150–220 ms; zoom-into-article 200–280 ms; reduced motion 120 ms crossfade. Easing: entering `cubic-bezier(0,0,.2,1)` (decelerate), leaving `cubic-bezier(.4,0,1,1)` (accelerate). Only `transform` and `opacity` may be in any `transition`/`animation` in Jaryo files (test_uxp30_budget already audits every `<style>` block; `#sheet`'s existing `visibility 0s` delay is the one allowed exception).

| Move | Mechanism | Duration |
|---|---|---|
| Tap feedback | `button:active` inverted fill; `transition:opacity .1s` on a `::after` is NOT used, the fill is instant and released after 100 ms via class `.jaryo-pressed` | 100 ms |
| Zoom into article (tile → app) | FLIP: read tile rect and `#phone` rect once; set `transform-origin:0 0`; app root starts at `translate(dx,dy) scale(sx,sy)` + `opacity:0`, animates to identity; the tile fades `opacity 1→0` in parallel | 240 ms (200–280) |
| Zoom back (app → same tile) | reverse FLIP to the stored tile id (`jaryo._lastTile`, falls back to `#home [data-open=<app>]`); focus lands on that tile (phone-b.js:32 already focuses `last`) | 240 ms |
| Zoom-through (page ↔ page, pivot ↔ pivot) | outgoing `scale(1)→scale(.94)` + `opacity→0` while incoming `scale(.94)→1` + `opacity 0→1`, both `translateX(±8%)` for direction; pivots add `translateX` page-turn | 180 ms (150–220) |
| Shade | `#sheet` `translateX(-100%)↔0` (replaces the `.25s ease` translateY, within budget) | 200 ms |
| Lock → Home | `#lock` `scale(1)→scale(1.04)` + `opacity→0`; Home zooms in `.94→1` | 200 ms |
| Section scroll | `#phone{scroll-snap-type:y proximity}`, section headers `scroll-snap-align:start`; `scroll-behavior:smooth` only when motion is allowed | native |

Rules:

- **Interruptible.** Animations run with the Web Animations API (`el.animate(...)`), never with CSS classes that block input. A new navigation during flight calls `anim.cancel()` on the running set and starts from the current computed transform (read `getComputedStyle(el).transform` once); `pointer-events` are never disabled; `#phone.inert` is set only by the lock, not by motion.
- **Reflow-free.** Read all rects first, then write transforms in one `requestAnimationFrame`; `will-change:transform` is set just before and cleared on `finish`/`cancel` (pattern already in phone-a.js:2–16). No animated `top/left/width/height/margin`.
- **Reduced motion.** `matchMedia('(prefers-reduced-motion: reduce)')` or `localStorage['jaryo_motion']==='off'` → every move above becomes a 120 ms opacity crossfade; scroll-snap stays, `scroll-behavior:auto`.
- **Frame budget notes (old phones).** One compositor layer per moving element, at most three at once (tile, app root, shade). Avoid animating elements with `filter`, large `box-shadow` or `backdrop-filter` (none exist after section 2.3). Zoom scales the app root as a whole; never animate per-child. Keep `#apps-list` off-screen sections under `content-visibility:auto; contain-intrinsic-size: auto 48px`. If `anim.playbackRate` cannot keep up (two consecutive frames >33 ms measured via `requestAnimationFrame` deltas during the first zoom), `jaryo.js` SHOULD switch to the crossfade path for the session.

JS API (new global, defined in `jaryo.js`, wrapping the existing `oposUI.openApp` like the Home and helper-screen code does):

```js
window.jaryo = {
  theme(): 'light'|'dark'|'system',
  setTheme(v),                 // sets html[data-theme], persists jaryo_theme
  zoomOpen(tileId, app),       // FLIP from #home [data-open=app] (or tileId) then oposUI.openApp(app)
  zoomBack(),                  // reverse FLIP to jaryo._lastTile then oposUI.openApp('home')
  go(pivot),                   // page-turn between sibling sections (settings/helper pivots)
  motion(): 'full'|'reduced'
};
// wiring: var o=oposUI.openApp; oposUI.openApp=function(n){ if(n==='home') return jaryo.zoomBack(o); return jaryo.zoomOpen(null,n,o) }
```

`zoomOpen/zoomBack` MUST call the wrapped `openApp` synchronously (state change first, motion second) so tests that check `dataset.app` immediately still pass.

## 6. Install-time setup: "while it installs, make it yours"

The wizard (setup/wizard_page.py, steps `#s1`–`#s7`) already runs apply in a background thread while `#progress` polls `/setup/api/progress`. Jaryo adds a second column of personal steps that the user answers while the service installs.

Steps, in order, each a `<section class="step jaryo-step" id="j1…j7">` with Skip, Back, Next buttons (≥48 px, text only):

| # | Step | Control | Default | Applied to |
|---|---|---|---|---|
| j1 | Theme | radios Light / Dark / Follow the system | system | `<html data-theme>` rendered by `opos.web.page()` from config; also seeds `localStorage['jaryo_theme']` on first kiosk load |
| j2 | Layout and favourite apps | checkboxes Phone/Messages/Helper/Settings sizes small/medium/wide; favourites order fixed | Phone+Messages medium, others small | `localStorage['jaryo_tiles']` seeded from config `jaryo.tiles` |
| j3 | Text size | radios Normal / Large / Largest | Normal | `html[data-text]` (existing phone-b.js code reads `opos_text`; server seeds the attribute) |
| j4 | Device type | radios Auto / Slab / Foldable / Slide-out / Tablet (+ the existing `#profile` select stays in `#s1`) | Auto | `html[data-ff]` (existing `opos_ff`); `cfg.device` stays owned by `#s1` |
| j5 | Language | one radio "English" + the line "More languages are not available yet" | en | **pending**: OPOS has no i18n (no gettext/translation code in `src/opos`); the choice is stored only |
| j6 | Wi-Fi | read-only: "Wi-Fi radio: present/not detected" from `detect_device()['radios_present']`; button "Set up later in Settings › Network" | skip | **honest limit**: OPOS has no network backend (no nmcli/iwd/wpa code); the kiosk cannot join a network. The step shows status and skips. If a backend ever lands, this step gets the SSID field. |
| j7 | Helper | radios On / Off, with the line "You can change this in the Shade" | On | server `Phone(visible=…)` default: `cfg.jaryo.helper` → `--visible` equivalent at service start; mirrors the existing `visible` POST |

Persistence:

- File: `config_dir()/jaryo-setup.json` (same directory as `config.json`, via `opos.setup.config.config_dir(env, platform)`), written atomically with the same `mkstemp` + `os.replace` + `0o600` pattern as `config.save`. Shape:

```json
{"version":1,"answered":["theme","tiles"],"theme":"dark","tiles":{"phone":"medium"},"text":"l","ff":"auto","lang":"en","wifi":"skipped","helper":true,"applied":false}
```

- Endpoint: `POST /setup/api/jaryo` `{step, value}` → validates (enum per step) and saves after EVERY step; `GET /setup/api/state` gains `"jaryo": {...}` so the page resumes at the first step not in `answered`. Skip records the default and adds the step to `answered`. "Skip all" (button on j1) records all defaults.
- Resume after kill: the wizard reloads `state.jaryo.answered` and shows step `min(unanswered)`; nothing is lost because each answer was saved before Next moved on.
- Apply on finish: `apply.apply()` gains a step `jaryo` after `config` ("Save your look") that copies `jaryo-setup.json` into `config.json` under a new optional key `"jaryo"` (config.validate adds it to `optional`, schema version stays 1) and sets `jaryo-setup.json` `applied:true`. `opos.web.page()` reads `cfg.get("jaryo")` and emits `data-theme`, `data-text`, `data-ff` on `<html>` plus a `<meta name="jaryo-tiles">`. `opos run --defaults` (cli.py:49) writes the default `jaryo` block so skip-all and headless installs are identical.
- The wizard page itself MUST follow Jaryo (section 2.3 wizard row) so the first thing the user sees is the language they chose.

## 7. Native OS integration contract

All reads come from `GET /api/state` = `Phone.state()` (web.py:174) unless noted. Endpoints are the existing `POST /api` actions (web.py:789–836).

| Screen | Reads today | Writes today | Pending / not wired |
|---|---|---|---|
| Status bar `#sb` | `battery.percent`, `helper.engine` (visible only), `navigator.onLine` | — | real radio state; `in_call` tone shown as text "In call" from `notice` |
| Lock | `battery.percent`, `battery.time_left`, `battery.headline`, `in_call`, `notice` | `hang_up` | — |
| Home tiles | `in_call`, `threads.length`, `mode` (NEW additive field, `d.mode.name`, always present), `battery.paused` for the brief | — | app badges beyond messages |
| Shade | `battery.{percent,time_left,headline,steps,paused,events,history,goal,goal_note,worn_note}`, `brightness`, `helper_visible`, `notice` | `visible`, `goal`, `undo`, `in_use` (not exposed) | real screen brightness (value is the budget's wish, budget.py; no sysfs writer) |
| Settings › Display | localStorage `jaryo_theme`, `opos_text`, `jaryo_tiles` | localStorage | server-side persistence after install (config `jaryo`) |
| Settings › Battery | same `battery.*` as shade | `goal`, `undo` | **real battery**: `hw.battery.read_battery(root)` is read only by the wizard (setup/device.py:79); `Phone` runs `DrainSim` (web.py:121). Proposed wiring: `OPOS_HW_BATTERY=1`, copy capacity/status/health per tick. Until then the % is simulated and the UI MUST say "Simulated" in About. |
| Settings › Device | `device.name` (NEW additive field from `setup_config.load()['device']`), `html[data-ff]` | localStorage `opos_ff` | fold posture APIs (bonus only, phone-b.css:76) |
| Settings › Language | config `jaryo.lang` | — | **pending**: no i18n exists |
| Settings › Network | `detect_device()['radios_present'|'net_ifaces']` surfaced as NEW `state.radios` (read-only) | — | **pending**: no join/toggle backend; no rfkill writer |
| Settings › About | `helper.engine` (`muse.label`), `memory_mb` (NEW additive, `assist.free_mb()`, int or null), `device.name` | `restart_helper` (service only) | boot→login time (not in state) |
| Helper | `helper_visible`, `mode`, `battery.headline`, `helper.*` (visible only) | `ask`, `plan`, `explain`, `visible` | streaming answers; conversation memory beyond 6 turns (by design) |

Schema rule: only the additive fields `mode`, `memory_mb`, `device`, `radios` may be added to `/api/state`; redaction tests (test_web_battery.py:85, test_home.py) MUST keep passing; `mode` is not a quiet word.

## 8. Acceptance tests

Methods: **py** = `python -m unittest`, string/regex asserts on `page()`/`wizard_page.page()`/server JSON; **vm** = Node `vm` with a fake DOM (pattern of tests/test_uxp03_shade.py); **cdp** = headless Chromium through tests/uxr_cdp.js (new checks added to `CHECKS`, run at 360×640, 720×1280, 1280×720 in `full` and `home` views, both themes).

| Test | Asserts | Method |
|---|---|---|
| `jaryo_palette` | every visible element's computed `color`, `background-color`, `border-*-color`, `outline-color` ∈ {rgb(0,0,0), rgb(255,255,255), transparent}; no `box-shadow`, `filter`, `background-image`, `opacity<1` on visible nodes | cdp |
| `jaryo_dark_inverse` | for each element, dark computed (fg,bg) == light (bg,fg) exactly | cdp |
| `jaryo_css_literals` | `jaryo.css` contains no colour literal other than `#000`, `#fff`, `transparent`, `currentColor`; no `@font-face`, `url(` | py |
| `jaryo_text_only_buttons` | every `button`, `[role=button]`, `a` has non-empty trimmed text and contains no `img/svg/canvas/picture/i[class]`; computed `background-image:none` | cdp + py |
| `touch_targets` (existing) | ≥48×48, ≥8 px gaps, now including `#jaryo-status-row`, `#jaryo-pivots`, `#jaryo-letters` | cdp |
| `contrast` (existing) | ≥4.5:1 (expect exactly 21:1 everywhere); run in both themes | cdp |
| `text_200` (existing) | no clipping/horizontal scroll at 200 % in both themes and all three viewports | cdp |
| `jaryo_transforms_only` | every `transition`/`animation` in all `<style>` blocks and every `el.animate` keyframe touches only `transform`/`opacity` (`visibility 0s` on `#sheet` allowed) | py (reuse test_uxp30_budget `rules()`) + cdp (`document.getAnimations()`) |
| `jaryo_durations` | `getAnimations()` during zoom: 200–280 ms; shade/pivot/zoom-through/lock: 150–220 ms; tap 100 ms; reduced: 120 ms | cdp |
| `jaryo_reduced_motion` | with `Emulation.setEmulatedMedia prefers-reduced-motion:reduce`, zoom produces one opacity-only animation of 120 ms | cdp |
| `jaryo_back_to_tile` | open Messages via tile, `zoomBack()` → `document.activeElement` is `#home [data-open=messages]` and the reverse FLIP target rect equals that tile's rect (±1 px) | cdp |
| `jaryo_interruptible` | call `zoomOpen('messages')` then `zoomBack()` 50 ms later: no exception, first animation `playState==='idle'` (cancelled), `dataset.app==='home'`, a click on `#allapps` during flight opens the list | cdp |
| `jaryo_fail_open` | inject a throwing `jaryo.js` (replace body with `throw`) → `page()` still renders; `oposUI.openApp('phone')` works; Unlock works | cdp + py |
| `jaryo_quiet_words` | no `/\b(AI|model|assistant)\b/i` in Jaryo CSS/JS/HTML strings or wizard copy; "Helper" is used | py |
| `jaryo_no_network` | Jaryo files contain no `fetch(`, `XMLHttpRequest`, `import(`, `http://`, `https://`; wizard JS only calls `/setup/api/*` | py |
| `jaryo_setup_persist` | POST each step → `jaryo-setup.json` exists, mode 0600, `answered` grows per step | py (tmp `OPOS_CONFIG_DIR`) |
| `jaryo_setup_resume` | answer j1–j3, kill the server process, start again → `state.jaryo.answered==['theme','tiles','text']` and the page shows `#j4` first | py (subprocess) + cdp |
| `jaryo_setup_apply` | `apply.apply()` with a saved setup → `config.json['jaryo']` equals the file, `applied:true`; `page()` emits `data-theme="dark"` and `data-text="l"` | py |
| `jaryo_setup_skip_all` | Skip all → file equals defaults; `opos run --defaults` writes the same block | py |
| `jaryo_page_budget` | `page()` size stays under test_uxp30_budget `PAGE_BUDGET` (raise it once, with the measured number, in the same commit) | py |

## 9. How it was built

Jaryo ships as four static files in `src/opos/web_static/`, inlined into the kiosk page by the `@@OPOS:…@@` placeholders in `opos.web.page()` (`src/opos/web.py`). `jaryo.css` (tokens, colour overrides, type) and `jaryo-layout.css` (layout rules, including landscape) come after `phone-b.css`, so plain id selectors win by source order. `jaryo.js` (theme, motion and `window.jaryo`) and `jaryo-layout.js` (the All apps A–Z list and tile sizes) run after `phone-b.js`. The install-time steps are rendered by `src/opos/setup/wizard_page.py` from `setup/jaryo_steps.py`, and `setup/prefs.py` validates and saves the answers. The disclaimer and notices come from `src/opos/notices.py` (section 11). Tests: `tests/test_jaryo_motion.py`, `test_jaryo_tiles.py`, `test_jaryo_typography.py`, `test_jaryo_apps.py`, `test_jaryo_landscape.py`, `test_jaryo_sbbutton.py`, `test_jaryo_setup_recovery.py`, and headless-browser checks in `tests/uxr_cdp.js`.

Rules for every Jaryo file:

- **Fail-open.** Code is wrapped in `try{…}catch(e){}` blocks, as in phone-a/b.js. If Jaryo throws, the inline shell, `oposUI.openApp`, lock/unlock and the shade must keep working.
- **Quiet words.** Jaryo files never say "AI", "model" or "assistant"; the UI says "Helper". Message text is the owner's and is never redacted.
- **No network, no downloads, no new dependencies.** System fonts only; no CDN, npm or Python packages.
- Existing ids and classes are never renamed; new ones are prefixed `jaryo-`.

## 10. Status

See the README for current status, and section 13 for changes since the proposal.

## 11. Disclaimer and about essentials (owner-mandated)

The following paragraph MUST appear word for word on the install-time setup screen, in Settings > About, and in the case study. It is held in one place, `opos.notices.DISCLAIMER` (src/opos/notices.py), shared with the installers; screens read it from there and never retype it. Tests assert each place (tests/test_notices.py, tests/test_setup_screens.py, tests/test_about_notices.py).

> Old Phone OS Interface is Louis Calata's hackathon demo. It is experimental software, provided as-is with no warranty (GPL-3.0). Installing it replaces your phone's operating system and may erase data; back up first and use at your own risk.

Concise essentials shown beside it (setup screen: "About this software"; Settings > About), all from the same source and only facts true for the build, unpublished items shown as a visible placeholder: version and build hash; GPL-3.0 licence, source link and third-party notices (postmarketOS, Linux, bundled tools); not affiliated with postmarketOS, Microsoft, Google or the phone makers, trademarks belong to their owners; tested devices (others untested); risks (warranty, data loss from unlocking the bootloader, bricking, no emergency-calling guarantee); how to restore stock; privacy (each claim verified against the code in docs/notices-verification.md); contact or issue link; (c) 2026 Louis Calata. Style: Jaryo (serif, left, thin rules, two colours, native details/summary, no icons).

## 12. Changes to this file

Changes made after the proposal are recorded as notes in section 13.

## 13. Implementation notes

- Layout/screens layer files are `web_static/jaryo-layout.css` and `jaryo-layout.js`. Theme, motion and type live in `jaryo.css` and `jaryo.js`. Install-time setup UI lives in `setup/wizard_page.py` (inline) with preferences in `setup/prefs.py`. The shared notices source is `src/opos/notices.py`.
- The loader placeholders and a repository-check exception for the Jaryo work were added in the first Jaryo commit.
- Install preferences in the kiosk: `Phone.state()['prefs']` carries the effective answers only after setup finished (`applied`), never Wi-Fi names. Theme, text size and device type seed their `localStorage` keys once after the first `/state` reply (first-paint seeding from config is still pending). Tile sizes seed `jaryo_tiles`. The favourites list (j2) is recorded only: its order is fixed and it never hides a tile, so Settings and every All apps entry stay reachable. Helper (j7) is shared server state: `web.main()` starts `Phone(visible=True)` when the applied answer is On; `--visible` still wins; the browser never replays it. Language is English only (no i18n), recorded only.
- Landscape layout: rules only, all in `web_static/jaryo-layout.css`; no JS, no DOM change. Lock in landscape at ≥840 px is the §4.1 two-column page (time, dateline with the 3 px masthead rule and battery on the left; notice and the buttons on the right, the same `#lock>button` split phone-b.css gives the foldable); Slab keeps its phone column and the foldable its two-pane hinge grid. Shade at ≥840 px is the §4.3 sidebar: `#sheet` is a `min(100%,24rem)` left column with a 1 px right rule resting at `translateX(-100%)`; the base `.25s` transition and its visibility delay are untouched and `#scrim` stops painting over the page but still closes the shade and keeps the page inert to pointers. In portrait the Shade stays a half sheet from the top; a side column was rejected for portrait (design decision 5, picked from 390×844 portrait mockups). Portrait is unchanged here. Home's four landscape tracks (§4.2) and the slide-out sticky `#controls` row (§4.6) already existed. Three test-suite repairs came with it: the Home code's `show()` in phone-b.js skips list rows without a button, so the All apps letter headings no longer abort All apps in a real browser; `tests/test_uxp04_launcher.py` accepts the tile-size `data-size` attribute on a tile; `tests/test_duo_helper_hint.py` names the inline `helperCall(` handlers now that the chat-style helper screen also sends `action:'ask'`. With All apps working again, the foldable (two-pane hinge grid, ≥840 px) showed the letter-jump row `#jaryo-letters` as a left-pane item in front of the two-pane `#apps-list`, which breaks the foldable's reading-order rule (full-width items first); while All apps is open the row now spans both panes and keeps its buttons in the left pane (padding equal to the right pane plus the 24 px gutter), so no letter is in the hinge and DOM order stays reading order. One rule in the layer applies at every size: `#lock>button{margin-right:0}`. The base `button` rule gives every button `margin-right:8px` and phone-a.css makes lock buttons `width:min(100%,360px)`; centred by the lock's flex column, the 368 px margin box sat at −4 px to 356 px in a 360 px viewport (measured when the landscape layout was checked), so Unlock and Call each lost 4 px at both edges on the §4.1 portrait lock. The 4 px vertical margins and the base rule itself are unchanged. Tests: `tests/test_jaryo_landscape.py`.
- Lock PIN step (`j8`, step name `pin`): the design decisions of Nov 1 2026 (item 6) add a short lock PIN step after Wi-Fi. The kiosk lock has no PIN check (the lock opens with Unlock, swipe-up or Escape), so the step follows the j6 Wi-Fi precedent and is record-only: an `<h3>` title, two lines saying a lock PIN is not available in this build and can be set in Settings › Device later, the button "Set up later in Settings › Device", and the usual Skip/Back/Next. There is no input of any kind; Next, Skip and the button each record a skip. `prefs.validate_answer('pin', …)` accepts only `later` or `skip`, and `Phone.state()['prefs']` never publishes the answer (the kiosk sees the default `later`). In navigation, resume and page order the step follows Wi-Fi (`wifi`, `pin`, `helper`), as the decision says; section ids stay with their steps (`jaryo_steps.IDS`: Helper keeps `j7`, Lock PIN is `j8`), so the existing `j1`–`j7` ids and their tests stay untouched even though `j8` now precedes `j7` in the page. When a lock backend lands, this step gets the digits field with server-side validation and a salted hash in a separate 0600 file; until then nothing is collected.
- Personal setup recovery derives progress from readable answers and skips. Missing or stale resume markers cannot bypass unfinished steps, and completion requires every step to be answered or skipped without overlap. Resuming restores the saved theme preview; skipping Theme restores Auto. These UI checks do not supply the separate server finish endpoint.

- Settings, Helper and About repair (2026-11-03 UTC): adds a Settings Light/Dark/System control and real palette consumers, keeps a manual System choice independently of installer defaults, and maps the already offered Small preference to its own root scale while keeping 48 px controls. Complete draft pivots and server-rendered first-paint setup seeding are not part of this repair. The critical-battery Ask screen now distinguishes intentional rest from transport failure; newer NORMAL/SAVER state cannot be overwritten by an older rest response, and Clear does not hide the current rest status. Plan/Why and server policy are unchanged. Settings About renders the shared escaped disclaimer plus native expandable licence/privacy/limits beside the independent helper status. The page measured 97,625 UTF-8 bytes before this repair. With this repair the page exceeded the original 98,090-byte cap, so the page-size test was given an explicit 8 KiB increment (105,817 bytes total); the next note restores the original cap. This repair does not cover every draft feature.

- Historical page-cap restoration (2026-11-03 UTC; superseded by the current page-budget note below): the 98,090-byte bound was enforced again. The repaired page is 97,852 bytes; a normal served demo with the generated token/nonce is 98,088 bytes (98,089 with the longest release build label). The earlier 105,817-byte proposal is superseded. Source formatting keeps all JavaScript literals, comments and script boundaries; pure dynamic DOM delegates remove repeated lookup/factory syntax. Explanatory CSS comments and full original headers are retained in docs/web-implementation-notes.md, while declarations and normalised CSS token strings stay unchanged. Required About/legal notices, accessibility, critical-rest, theme and persistence behavior are retained. The normal served demo also has an explicit regression check against the original bound. This restoration uses no deferred assets, content removal, new runtime dependency or user-visible tradeoff.

- Setup tile-size completion (2026-11-03 UTC): j2 now exposes native Small/Medium/Wide selects for Phone, Messages, Helper and Settings, using the existing `layout.sizes` schema. Defaults match the kiosk (Phone and Messages Medium; Helper and Settings Small). Next saves the sizes with favourites; Back, reload and resume restore them, including defaults for older missing/partial size maps. The summary reports starting sizes and explicitly describes favourites as recorded only. Fresh kiosk profiles consume the applied sizes through the existing layout hook; previously stored screen choices remain authoritative. No tile is hidden or reordered, no new model/network/device feature is implied, and Wi-Fi/PIN and first-paint/full-pivot limitations remain unchanged.

- Softer whites (2026-11-03 UTC, Louis's request to tone down the brightness of white): the tokens are now `#e6e2d9` on `#000` in dark mode and `#1a1a1a` on `#f3f0e8` in light mode, in the kiosk (`jaryo.css`) and the setup card (`setup/jaryo_steps.py`); the setup wizard shell uses `#e6e2d9` on `#000`. This supersedes the exact `#000`/`#fff` rule in section 1 item 3 and the 21:1 contrast expectation: contrast is now about 16.3:1 (dark) and 15.3:1 (light). Each theme still uses exactly two colours, never mixed across themes; `var()` fallbacks keep `#000`/`#fff`.

- Current page budget (2026-11-03 UTC): `tests/test_uxp30_budget.py` sets `PAGE_BUDGET = 100 * 1024`, or 102,400 UTF-8 bytes (100 KiB), for the Settings top-bar, brightness and volume controls. This supersedes the 98,090-byte cap above; the earlier page and served-demo sizes are historical measurements. The current `web.page()` measured 101,109 UTF-8 bytes on this date, without the generated token/nonce of a served demo. See [architecture.md](architecture.md) for the current budget.
