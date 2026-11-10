<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Jaryo panes: a black screen and a white screen on the two-panel window

> Design spec, 2026-11-07. Normative words: MUST, MUST NOT, SHOULD. Applies to the kiosk page served by `opos.web.page()` and to the three Duo OS pages `/network`, `/firstrun` and `/timezone`. Written to be moved into the repository as `docs/jaryo-panes.md`. Every rule below was built as a throw-away prototype and checked in headless Edge at 1392x900 (state by state, both hands, both themes); the implementation is the files named in section 6.2. Numbers marked "measured" come from that run.

## 0. In one screen

- `OPOS_PANES=1` (an environment flag, off by default) makes the server answer `/panes.css` and `/panes.js` and add two tags to the pages that already take the on-screen keyboard's tags. `web.page()` does not change by a single byte. In short: with OPOS_PANES unset the panes assets and tags are absent and page() is unchanged; the Network, first-run and time zone pages carry the Jaryo conformance fixes either way (section 5), because those fixes are in the pages' own styles and not behind the flag.
- On the Duo window that spans both panels (`OPOS_SPAN=675,42`, 1392 x 900 CSS px), each screen is strictly black and white. The **thumb pane** (the app or detail pane, where you act) is black with light text. The **list pane** (masthead, lists, the Shade) is white with dark text. The hinge strip is black. A **Hand** setting (Right by default, Left) says which physical pane is the thumb pane and mirrors the layout.
- Pressed, active and on controls keep the Jaryo rule "inverted fill" relative to their own pane, because every Jaryo rule already reads `--jaryo-fg` and `--jaryo-bg`; the panes only change what those two names mean inside each pane.
- The pane colors REPLACE Light/Dark/System while the window spans two panels. On a one-pane window nothing changes: `jaryo.css` paints exactly what it paints today.
- Contrast measured 15.28:1 on the white pane and 16.25:1 on the black pane, the same two pairs as `jaryo.css` ("softer whites", docs/jaryo.md section 13). A single switch, `OPOS_PANES=1,pure`, gives the strict AGIW pair (`#000` / `#fff`, 21:1).

## 1. The brief, the reading, and what the AGIW logo says

### 1.1 The brief (Louis, verbatim)

> "Make it like how AGIW's new logo is. Black and white on each screen. The active screen is in black screen, for right handed mode and invert for the left. Have the text buttons and UI designed around Jaryo."

### 1.2 The reading that is built

1. "Each screen" is each of the two physical panels of the Duo, seen by the page as one window of 1392 x 900 CSS px: pane 675 px, hinge 42 px, pane 675 px (1350 + 84 + 1350 device px at output scale 2).
2. "The active screen" is the thumb pane: the pane that holds the app or detail the person is working in. It is black with white text. The other pane (list, master, masthead, shade) is white with black text. Section 2.4 says which is which in every state.
3. Right-handed (the default): the right pane is the thumb pane. Left-handed: the mirror image. The left pane is the thumb pane and black, the right pane is the list pane and white, and the layout mirrors with it (section 4.3).
4. "Text buttons and UI designed around Jaryo": the controls stay what docs/jaryo.md section 2.2 says they are (1 px solid border, inverted fill when pressed, dashed border when unavailable, 3 px double box for notices, no icons). Section 5 restates the rules and lists every control in the OS pages that breaks them today, with the fix.
5. Reading 3 is the one most open to a second reading ("invert" as colors swapping while positions stay). That reading is one switch away (section 7, `OPOS_PANES=invert`). It is stated here so the owner can pick.

### 1.3 What the AGIW logo handoff says about the mark

Read from the AGIW logo handoff folder `agiw-logo-for-opos-20261108` (17 files: `identity/jaryo-identity.md`, `jaryo/README.md`, `jaryo/tokens.json`, `jaryo/primitives.css`, `jaryo/theme.mjs`, `logo/*.svg` and `*.png`, `superseded-d4-split/*.svg`). It arrived; nothing was waited for.

- The mark is type. "The chrome name is the word AGIW in Inter ... weight 600, with the font's own kerning plus 0.02em tracking. This is provisional." The favicon and the square mark are the letters AG, white on black. "There is no geometric app mark."
- Color: "Strict black and white. No graphite, copper, or color accent in AGIW." "Dark treatment is white on black. Light treatment is black on white. Narrow treatment is the square mark alone."
- Theme: system light/dark is the default; Light or Dark is an explicit override (the same rule as OPOS's `#jaryo-theme`).
- Icons: "Controls inside the pages stay text only. The marks are product icons, not UI glyphs."
- D4 Split (a filled block beside an open block, the geometric cousin of a black pane beside a white pane) is recorded as rejected: "Superseded by the Inter wordmark."
- Proportions, measured from `agiw-mark.svg` (1024 square) and `agiw-wordmark-on-light.svg` (1200 x 400): the AG run is 60 % of the square's width, centred on both axes, cap height 31.6 % of the square; the AGIW run is 80 % of the wordmark's width (10 % margin each side), cap height 63.6 % of its height, centred on both axes. In every file the letters sit exactly on the centre of a plain field of one color, nothing else on it.
- `primitives.css` names the pairs `--j-bg #000` / `--j-text #fff` / `--j-invert #fff` / `--j-invert-ink #000` (dark) and the swap (light). That is the same "inverted fill" rule as OPOS, with strict values. It also carries `--j-grid: rgb(255 255 255 / 0.16)`, a translucent tint that OPOS forbids; it is not used here.

What this changes in OPOS, and what it does not:

| Logo fact | Decision |
|---|---|
| Black field, white letters; white field, black letters; nothing else | The two surfaces are exactly that: one black, one white, no accent, no grey, no gradient, no image (the palette test, section 8, enforces it). |
| Everything centred and symmetric | Panes are equal width and the hinge sits in the middle. The only asymmetry is which pane is black, and the person chooses it (Hand). |
| Type is the mark; controls are text only | No AGIW mark, wordmark, favicon or any drawn shape goes into the OPOS UI. The split is a surface rule, never a logo. |
| D4 Split was rejected as the mark | The black/white split MUST NOT be drawn as an emblem (no filled block beside an outlined block anywhere in chrome). |
| Inter 600 | Not adopted in OPOS. Jaryo section 3 forbids `@font-face` and downloads, and the CSP is `default-src 'none'` with no `font-src`. OPOS keeps its system serif, condensed and body stacks. |
| Strict `#000` / `#fff` | Default stays the softer OPOS pair (`#e6e2d9` on `#000`, `#1a1a1a` on `#f3f0e8`) because Louis asked for toned-down whites on 2026-11-03 and the task requires 15:1 "as today". `OPOS_PANES=1,pure` gives the AGIW pair. Open question 1. |

## 2. The pane model

### 2.1 Geometry the page already has

- `opos.span` (`OPOS_SPAN=675,42`) rewrites the page's `(horizontal-viewport-segments:2)` rules into `(min-width:1055px)` (the gate: first pane + hinge + half a second pane) and the segment `env()` values into `--pane` and `--hinge`. Chromium on Linux cannot report viewport segments, so without `OPOS_SPAN` the Duo window is one 1392 px page and `data-ff` is not `foldable`.
- With the span, Auto picks `html[data-ff="foldable"]`. `#phone` and `#lock` become two-column grids: first column `calc(--pane - safe-left)`, gap `--hinge`, second column `1fr`, `grid-auto-flow: row dense`. The hinge strip is x 675 to 717.
- Everything the panes touch outside `#phone` is `position: fixed`: `#sb` (status bar, full width), `#meter` and `#meter-hit` (battery gauge and pull strip, full width), `#scrim`, `#sheet` (the Shade, a 24 rem sidebar from the left edge at 840 px and up), `#lock` (full window grid), `#reload-shell`. `#jaryo-status-row` (Shade and Lock buttons) is a normal block that `phone-a.js` inserts after `#sb`.
- DOM order inside `#phone` (it decides every column): `#jaryo-masthead`, `#home` (tiles and `#home-notice`), `h2.at` "All apps", `#apps-list`, `#jaryo-letters` (inserted before `#apps-list`), `#apps-close`, `#back`, three `h2.at`, `#settings`, `#meter`, `#meter-hit`, `#scrim`, `#sheet`, `#screen`, `#notice`, `#intro`, `#contacts`, then `#threads`, `#thread`, `#number`, `#msg`, `#controls`, `#ask`, `#helper`. `phone-b.css` puts `#threads` and every later sibling in column 2, everything earlier in column 1.

### 2.2 Which elements are in which pane today (measured, duo-acceptance, `--span 675,42`, 1392 x 900)

| State | Left pane today | Right pane today |
|---|---|---|
| Lock | `#lock-time`, `#lock-date`, `#lock-batt`, `#lock-story` | `#unlock`, `#lock-call` (the `#lock>button` rule) |
| Home | status row, masthead, all tiles, All apps, brief | nothing (only the battery text in the status bar) |
| All apps | masthead, "All apps", letter index, entries, Close | nothing |
| Phone | masthead, Home, "Phone", contacts | number, note, Call, Hang up, Hide helper |
| Messages | masthead, Home, "Messages" | threads, thread, number, message, Send, Hide helper |
| Helper | masthead, Home, "Helper" | Ask field, Send, Cancel, Clear, Why, Plan, Technical details, Reset |
| Settings | masthead, Home, the whole Settings list | nothing |
| Shade | the Shade, a 384 px column at the left edge, over the page | nothing |

Zero text or controls sit on the hinge in any of these states. Home, All apps and Settings leave the right pane empty, which on a black thumb pane would be an empty black pane. Section 2.4 therefore re-places those three.

### 2.3 The rule

- The **thumb pane** holds what you operate: the Shade and Lock buttons, the tiles, the app list, Home (back), the number and message fields, Call and Send, the helper's Ask field, Settings.
- The **list pane** holds what you read or choose between: the masthead and dateline, the page heading ("Phone"), contact and thread lists, the helper's Technical details, the lock screen's time, date and battery, and the Shade.
- A control may live in the list pane (contacts, thread rows, Reset). The split is a reading order and a reach, not a lock: every control works wherever it is.

### 2.4 Right-handed assignment, state by state (verified)

| State | List pane (white, left) | Thumb pane (black, right) | Moved from today |
|---|---|---|---|
| Lock | time, date, battery, power line, notice | Unlock, Call, Helper (if shown), Hang up | none |
| Home | masthead and dateline | Shade, Lock, the four tiles, All apps, the brief (`#home-notice` is inside `#home`) | `#home` to the right pane |
| Shade | the Shade, full pane width (675 px), with the battery gauge and pull handle | the page behind it, still visible, inert | Shade widened from 384 px to the pane; status row stays on the right |
| All apps | masthead, "All apps" | letter index, the app list, Close | `#apps-list`, `#jaryo-letters`, `#apps-close` to the right pane |
| Phone | masthead, "Phone", contacts | Home, call notice, number, Call, Hang up | `#notice` to the right pane |
| Messages | masthead, "Messages", thread list | Home, thread, number, message, Send | `#threads` to the left pane |
| Helper | masthead, "Helper", Technical details and Reset | Home, Ask field, Send, Cancel, Clear, Why, Plan, Wikipedia switch | `#helper` to the left pane |
| Settings | masthead | Home, Settings (device type, Display with Theme and Hand, About) | `#settings` to the right pane |

The status bar (`#sb`) keeps its text where it is (time, network note and "Helper on" in the left pane; battery in the right pane) and takes each pane's colors. The Shade and Lock buttons (`#jaryo-status-row`) move to the thumb side: right-aligned in Right, left-aligned in Left.

Settings stays one column in the thumb pane. A two-column Settings spanning both panes was tried and dropped: the shared grid rows push Device type below the whole Display section (the same artefact the audit recorded in geometry-15).

### 2.5 What happens on single-pane windows (verified)

| Window | `data-ff` | `data-panes` | Result |
|---|---|---|---|
| 675 x 900, Auto | none | none | exactly today; Hand button hidden; Theme label unchanged |
| 1000 x 700, Auto (below the gate) | none | none | exactly today |
| 1392 x 900, Slab forced in Settings | `slab` | none | exactly today (the 420 px column); panes off by design |
| 675 x 900, Foldable forced | `foldable` | `two` | CSS is inert (the media gate is false); Hand button hidden |
| 1392 x 900, Auto | `foldable` | `two` | panes on |

`html[data-panes="two"]` is the single switch. `panes.js` sets it only when the page is the main page with `data-ff="foldable"`, or one of the three OS pages. Every rule in `panes.css` starts with that selector (a test asserts it), inside the two-screen media gate. If `panes.js` is missing, blocked, or throws, the attribute is never set and the page is what Jaryo paints today (fail-open, as docs/jaryo.md section 9 requires).

## 3. The color model

### 3.1 Custom properties (exact)

All names are prefixed `--jp-` and live on `html[data-panes="two"]`.

| Property | Default (Hand: Right) | Meaning |
|---|---|---|
| `--jp-k`, `--jp-k-ink`, `--jp-k-cs` | `#000`, `#e6e2d9`, `dark` | the black surface, its ink, its `color-scheme` |
| `--jp-w`, `--jp-w-ink`, `--jp-w-cs` | `#f3f0e8`, `#1a1a1a`, `light` | the white surface, its ink, its `color-scheme` |
| `--jp-x1` | `env(viewport-segment-width 0 0)` (rewritten to `var(--pane)`) | where the first pane ends and the hinge begins |
| `--jp-x2` | `env(viewport-segment-left 1 0)` (rewritten to `calc(var(--pane) + var(--hinge))`) | where the second pane begins |
| `--jp-l`, `--jp-l-ink`, `--jp-l-cs` | white, white ink, light | the physical LEFT pane |
| `--jp-r`, `--jp-r-ink`, `--jp-r-cs` | black, black ink, dark | the physical RIGHT pane |
| `--jp-act`, `--jp-act-ink`, `--jp-act-cs` | = `--jp-r` | ROLE: the thumb pane's colors |
| `--jp-lst`, `--jp-lst-ink`, `--jp-lst-cs` | = `--jp-l` | ROLE: the list pane's colors |

Hand: Left swaps `--jp-l` with `--jp-r` (left becomes black, right white) and points `--jp-act` at the left pane and `--jp-lst` at the right pane. Content colors follow roles; only the backdrops, the status bar and the keyboard follow physical sides. That is why Left needs 4 rules for color and the rest for placement.

The four literals are the two token pairs of `jaryo.css` lines `--jaryo-fg:#e6e2d9;--jaryo-bg:#000` and `--jaryo-fg:#1a1a1a;--jaryo-bg:#f3f0e8`. A test pins them to those values so the two files cannot drift.

### 3.2 How the two surfaces are painted (no gradient, no image, no new element)

`jaryo.md` section 2.1 and the planned `jaryo_palette` test forbid `background-image`, gradients, shadows and filters on visible nodes, so a hard-stop gradient is out. Three flat layers are used instead, on `html` only:

1. The canvas is the right pane: `html { background: var(--jp-r); color: var(--jp-r-ink) }`. `body` and `#phone` become `transparent`. The root's `color` (and with it the `caret-color: auto` that follows it) is the right pane's ink, so with `pure` the root computes `#fff` or `#000` and not the theme's soft ink; no `caret-color` is set, because it would be inherited into the other pane.
2. The left pane is one fixed layer: `html::before { position: fixed; z-index: -1; top: 0; bottom: 0; left: 0; width: var(--jp-x1); background: var(--jp-l) }`.
3. The hinge is one fixed layer, always the black surface: `html::after { position: fixed; z-index: 1000; left: var(--jp-x1); width: calc(var(--jp-x2) - var(--jp-x1)); background: var(--jp-k); pointer-events: none }`. It covers the 42 px strip above everything (the Shade is 70, the lock is 100, the keyboard is higher and avoids the hinge itself).

The hinge is black in both hands because the physical hinge is black: the panels end, nothing is shown there, and a black strip reads as a clean edge between the two surfaces. In Right the boundary reads white | black at x 675; in Left it reads black | white at x 717. Measured: the pixel column at x 696 is `#000000` at y 14, 450 and 880 in all 36 main-page and 24 OS-page state, hand and theme combinations. If the owner wants a white hinge, the single change is `background: var(--jp-w)` on that one rule (open question 3).

The layers are fixed, so the panes stay full height however far the page scrolls, and an empty pane (a state with nothing in it) still reads as a pane.

### 3.3 Scoping the Jaryo rules to a pane

Every Jaryo rule paints with `var(--jaryo-fg)` and `var(--jaryo-bg)`. The panes redefine those two names on each top-level item, so no Jaryo rule needs editing:

```css
html[data-panes=two] :is(#phone>*,#lock>*){--jaryo-bg:var(--jp-lst);--jaryo-fg:var(--jp-lst-ink);color:var(--jp-lst-ink);color-scheme:var(--jp-lst-cs)}
html[data-panes=two] :is(#phone>:is(#home,#apps-list,#jaryo-letters,#apps-close,#back,#settings,#notice,#thread,#number,#msg,#controls,#ask),#lock>button,#jaryo-status-row,#reload-shell,#demo){--jaryo-bg:var(--jp-act);--jaryo-fg:var(--jp-act-ink);color:var(--jp-act-ink);color-scheme:var(--jp-act-cs)}
```

- Default is the list pane, so any child that a later branch adds to `#phone` is white with dark ink and sits in column 1 until someone lists it.
- `color` is stated only on what is not a button (`:not(button)`). A button takes its label from `jaryo.css` (`button{color:var(--jaryo-fg)}`, `button:active{color:var(--jaryo-bg)}`), and a `color` stated on `#back`, `#apps-close`, `#unlock` or `#lock-call` by the rules above (two ids, so more specific than `button:active`) left the label in the pane's ink on a fill that had just become the pane's ink: the label vanished while pressed. The same holds for the Home link of `/network` in rule 9. The browser test presses every visible control (section 8) so a control added later cannot repeat it.
- `color-scheme` follows the pane, so native `select`, `input[type=time]` and scrollbars draw for the right background.
- "Pressed, active, on = inverted fill" is `button:active, [aria-pressed="true"] { background: var(--jaryo-fg); color: var(--jaryo-bg) }` in `jaryo.css`, so it inverts relative to the pane it sits in: in the black pane a pressed button is `#000` text on a `#e6e2d9` fill; in the white pane it is `#f3f0e8` text on a `#1a1a1a` fill. Measured: "Helper: On" in the Shade (white pane) is the second; the selected region chip in the first-run time zone step (black pane) is the first.
- Focus ring `outline: 3px solid var(--jaryo-fg); outline-offset: 3px` takes the pane's ink the same way.

### 3.4 Chrome that spans the window

| Element | Rule | Why |
|---|---|---|
| `#sb` status bar | background = right surface; `#sb::before` (absolute, z -1, left, width `--jp-x1`) = left surface; `#sb-time, #sb-net, #sb-engine` take left ink; `#sb-batt` takes right ink | the bar is full width and must stay opaque so scrolled content never shows through it |
| `#meter` (battery gauge) | `left:0; right:auto; width:--jp-x1` (Left: `left:auto; right:0`); ink from the list role | the gauge, the Shade and its pull handle all belong to the list pane; the 48 px pull strip `#meter-hit` stays full width as a shortcut, its handle is centred in the list pane |
| `#sheet` (Shade) | `left:0; width:--jp-x1; border:0; transform:translateX(-100%)`, open state `body.sheet-open #sheet { transform: translateX(0) }` (Left: from the right edge, `translateX(100%)`) | the Shade becomes the white pane's content; a 384 px strip over a masthead (today's look, "ace" clipped at x 384) goes away. The existing transition and `visibility` delay are untouched |
| `#lock` | background = right surface; `#lock::before` = left surface; text in column 1 (list), buttons in column 2 (thumb) | the lock's two columns are already pane-aligned |
| `#reload-shell` | right-aligned at 12 px (Left: left at 12 px); thumb tokens | it is a thumb control |
| stale-page banner (`body[data-stale] #notice`, a fixed box under the status bar from `jaryo.css`) | list tokens (`--jp-lst`, `--jp-lst-ink`) and the list pane: `left:0` (Left: `left:--jp-x2`), one pane wide (`opos.span`) | an alert is something to read, so it is the white pane's own 3 px double box and the white pane stays white; it never covers the Shade and Lock buttons or Reload, which are thumb controls in the other pane. It takes no taps (`pointer-events:none`) |
| keyboard (`#opos-osk`) | `.osk-split>.osk-panel:first-child` takes the left surface, `:last-child` the right surface | the two halves already hug the outer edges; verified in both hands with `OPOS_OSK=1` |

### 3.5 Native controls and placeholders

Measured on the page with only the sections 3.1 to 3.4 rules: 40 computed values were off-palette, all on form controls (36 times `#fff` as text or outline color on checkbox, radio and range, 4 times `#3b3b3b` as the range track). The fix is five rules:

```css
html[data-panes=two] :is(input,select,progress){color:var(--jaryo-fg);accent-color:var(--jaryo-fg);outline-color:var(--jaryo-fg)}
html[data-panes=two] :is(input[type=radio],input[type=checkbox]){-webkit-appearance:none;appearance:none;width:24px;height:24px;border:2px solid var(--jaryo-fg);background:var(--jaryo-bg);border-radius:0}
html[data-panes=two] input[type=radio]{border-radius:50%}
html[data-panes=two] :is(input[type=radio],input[type=checkbox]):checked{background:var(--jaryo-fg)}
html[data-panes=two] input[type=range]{background:transparent}
html[data-panes=two] ::placeholder{color:var(--jaryo-fg);opacity:1;font-style:italic}
```

Checked is the inverted fill, not a tick glyph. Placeholders are full ink and italic (default grey is about 4.6:1). After these, the whole page computes only `#000`, `#e6e2d9`, `#f3f0e8`, `#1a1a1a` and `transparent` (measured, all states, both hands, both themes). Known limit: the engine draws the thumb and track of `input[type=range]` and the clock icon of `input[type=time]`; `accent-color` gives them the pane ink, and the palette test exempts those two pseudo-parts (nothing else).

### 3.6 Theme (Light / Dark / System)

- While `data-panes="two"` and the gate is true, the pane colors REPLACE the theme: one pane is always black, the other always white, whatever `#jaryo-theme` says. `html[data-theme]` is left alone, so the choice is back the moment the phone is used on one screen.
- `#jaryo-theme` is not disabled (it still cycles and saves). While spanned, a CSS suffix tells the truth: `html[data-panes=two] #jaryo-theme::after { content: " (one screen only)" }`, so the label reads "Theme: System (one screen only)". `jaryo.js` rewrites only the button's `textContent`, so there is no conflict.
- On a one-pane window the suffix is absent and nothing else changes.

### 3.7 Strict AGIW pair

`OPOS_PANES=1,pure` replaces the four literals by `#000`, `#fff`, `#fff`, `#000` (`primitives.css`). Everything else is identical. The test asserts the page then contains only `#000` and `#fff`.

## 4. The Hand setting

### 4.1 The control

- A text button `#jaryo-hand`, created by `panes.js`, inserted with `insertAdjacentElement('afterend')` right after `#jaryo-theme` in Settings > Display. `page()` does not contain it.
- Label: `Hand: Right` or `Hand: Left`. One tap flips it, as `#jaryo-theme` cycles. Keyboard Enter and Space work (it is a `<button>`).
- Style: the same rule as `#jaryo-theme` (`min-height:48px; min-width:48px; width:100%; margin:0 0 8px; padding:8px; font-size:1rem; white-space:normal`). Measured 627 x 48.
- Hidden (`display: none`) everywhere except inside the two-screen gate with `data-panes="two"`. It has no effect on a one-pane window, so it is not shown there.
- It is not an on/off toggle, so, like Theme, it carries its state in its label and has no inverted fill.

### 4.2 Persistence and the attribute

- `localStorage['jaryo_hand']` is `right` or `left`. Absent, or any other string, means Right. Every read and write is wrapped in try/catch, and the page works with storage blocked: storage gives the first value and receives each tap, but the label and the next tap go by the page's own `html[data-hand]`, so with storage blocked or full a tap still flips the layout and the label together (it used to read the hand back from storage, so the label stayed "Hand: Right" and every tap set Left).
- `html[data-hand]` is always `right` or `left` once `panes.js` has run. `panes.js` is loaded in the head (a normal, blocking script), so `data-hand` is set before the first paint: no flash of the wrong mirror for a left-handed owner.
- A `storage` event for the key re-applies it (two tabs, or the OS pages open in another tab). All four pages share one origin, so `/network`, `/firstrun` and `/timezone` honour the same value.
- Not stored: nothing is written until the owner taps (no default is persisted).

### 4.3 What mirrors when Left (verified)

| Thing | Right | Left |
|---|---|---|
| Black (thumb) pane | right | left |
| White (list) pane | left | right |
| List items (`#jaryo-masthead`, `.at`, `#contacts`, `#threads`, `#helper`, `#screen`, `#intro`) | grid column 1 | grid column 2 |
| Thumb items (`#home`, `#apps-list`, `#jaryo-letters`, `#apps-close`, `#back`, `#settings`, `#notice`, `#thread`, `#number`, `#msg`, `#controls`, `#ask`) | grid column 2 | grid column 1 |
| Lock text / lock buttons | column 1 / column 2 | column 2 / column 1 |
| Shade and Lock buttons (`#jaryo-status-row`) | `justify-content: flex-end` | `flex-start` |
| Shade (`#sheet`) | from the left edge, `translateX(-100%)` | from the right edge, `translateX(100%)` |
| Battery gauge `#meter` and pull handle | left pane | right pane |
| Keyboard halves | left half on white, right half on black | left half on black, right half on white |
| `#reload-shell` | right, 12 px | left, 12 px |
| Stale-page banner | list pane (left), list tokens | list pane (right), list tokens |
| `/network`, `/firstrun`, `/timezone` columns | list column left, thumb column right | swapped |
| Status bar text (time, network, engine left; battery right) | unchanged | unchanged; the two halves take their pane's colors |
| Grid template, hinge, widths | `675px 42px 1fr` | the same (equal panes) |

Accepted deviation: DOM order is unchanged, so in Left the visual order (thumb pane first) differs from the Tab and reading order (list first). docs/jaryo.md section 4 forbids `order:`; this uses `grid-column`, as the Foldable layout already does, and `tests/test_uxp12_home.py` (which enforces DOM order) is unaffected.

## 5. Text buttons: conformance

### 5.1 Rules, restated (docs/jaryo.md sections 1, 2.2, 3, 4)

| Rule | Value |
|---|---|
| Label | Every control is a `<button>`, `<a>`, `<input>`, `<select>` or `<label>` whose meaning is in its words. No icons, SVG glyphs, images, `background-image`, tick marks or mystery symbols. Labels are words ("Hand: Left", "Show the password"). |
| Colors | The two surfaces and their two inks only. No grey, tint, translucent, shadow, gradient, filter, or `opacity` used for dimming. |
| Corners | `border-radius: 0` on buttons, inputs, boxes (radios may be round). |
| Default button | 1 px solid ink border, ink on surface. |
| Pressed, active, `aria-pressed="true"` | Inverted fill. |
| Toggle | Label `...: On` / `...: Off` plus inverted fill when on. |
| Disabled | 1 px dashed border, full ink, no opacity; label ends " unavailable". |
| Primary action in a row | 3 px solid border and bold (weight, not color). |
| Notice or error | Text in a box with a 3 px double border. |
| Rules | 1 px solid between sections, 3 px solid under a masthead. |
| Focus | `:focus-visible { outline: 3px solid <ink>; outline-offset: 3px }`. |
| Targets | at least 48 x 48 CSS px, at least 8 px between separate targets (list rows separated by a 1 px rule are allowed by section 4.6). |
| Type | sizes in `rem`; serif headlines, condensed uppercase subheads, system-ui body; no fixed heights on text containers. |
| Contrast | at least 15:1 (the two pairs measure 15.28:1 and 16.25:1; strict pair 21:1). |
| Words | "Helper", never "AI", "model" or "assistant". |

### 5.2 Main page, under panes

`panes.css` supplies: pane ink for every control (section 3.3), custom checkbox, radio and range paint (3.5), italic full-ink placeholders (3.5), the Hand button (4.1), and the thumb-side Shade and Lock row. Anything in the table above that was already true on the main page (square corners, 1 px borders, dashed disabled, 3 px focus ring, 48 px targets) is unchanged and was re-measured: 0 targets under 48 px in all 36 combinations (the 4 px `#meter` gauge is exempt; it has a 48 px strip).

The placeholder, checkbox and radio rules are Jaryo fixes that belong in `jaryo.css`, and are in `panes.css` only because the page is within 64 bytes of its budget (the Network page, which has no such budget, draws its own checkbox: section 5.3, N17). If `ui/wave0` frees bytes, move them (open question 5).

### 5.3 `/network` (`net_page.py`): every violation, measured at 1392 x 900 in dark theme

The page has its own dark palette and ignores the light theme and `jaryo_theme`. Counts are elements, measured.

| # | Today | Rule | Exact fix |
|---|---|---|---|
| N1 | 11 off-token colors: `#111` page, `#eee` text (30 elements), `#333 #444 #555 #666` borders, `#2a2a2a` buttons, `#1b1b1b` inputs and box, `#bbb` notes, `#9ecbff` link and primary, `#ffb4a9` error, `#fff` checkbox | two colors | Delete the page's whole `<style>` body. Start from `firstrun_page.CSS` (`from opos.firstrun_page import CSS as _BASE`): it has the token block with light and dark, buttons, focus ring, `.note`, `.btn`, `.kick`, `h1`/`h2`. Add the net-only rules of 5.3.1. Add the same one-line head script `_HEAD` uses to apply `jaryo_theme`. |
| N2 | 9 controls with `border-radius: 8px` (`button`, `#pw`, `#home`), `#sel` 12 px | square corners | covered by `_BASE` and the input rule below |
| N3 | `button.go` (Join) is primary by `#9ecbff` fill | primary = weight | markup: `class="go"` to `class="main"` (3 px border, bold, from `_BASE`) |
| N4 | `button:disabled { opacity: .45 }` on 5 buttons, solid border | dashed, no opacity, " unavailable" | `_BASE` has the dashed border; add `button:disabled::after { content: " unavailable" }` |
| N5 | `.note { color: #bbb }` grey paragraphs | no grey | `_BASE` `.note` is the 3 px double notice box in ink |
| N6 | errors in `#msg` via `m.style.color = bad ? '#ffb4a9' : '#eee'` | notice = double box | JS: `m.toggleAttribute('data-bad', !!bad)`; CSS: `#msg[data-bad] { border: 3px double var(--fg); padding: 8px 12px }` |
| N7 | "Show the password" label is 32 px tall (measured 609 x 32) | 48 px target | `#srow { display: flex; align-items: center; gap: 12px; min-height: 48px }` |
| N8 | `body { font: 20px/1.4 }`, `h1 1.4em` | rem, serif headline, 3 px masthead rule | `_BASE`: `1rem`, Georgia h1 at 2.25rem with the 3 px rule; header `border-bottom: 3px solid var(--fg)` |
| N9 | header and list rules `1px #333`, `1px #2a2a2a` | rules in ink | `border-bottom: 1px solid var(--fg)` on `#st li`; `section + section { border-top: 1px solid var(--fg) }` |
| N10 | `#home` link is a colored, rounded, outlined anchor | text button | markup: `class="btn"` |
| N11 | no `:focus-visible` rule | 3 px ring | from `_BASE` |
| N12 | inputs `#1b1b1b`, `1px #666`, 8 px radius, `padding: 0 12px` | token, 1 px ink, square | `input[type=password], input[type=text] { width: 100%; min-height: 48px; margin: 8px 0; padding: 8px 12px; background: var(--bg); color: var(--fg); border: 1px solid var(--fg); border-radius: 0; font: inherit }` |
| N13 | empty `<p class="note">` would render as an empty double box under `_BASE` | no empty boxes | `.note:empty { display: none }` |
| N14 | two-column breakpoint 1200 px, `--hinge:42px` | align with the first-run page | `@media (min-width: 1000px)` (as `firstrun_page`) |
| N15 | `#nets button` margin 4 px | 8 px between targets | `margin: 8px 0` |
| N16 | `net_page.LINK` (the Network link in Settings, `OPOS_NETWORK=1` only) has inline `color:#9ecbff` | two colors | `style="display:inline-block;min-height:48px;line-height:48px;color:inherit;text-decoration:underline"` (+26 B, only where `OPOS_NETWORK=1`; `len(LINK)` goes 203 to 229, under the test's 300; `web.page()` default is unchanged) |
| N17 | the "Show the password" checkbox is the engine's: blue (`#0075ff`, `#99c8ff`) when checked and white in the dark scheme, on a phone, below the two-panel gate and on the Duo image as it ships (`OPOS_SPAN` is still commented out); only the spanned window repaired it, through `panes.css` | two colors, a box, a word | `input[type=checkbox]` in `_NET_CSS` (below): `appearance: none`, a 24 px square, 2 px solid ink border, square corners, checked = background ink, `accent-color` and `color` ink. In the page's own style, so it holds with the panes off; `panes.css` rule 11 paints the same box on the spanned window |

#### 5.3.1 The net-only rules (verified)

```css
header{display:flex;align-items:center;gap:16px;padding:12px 16px;border-bottom:3px solid var(--fg)}
header h1{border:0;padding:0;margin:0}
main{display:block;padding:12px 16px}
section{padding:8px 0}
section+section{border-top:1px solid var(--fg)}
#gap{display:none}
@media (min-width:1000px){main{display:grid;grid-template-columns:minmax(0,1fr) var(--hinge) minmax(0,1fr);padding:12px 0}#gap{display:block}main>section{padding:8px 16px;border-top:0}}
ul{list-style:none;margin:0;padding:0}
#st li{padding:8px 0;border-bottom:1px solid var(--fg)}
#nets button{display:block;width:100%;text-align:left;margin:8px 0}
input[type=password],input[type=text]{width:100%;min-height:48px;margin:8px 0;padding:8px 12px;background:var(--bg);color:var(--fg);border:1px solid var(--fg);border-radius:0;font:inherit}
label{display:block;margin:8px 0}
#srow{display:flex;align-items:center;gap:12px;min-height:48px}
input[type=checkbox]{-webkit-appearance:none;appearance:none;flex:none;width:24px;height:24px;margin:0;border:2px solid var(--fg);border-radius:0;background:var(--bg);color:var(--fg);accent-color:var(--fg)}
input[type=checkbox]:checked{background:var(--fg)}
#sel{border:1px solid var(--fg);padding:12px;margin-top:12px}
#msg{min-height:1.4em;font-weight:700}#msg[data-bad]{border:3px double var(--fg);padding:8px 12px}
button:disabled::after{content:" unavailable"}
.note:empty{display:none}
```

The rendered page is 10,560 bytes (limit asserted in `tests/test_net.py`: 12,000), 118 of them the first-run lines F1 to F3, which the page shares. `tests/test_net.py` pins no color or class of this page. `proto/patches/net_page.diff` is the complete edit.

### 5.4 `/firstrun` and `/timezone` (`firstrun_page.py`): measured

They already follow Jaryo (tokens, light and dark, 0 off-palette colors, 0 radius, 0 controls under 48 px, 3 px focus ring, primary 3 px, disabled dashed, notices in 3 px double). What is left:

| # | Today | Fix |
|---|---|---|
| F1 | `input[type=search]` placeholder is the engine's grey (about 4.6:1) | append `::placeholder{color:var(--fg);opacity:1;font-style:italic}` |
| F2 | disabled buttons have the dashed border but no " unavailable" label | append `button:disabled::after{content:" unavailable"}` |
| F3 | time zone list rows touch (measured "Johannesburg / Lagos 0 px") | `.list{display:flex;flex-direction:column;gap:8px;border:0}` and `.list button{border:1px solid var(--fg)}` (or accept the docs/jaryo.md section 4.6 "rows with 1 px rules" reading; open question 6) |
| F4 | the two columns are hinge-exact only when the window is exactly 1392 px (`.cols { max-width: 1392px; margin: 0 auto; padding: 16px }` with `1fr 42px 1fr`) | with panes on, rule 9 of `panes.css` sets the columns from `--jp-x1` and `--jp-x2`, so it is exact for any window (verified); without panes it is unchanged |
| F5 | "Skip setup" sits in the list column | accepted; it is an escape hatch, and moving it would change the DOM |

F1 to F3 are additive CSS lines in `firstrun_page.CSS` (top of the file). `fix/astra-os` edits a JS line far below (`$('home').onclick`) in the same file, so they do not touch.

### 5.5 Pages added by other branches

`/update` (OTA) and `/monitor` (wire/monitor) are not touched here. They get no panes until their HTML passes through `panes.inject_bytes(body, nonce)` and `panes.js` lists their path; each MUST first pass the checklist in 5.1.

## 6. Delivery: byte plan and hooks

### 6.1 `web.page()` growth: 0 bytes

The page, the budget and the hook:

- Measured with the recipe of `tests/test_uxp30_budget.py` (43-character token, 22-character nonce): `page()` is 102,336 bytes without a span and 102,293 with `OPOS_SPAN` (the span squeeze removes about 43). Budget 102,400. Headroom 64 and 107 bytes.
- With `OPOS_PANES=1` the same calls return the same bytes. Growth: **0 bytes**. There is no hook in `page()`.
- The served response gains 137 bytes (two tags with a 22-character nonce), outside `page()`.
- The new CSS and JS are separate assets, served only with the flag on, as the keyboard's are.

### 6.2 New files (about 17.5 KB, none loaded with the flag off)

| File | Bytes | Role |
|---|---|---|
| `src/opos/panes.py` | 5,473 | flag words, `css_text`, `asset`, `tags`, `inject`, `inject_bytes`, `serve` (mirrors `osk.py`) |
| `src/opos/panes_static/panes.css` | 8,250 | surfaces, roles, placement, chrome, OS pages, native controls (`@MIRROR@` inside the media block) |
| `src/opos/panes_static/panes-mirror.css` | 1,780 | the Left mirror; omitted by `invert` |
| `src/opos/panes_static/panes.js` | 1,973 | Hand button, `data-hand`, `data-panes` switch |
| `tests/test_panes.py`, `tests/panes_dom_test.js` | 17.1 KB, 7.2 KB | section 8 |

The files themselves are the reference: `src/opos/panes_static/panes.css` is the style sheet of sections 3, 4 and 5, `panes-mirror.css` the Left mirror of section 4.3 and `panes.js` the Hand button and the two-pane switch of section 4. This file keeps no second copy of them.

`packaging/pmos/runtime-files.txt` (sorted) MUST gain, between `src/opos/osk_static/osk.js` and `src/opos/power.py`: `src/opos/panes.py`, `src/opos/panes_static/panes-mirror.css`, `src/opos/panes_static/panes.css`, `src/opos/panes_static/panes.js`. Without them the image ships no panes and `osk.py` silently skips the import.

### 6.3 The hook (exact)

Recommended, Option A: chain through the module every full page already passes through. `osk.inject_bytes` is called from all four full-page routes (`web.py` `/`, `web.py` `_net_serve`, `firstrun.py` `route_get` for `/firstrun` and `/timezone`), and `osk.serve` from `Handler._get`. No in-flight branch edits `osk.py`. Seven lines, nothing in `web.py`, `firstrun.py`, `net.py` or `page()`, so nothing to conflict with `fix/astra-os` (which edits `firstrun.py`'s `route_get`, where Option B would hook), `wire/monitor`, `ota/*` or `ui/wave0`:

```diff
--- a/src/opos/osk.py
+++ b/src/opos/osk.py
@@ -17,6 +17,10 @@
 from pathlib import Path

 from opos import secure
+try:  # panes: the black/white two-pane surfaces ride on this module's injection (src/opos/panes.py); optional like the keyboard
+    from opos import panes as _panes
+except ImportError:
+    _panes = None
@@ def inject_bytes(body, nonce, env=None):
+    if _panes is not None:
+        body = _panes.inject_bytes(body, nonce, env)  # panes: the same bytes object while OPOS_PANES is unset
     if not enabled(env):
         return body
@@ def serve(handler, path, env=None):
     a = asset(path, env)
     if a is None:
-        return False
+        return _panes is not None and _panes.serve(handler, path, env)  # panes: /panes.css and /panes.js, only while OPOS_PANES is set
```

Consequences to keep: with `OPOS_OSK` unset, `osk.inject_bytes` still returns the very same object when `OPOS_PANES` is unset (`test_osk.py::test_inject_bytes_is_the_same_object_while_off` keeps passing, verified); panes works whether the keyboard is on or off (tested both ways); a runtime without `osk.py` (`web.osk = None`) serves no panes, which is the same fail-open the keyboard has.

Option B, if the owner does not want panes inside the keyboard module: five explicit one-liners mirroring the keyboard's, each guarded by `if panes is not None`: `web.py` import beside the `osk` import, `_get` beside `osk.serve`, the `/` branch and `_net_serve` beside `osk.inject_bytes`, and `firstrun.py` `route_get` beside its `osk.inject_bytes`. The `firstrun.py` line is inside `route_get`, the function `fix/astra-os` edits (about 17 lines above it); expect a manual merge.

### 6.4 Injection details

- Where: before the first `</head>` (before the last `</body>` if there is none), so the style sheet comes after the page's own `<style>` and wins ties by source order, and `html[data-hand]` is set before the first paint. The keyboard injects before `</body>`; both are idempotent and independent.
- Tags: `<link rel="stylesheet" href="/panes.css" nonce="N"><script src="/panes.js" nonce="N"></script>`. The nonce is the page's own, checked against `[A-Za-z0-9_-]{1,128}` as the keyboard does. CSP is unchanged: `style-src` and `script-src` are nonce-only, `connect-src 'self'`; `panes.js` makes no request. Checked: 0 console errors, 0 CSP violations in all runs.
- Served with `secure.security_headers()` (`no-store`, `nosniff`, `same-origin`). `/panes.css` is built per request: placeholders filled (`@K@` and friends), then `span.rewrites(span)` applied and `span.variables(span)` prepended once, from `handler.span`. That is the single source of geometry: the same function that rewrote the page's own rules rewrites these.
- No `OPOS_SPAN` and no browser-reported segments: the gate never matches and the sheet does nothing. In a browser that does report two segments (not testable on this PC) the source form's `env(viewport-segment-*)` values are used as written.

### 6.5 What is shared with the keyboard

The nonce check and tag shape, the "same bytes object while off" contract, the `serve` body (about 15 lines, copied rather than factored so `osk.py` stays small), the static-asset folder pattern, the `runtime-files.txt` inventory, the geometry (`--pane`/`--hinge` from `OPOS_SPAN`), and the injection point in the three routes. The keyboard's halves get panes colors through rule 7 of `panes.css`.

### 6.6 Deployment and docs

- Unit: `packaging/duo-os/opos-server.service` sets `Environment=OPOS_PANES=1` in its own `# >>> track panes` block next to `OPOS_SPAN` (answering open question 2 with yes). Panes do nothing without the span, and the span is still a commented line until it is measured on the phone, so the image shows no change until `OPOS_SPAN=675,42` is turned on. `verify-image.sh` has a matching `track panes` block.
- Docs: this file as `docs/jaryo-panes.md`; one row `OPOS_PANES` in the `docs/duo-os-final.md` environment table; a note in `docs/jaryo.md` section 13 ("on a spanned window the two pane surfaces replace Light/Dark; see jaryo-panes.md"); one `CHANGELOG.md` line; one line in `packaging/duo-os/README.md` where `OPOS_OSK` is.
- Shell: tests run on Linux in WSL with the script recipe (node shim, `TMPDIR` on `C:`, targeted modules only).

## 7. Alternatives to the reading, each with its single switch

`OPOS_PANES` takes a comma list of words (`1`, `on`, `mirror`, `invert`, `theme`, `pure`); any known word turns the feature on. Unknown words turn it off, and so does `touch` until it is built: the table below says "specified, not built" for it, and a flag word that turned the feature on but behaved like `1` would be a promise the build does not keep.

| Alternative | What it does | Switch | Status |
|---|---|---|---|
| Invert, not mirror ("invert for the left" read as colors swapping) | Left: layout does NOT mirror; the thumb pane stays on the right and becomes white, the list pane on the left becomes black | `OPOS_PANES=invert` (`panes-mirror.css` omitted; the color swap stays) | built and screenshot-verified |
| Both panes follow the system theme | no fixed black and white; both panes use `--jaryo-bg/--jaryo-fg` (Light, Dark or System); the split reads only by the 42 px hinge strip, which then has the page's background | `OPOS_PANES=theme` (the four literals become `var(--jaryo-bg/--jaryo-fg)`, `color-scheme: normal`) | built; computed colors verified (both panes equal) |
| Strict AGIW pair | `#000` / `#fff`, contrast 21:1 | `OPOS_PANES=1,pure` | built; verified (`#fff` left pane) |
| Active = last touched pane | colors (not content) follow the last pointer: the pane last touched is black; Hand is hidden | `OPOS_PANES=touch` (not a flag word yet) = `invert` plus: in `panes.js`, `document.addEventListener('pointerdown',function(e){d.dataset.black=e.clientX<(window.innerWidth/2)?'left':'right'},true)` with initial `right`, no `#jaryo-hand`; in `panes.css` the Left physical block's selector becomes `html[data-panes=two]:is([data-hand=left],[data-black=left])` | specified, not built |
| Home stays in the list pane (leave Home, All apps and Settings where `improve/hinge-layout` put them) | the thumb pane is empty on those three screens | delete the three `#home`, `#apps-list`, `#settings` entries from the placement list (4 rules in 2.4) | specified, not built |
| White hinge | hinge strip is the white surface | change `background: var(--jp-k)` to `var(--jp-w)` on `html::after` | one declaration |

## 8. Tests to write

All stdlib, run on Linux (WSL) with the usual shim; skip when `node` or a headless Chromium is missing, as `test_uxp22_twopane.py` does. The reference versions of the first four are in `proto/tests/`.

1. **`tests/test_panes.py` (Python strings and server)**
   - Flag words parse as in section 7; unset and unknown words give "off".
   - Tokens equal `jaryo.css` (`#e6e2d9/#000`, `#1a1a1a/#f3f0e8`); with `pure` only `#000` and `#fff` appear.
   - No `@font-face`, `url(`, gradient, `box-shadow`, `filter`, `text-shadow`, `rgb(`, `!important`, `transition`, `animation`; the only `opacity` is `opacity:1`.
   - Every rule inside the gate starts `html[data-panes=two]`; the one rule outside is `#jaryo-hand{display:none}`.
   - Without a span: one `(horizontal-viewport-segments:2)` block and the two `env()` names; with `Span(675,42)`: `(min-width:1055px)`, no `viewport-segment`, `:root{--pane:675px;--hinge:42px}` once.
   - `invert` omits the mirror but keeps the color swap; `pure` equals soft with four values replaced.
   - `page()` bytes are identical with and without `OPOS_PANES`, contain no `panes`, `jp-` or `jaryo_hand`, and stay under `PAGE_BUDGET` (102,400).
   - Injection: before the first `</head>`, after the page's `<style>`; idempotent; same object when off; bad nonce raises; chained through `osk.inject_bytes` with the keyboard on, off, and `web.osk = None`.
   - Routes (`webclient.Running`): flag off is 404 `{"error": "not found"}` for both files and a byte-identical page; flag on serves both with `no-store`, `nosniff`, correct content type, HEAD empty; `/network` carries the tags; the page minus the tags equals `page()`.
   - `runtime-files.txt` lists the four new files, sorted.
   - `touch` is not a flag word (alone or in a list); the root takes the right pane's ink in soft and in `pure`; no role rule states `color` on a button (section 3.3); the flag-off sentence of section 0 is pinned in this file, in the `panes.py` docstring and in `opos-server.service`, and the older wordings are pinned out of those and of `docs/duo-os-final.md` and `CHANGELOG.md`.
2. **`tests/panes_dom_test.js` (node vm, fake DOM)**: default Right and no write; label `Hand: Right`; button right after `#jaryo-theme`; a tap flips to Left, stores `left`, relabels; tap again; reload reads the stored value in the head before the DOM exists (`readyState 'loading'`); garbage in storage is Right; throwing storage does not throw and the tap still flips the page; a `storage` event re-reads; other keys ignored; `data-panes` is `two` for `/` only with `data-ff=foldable` (and follows `data-ff` arriving or leaving), always for the three OS pages, never for other paths; no button without `#jaryo-theme`; one button after repeated `DOMContentLoaded`. 31 assertions pass in the prototype.
3. **`tests/test_panes_browser.py` with `tests/panes_browser_driver.mjs` (browser geometry)**: serve with `OPOS_PANES=1` and `Span(675,42)`; headless Chromium driven over the DevTools port by node 22+ (the global `WebSocket`; the shared UXP21 driver speaks the pipe, which Windows Chrome and Edge do not), 1392 x 900, `deviceScaleFactor` 1 (CSS px equal at 2); each of lock, home, All apps, phone, messages, helper, settings, shade; each of Right and Left; each of light and dark (`jaryo_theme`). For each:
   - `data-ff="foldable"`, `data-panes="two"`, `data-hand` as set.
   - Surfaces: computed `html::before` background (left pane) and `html` background (right pane) are `rgb(243,240,232)` and `rgb(0,0,0)` in Right, swapped in Left; screenshot pixels at (300, 880), (696, y) and (1000, 880) are those two colors and `#000000` for the hinge at three heights.
   - No text rectangle (a `Range` over each own text node, not over an element that contains controls) and no control intersects x 675 to 717.
   - Every interactive element is at least 48 x 48 (a radio or checkbox measured by its label; `#meter` exempt).
   - Contrast: for each text rectangle, the computed ink against the screenshot color at the rectangle's corners is at least 15:1 (measured minimum 15.28).
   - Palette: computed `color`, `background-color`, border and outline colors of every visible element are in `{#000, #e6e2d9, #f3f0e8, #1a1a1a, transparent}`; no `box-shadow`, `filter`, `background-image`, or `opacity < 1`.
   - Mirror: the thumb items' `left` is 729 in Right and 12 in Left; the masthead `left` is 0 and 717; the Shade is at x 0 and 717 with width 675; `#jaryo-status-row` buttons are in the thumb pane.
   - Clicking `#jaryo-hand` in Settings flips `data-hand` and the layout live and `localStorage.jaryo_hand`; Enter on the focused button does the same.
   - Off cases: 675 x 900 Auto, 1000 x 700, 1392 x 900 with Slab, and 675 x 900 with Foldable forced all have no `html::before` content and no visible `#jaryo-hand`.
   - OS pages (`/network`, `/firstrun` at the welcome and time zone steps, `/timezone`): the same checks, plus `main`'s columns are 675 / 42 / 675.
   - Keyboard (with `OPOS_OSK=1`, a touch tap on `#msg`): the left half's keys are `#1a1a1a` on `#f3f0e8` in Right and `#e6e2d9` on `#000` in Left; the right half the reverse.
   - Motion smoke: with `prefers-reduced-motion: no-preference`, open and close Messages, Phone, Helper and Settings from Home: no console error, animations end, focus returns to the tile, positions as in 2.4.
   - Pressed: every visible control of every state (the main page, the OS pages, the keyboard) is pressed with `:active` forced through the DevTools protocol, in both themes and both hands, and, in the dark run, with a real mouse-down that is also photographed (clicks are stopped before they reach the page). Each label reads at least 15:1 on its fill; every enabled control that is not already on inverts; Home, Close, Unlock, Call and the Network Home link are black on `#e6e2d9`; the picture of a pressed button still has a label in it (the browser restyles a pressed control a frame late, so the driver waits a frame and presses again before it believes "no change").
   - The root's computed `color` and `caret-color` are the right pane's ink (`#e6e2d9` in Right, `#1a1a1a` in Left).
   - The Network page's password checkbox, unchecked and checked, at 390 x 844 and 1000 x 700 in both themes (the panes do not paint there) and at 1392 x 900 in both hands: appearance none, 24 px, 2 px ink border, square corners, and in the pixels of the box only the page's two colors (the ink in the border and, checked, in the middle).
   - The driver waits for the page it loaded, never a fixed time (`tests/panes_wait.mjs`, unit-tested by `tests/panes_wait_test.mjs`); a server that answers three seconds late must not break it.
4. **Conformance tests for the OS pages** (`tests/test_panes_pages.py`, a new file so that the branches that edit `test_net.py` and `test_firstrun.py` do not conflict): the net page CSS has no color literal outside the four tokens, no `border-radius` other than 0, no `opacity`, contains `button:disabled::after`, `#msg[data-bad]`, `.note:empty`; its JS has no `style.color`; the `Join` button has class `main`; `LINK` has no `#9ecbff`; the first-run CSS has `::placeholder` and the disabled label rule.
5. **Existing tests that must keep passing** (run, do not edit): `test_osk` (identity of `inject_bytes` while off), `test_span` (all), `test_uxp30_budget`, `test_uxp22_twopane`, `test_net` (12,000 byte limit, `len(LINK) < 300`), `test_firstrun` (`Pages`), `test_duo_final` and `test_runtime_source_archive` inventories (the digest failure is the known one).

Existing tests that this feature must not break are listed above. Numbers checked by hand on the prototype, for the record: `page()` 102,336 B (102,293 with a span) with and without `OPOS_PANES`, under 102,400; rendered net page 10,442 B under 12,000; `len(net_page.LINK)` 229 under 300.

Prototype results, for the record (Edge 157, 1392 x 900): 18 main-page state, hand and theme combinations plus 12 OS-page combinations in each hand: 0 text or control on the hinge, 0 targets under 48 px, minimum contrast 15.28, palette limited to the four colors and transparent, hinge pixels `#000000` everywhere. One expected false positive when a `Range` is taken over a label that contains a checkbox (use text nodes).

## 9. Risks, limits, open questions

Limits and risks:

- Not measured on the phone: the real Chromium's rendering of `html::before` layers and `appearance: none` controls at output scale 2, the real hinge mask, and the Georgia and Arial Narrow fallbacks (the audit's caveat stands). Native `env(viewport-segment-*)` (no `OPOS_SPAN`) cannot be tested on this PC.
- Rows are shared between panes in the grid (`grid-auto-flow: row dense`), so a tall item in one pane pushes the next row's start in the other. This is today's behaviour, visible in Phone (the number field aligns with the masthead row); a fix needs a wrapper element or subgrid and is out of scope.
- The All apps letter index (H, M, P, S for four apps) is still the audit's geometry-10 finding; panes do not change it.
- The Shade now covers the whole list pane. A person who wants to see the page behind it sees only the thumb pane's half. That is the point of the white pane, but it is a visible change from the 384 px strip.
- The zoom animations (`jaryo.js`) now fly between panes for Messages (tile in the thumb pane, `#threads` in the list pane). Smoke-tested in both hands; the hinge strip is drawn over the flight.
- `panes.css` leans on the specificity of `html[data-panes=two][data-ff]` and `#id` lists to beat `html[data-ff="foldable"] #phone>#threads~*{grid-column:2}` (2,1,1). If `ui/wave0` or later work adds a higher-specificity placement rule, the Browser test (mirror positions) is what fails.
- The in-image runtime ships panes only if `runtime-files.txt` lists the four files (6.2).

Open questions for Louis:

1. Soft or strict whites? Default is the Nov 3 soft pair; `pure` is one word. Which should the Duo image set?
2. Default on? Should `opos-server.service` set `OPOS_PANES=1` once `OPOS_SPAN` is enabled, or stay a commented line until the phone has shown it?
3. Hinge color: black (the physical hinge) or white?
4. "Invert for the left": is it a mirror (built) or a color swap with no mirror (`invert`)?
5. The placeholder, checkbox and radio fixes: leave in `panes.css`, or move into `jaryo.css` when the page budget allows?
6. Time zone list rows: 8 px gaps between boxes (strict), or rows with 1 px rules (section 4.6)?
7. Should Settings > Display also offer "Hand" on one-pane windows (it does nothing there; currently hidden)?

## 10. What was run on this branch (ui/panes, 2026-11-08)

Headless Edge 157 at 1392 x 900 CSS px with `OPOS_SPAN=675,42` and `OPOS_PANES=1`, against the real server. Not run on a phone: the real Duo
Chromium at output scale 2, the real hinge mask and the native `env(viewport-segment-*)` path (no `OPOS_SPAN`) are all still unchecked.

| Run | Result |
|---|---|
| `tests/test_panes.py` | 24 tests (19 first; then `touch` off, the root's ink, no role `color` on a button, two for the flag-off wording): flag words, tokens, no forbidden CSS, the gate on every rule, span rewriting, modes, the injection and its chain through `osk.py`, routes on and off (flag off serves `/` and `/network` byte for byte as `page()` and `net_page.render()` build them), the runtime inventory |
| `tests/panes_dom_test.js` (run by `test_panes`) | 36 assertions (the Hand button with a throwing store and with a store whose writes fail) |
| `tests/test_panes_pages.py` | 13 tests: the OS pages' colors, corners, opacity, disabled, primary, notice and link rules, the password checkbox rule |
| `tests/test_panes_browser.py` (23 tests, Edge: 21 of the pages and 2 of the driver's waiting; about 225 s) | the pressed state of every visible control (forced and a real mouse-down), the root's ink, the password checkbox at three window sizes, and: 36 main-page and 20 OS-page state, hand and theme combinations: surfaces and hinge pixels `#000000`, 0 text or controls on the hinge, 0 targets under 48 px, minimum contrast at least 15, only the four palette colors, mirror positions, the Hand button (tap, reload, Enter), the one-pane windows unchanged, keyboard halves, motion smoke, 0 console errors |
| Existing, WSL Ubuntu | `test_osk` 29, `test_span` 43 (7 browser tests skipped: no Chromium in WSL), `test_uxp30_budget` (`page()` 100,641 B of 102,400), `test_net` 71, `test_firstrun` 82, `test_firstrun_wiring`, `test_uxp22_twopane`, `test_duo_final`, `test_docs_claims`, `test_duo_os_docs`, `test_duo_os_clock`, `test_selftest`, `test_web_security`, `test_duo_kiosk`, `test_lean_image` and the docs tests pass |
| After the independent check (2026-11-08) | Six findings fixed (pressed label on Home, Close, Unlock, Call and the Network Home link; the Network checkbox with the panes off; the Hand label with storage blocked; the flag-off wording; the `touch` word and the root color; the driver's fixed wait). Windows Python 3.13 and headless Edge: `test_panes`, `test_panes_pages`, `test_panes_browser`, 60 tests OK. WSL Ubuntu, Python 3.14.4, a native copy of the branch tip, the Windows node behind a shim: those three modules plus `test_net`, `test_firstrun`, `test_osk`, `test_uxp30_budget`, `test_docs_claims`, `test_docs_script_paths`, 260 tests OK (23 skipped: no Chromium in WSL). With the four source files of the fixes put back as they were, 13 of the new tests fail and the other 47 pass. |
| Environment failures on `/mnt/c` only | `test_net` `test_actions_are_rate_limited` (wall-clock refill on a saturated VM), `test_firstrun` `Flag.test_the_flag_is_private_and_no_temp_file_is_left` and three `test_duo_kiosk` file-mode checks (drvfs reports 0777); all pass on ext4. `test_runtime_source_archive` fails on the release recipe digest, as it did before this branch |

Served bytes, with a real token and nonce (`page()` itself is 100,641 B with or without the flag, 100,573 B with a span; the `/network` and `/panes.*` rows were measured again after the checkbox rule, the root color and the Hand fix, the rest are as first measured):

| Response | Flag off | `OPOS_PANES=1` | Duo image (`OSK`, `NETWORK`, `FIRSTRUN`, `PANES`, span) |
|---|---|---|---|
| `/` | 100,870 (100,833 with a span) | 101,006 (+136, the two tags) | 101,605 |
| `/network` | 10,906 | 11,042 (+136) | 11,196 |
| `/firstrun`, `/timezone` (with `OPOS_FIRSTRUN=1`) | 13,765 and 7,840 | +136 each | 14,055 and 8,130 |
| `/panes.css`, `/panes.js` | 404 | 10,032 and 1,967 | 10,032 and 1,967 |

The keyboard adds 154 B to each full page and serves `/osk.js` (30,642 B) and `/osk.css` (2,663 B); the Network link adds 229 B to `/` and the time zone link 253 B, only where those flags are on.

## 11. Wave 1, layout track (`ui/w1-layout`, 2026-11-08): the hinge, the keyboard and long words

Plan items W1-10, W1-11 and W1-12 of the interface finalization plan. Headless Edge 157 at 1392 x 900 CSS px against the real server
(`python -m opos.web --span 675,42`), the same limits as section 10: nothing here was run on the Duo, and the span is still off in
`opos-server.service`.

### 11.1 W1-11: nothing on the hinge (x 675 to 717)

The check is the audit's `overHinge` rule (every visible interactive element whose box reaches into 675 to 717) plus two more, so that
"no text, button or border" is tested as written: every visible piece of text (each line box of each text node) and every box with a
visible border. It was run on the lock screen, Home, All apps, Phone (idle, in a call, after a refused call), Messages (empty and after
a sent text), Helper (waiting and after an answer), Settings and the open Shade.

| Configuration | Before (`11c39ba`) | After |
|---|---|---|
| `--span 675,42` only | 10 of 12 screens list `#meter`, the 4 px gauge button, from x 0 to 1392 (control and border); text: none | every list is empty |
| `OPOS_PANES=1 OPOS_OSK=1`, Hand: Right | empty (`panes.css` already keeps `#meter` in the first pane) | empty |
| the same, Hand: Left | empty | empty |

Settings, Device type reads "Auto looks like: two screens. Window 1392 x 900 CSS px at 1x, span 675 + 42 + 675." in all three.

The one thing found was the gauge. `#meter` is `left:0;right:0;width:100%`, so with the span on and the panes off its border and its fill ran
across the hinge (its caption is placed from its right edge, so it sat in the second pane). `span.EXTRA`, the block of rules the span adds
for what the segment rules leave on the hinge, now also carries `#meter{right:auto;width:var(--pane)}`: the gauge is the first pane's, as
the panes already made it, and its fill is a percentage of that pane. It is 38 B in the spanned page and nothing in the unspanned one
(`page()` is unchanged by this item). `tests/test_span.py` asserts the rule and its browser test now counts `#meter` (it used to skip
it), adds the Phone states "in a call" and "after a refused call", and checks that those two states were drawn.

Not done, on purpose: `#meter-hit`, the transparent 48 px pull strip, stays full width. With Hand: Left the panes put the gauge and the
handle in the second pane, and the handle is positioned inside `#meter-hit`; clipping the strip to the first pane would leave the
handle outside it. The hinge-horizontal orientation (D18) was not looked at.

### 11.2 W1-12: the field and its buttons above the keyboard

Run with `OPOS_OSK=1` (the in-page keyboard, which publishes its height as `--osk-h` and adds a padding element of that height to the end of
the page), a real mouse press on the field, headless Edge. "Above" means the bottom of the box is not below the top of the keyboard and the
centre of the box is not covered. The alphabet keyboard is 306 px high, the digits keyboard 256 px.

| Window | Before (`11c39ba`) | After |
|---|---|---|
| 1392 x 900, spanned, Messages | `#msg` 534-582, Send 631-679: Send under the keyboard (top 594) | `#msg` 406-454, Send 503-551 |
| 1392 x 900, spanned, Phone | `#number` 110-158, Call 243-291 (keyboard top 644): fine | the same |
| 675 x 900, Messages | `#msg` 534-582, Send 631-679: under the keyboard, and covered by it | `#msg` 421-469, Send 518-566 |
| 675 x 900, Phone | `#number` 489-537, Call 586-634 (keyboard top 644): fine | `#number` 456-504, Call 553-601 |
| 390 x 844, Messages and Phone (not asked for) | Send 575-623 and Call 626-674 under the keyboard (tops 538 and 588) | Send 447-495, Call 498-546 |
| Helper, field top over four Asks (1392 / 675) | 122, 184, 208, 262, 285 / 393, 456, 479, 533, 557: it walks down on every Ask, and on the single pane the Send bar is under the keyboard from the third | 122, 406, 406, 406, 406 / 393, 406, 406, 406, 406: it moves once, when the first question makes the log appear, and not when an answer arrives; the field and the Send bar stay above the keyboard |

`OPOS_PANES=1`, Hand: Right and Hand: Left give the same figures on the two windows tried (1392 spanned, 675), the Helper included.

What was done differs from the plan's text, and why:

- The plan's `#controls{position:sticky;bottom:var(--osk-h,0px)}` was built and measured first. In the spanned grid a sticky box cannot leave its
  grid area, and `#controls` is stretched to its grid row, so Send ended at y 240, above the field; on the single pane the stuck bar covered
  `#msg` even with no keyboard up. It is not used, and
  `tests/test_w1_layout.py` fails if `sticky` comes back into `phone-b.css`.
- Instead the browser's own scroll-into-view does the work. `body{scroll-padding-bottom:var(--osk-h,0px)}` makes the keyboard's height part of
  the scroller's edge, and `#number,#msg,#q{scroll-margin-bottom:140px}` makes the box a field must fit include the buttons under it (Send and
  Call sit 97 to 133 px below their field, the Helper's Send bar 64 px). The keyboard's `reveal()` already calls `scrollIntoView`, so a tapped
  field goes where both fit. No `body{padding-bottom}`: the scroll room is the keyboard's own padding element, and a second one would double it.
  The margin is on the three text fields only; on every input it would also move a checkbox or a slider that takes focus under a finger.
- `#hs-log{height:35dvh;overflow-y:auto}` is a height, not the plan's `max-height`: the log sits above the field, so with a maximum the field
  still moved on every answer until the log was full. The cost is one jump, when the first question makes the log appear. Two lines in
  `draw()` (phone-b.js) go with it: the log is scrolled to its newest turn, and a field that has focus is scrolled back into view, which is how
  it stays above the keyboard on the single pane after that jump.

Not done: the plan's P2 rule that hides the masthead below 700 px of height (+90 B; the page budget does not allow it, and no Duo window is that
short). Not looked at: `OPOS_OSK=stevia` (the Wayland keyboard publishes no `--osk-h`, so these rules do nothing and the browser behaves as it did),
the keyboard's Hide key, and any of it on the Duo.

## Appendix. `panes.py` API (`src/opos/panes.py`)

`modes(env=None) -> set`, `enabled(env=None) -> bool`, `css_text(span=None, env=None) -> str` (placeholders, `@MIRROR@`, then `span.rewrites` and `span.variables`), `asset(path, span=None, env=None) -> (bytes, content_type) | None`, `tags(nonce) -> str`, `inject(html, nonce, env=None) -> str`, `inject_bytes(body, nonce, env=None) -> bytes` (the same object while off), `serve(handler, path, env=None) -> bool`. Constants `SOFT`, `PURE`, `THEME` hold the surface literals.

## Everyday app panes

Microsoft's archived dual-screen patterns are list-detail (also called master-detail), companion pane, and extended canvas ([Xamarin.Forms dual-screen design patterns](https://learn.microsoft.com/en-us/previous-versions/xamarin/xamarin-forms/app-fundamentals/dual-screen/design-patterns), [Large-screen design patterns](https://devblogs.microsoft.com/surface-duo/large-screen-design-patterns/)). On a spanned Duo window the hinge is the band from x 675 to x 717. None of these apps use an extended canvas, because a control must not sit on that band. Below 1000 CSS px the two columns become one stack. These pages do not read the hinge sensor. Folded behavior on a phone is **NOT_RUN**.

| App | Pattern | List pane | Thumb pane | Single pane | Folded |
|---|---|---|---|---|---|
| Apps index | list-detail | the app names | about these apps, and Home | the same blocks, stacked | the same stack |
| Clock | list-detail | alarms, timer, stopwatch | the editor, Stop, Snooze, Bedside, and Home | stacked | stacked; Bedside is a button until posture is seen on the phone |
| Contacts | list-detail | search and the names | the person and Call | stacked | stacked |
| Notes | list-detail | the titles | the note and New note | stacked | stacked |
| Calculator | companion pane | the result and the tape | the keys | stacked, result above the keys | stacked |
