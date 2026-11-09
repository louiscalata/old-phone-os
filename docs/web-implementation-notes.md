<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Web implementation notes

These explanatory comments were moved out of the stylesheets to keep the served page within its byte budget; CSS declarations, layout behavior and licensing notices are unchanged. Internal change labels in the original comments are replaced here with plain descriptions.

## jaryo-layout.css

```css
/* The jump row precedes the list in DOM order. Keep both full-width on a
   foldable, with matching two-column gaps so no jump button crosses the hinge. */
```

## jaryo-layout.css

```css
/* Landscape layout (docs/jaryo.md 4.1, 4.3, 4.6 and section 13). Rules only: ids, DOM order and Tab order are unchanged,
   no order:, no transition or animation, colour only through currentColor. Slab keeps its phone column and the foldable
   keeps its two-pane hinge grid (phone-b.css), so both are excluded. Outside the two >=840px blocks the landscape layout adds
   only the lock button margin below; phone-a.css owns the page's single max-width:839px block. Home's four landscape tracks
   are in jaryo.css and the slide-out sticky #controls row is in phone-b.css. */
```

## jaryo-layout.css

```css
/* Lock buttons fit the lock's width at every size. The base button rule (web.py) gives every button margin-right:8px and
   phone-a.css makes #lock buttons width:min(100%,360px); centred by the lock's flex column, that 368px margin box sits at
   -4px to 356px in a 360px viewport (measured when the landscape layout was checked), so each button lost 4px at both edges. The 4px top
   and bottom margins are the base rule's and stay. */
```

## jaryo-layout.css

```css
/* 4.1 Lock in landscape at >= 840px: two columns. Left = time, dateline (3px masthead rule), battery; right = notice and
   the buttons, the same #lock>button split phone-b.css gives the foldable. Dense placement only lets the right column
   start on the first row; items keep DOM order inside each column, so reading and Tab order are unchanged. */
```

## jaryo-layout.css

```css
/* One >=840px block for both owners (phone-b.css keeps exactly one too).
   All apps letter row on the Foldable (two-pane hinge grid, phone-b.css): jaryo-layout.js places #jaryo-letters as a #phone child just
   before #apps-list. DOM order is reading order, so a full-width item may not follow a pane item (test_uxp21_profiles order check):
   the row spans both columns like the list, and its content box is padded down to the left pane so no letter button sits in the
   24px centre gutter. With real viewport segments it keeps to column 1, like #apps-list. Below 840px #phone is one column.
   4.3 Shade at >= 840px (landscape layout): a sidebar column from the left edge; the page stays visible beside it. The base transition
   (transform .25s, visibility 0s .25s; test_uxp30_budget) is untouched, only the resting transform turns from translateY
   into translateX. #scrim still closes the shade on click and keeps the page inert to pointers; it just stops painting. */
```

## jaryo-layout.css

```css
/* With real viewport segments, keep the row in the same segment as the list. */
```

## jaryo.css

```css
/* Paint only: keep the existing shell, responsive layout and motion paths. */
```

## jaryo-layout.css header

```css
/* jaryo-layout.css: Jaryo layout layer */
```

## jaryo.css header

```css
/* jaryo.css: Jaryo theme, type and motion layer */
```

## phone-a.css header

```css
/* phone-a.css: phone-shell hook point */
```

## phone-b.css header

```css
/* phone-b.css: phone-shell hook point */
```

## Spanned page (opos.span)

With `OPOS_SPAN` or `--span` set, the server rewrites the page it serves; the files in `web_static` are unchanged and an unset span serves them as before. The segment query `(horizontal-viewport-segments:2)` (in `phone-b.css`, `jaryo-layout.css` and the `TWO` constant of `phone-b.js`) becomes a width test, the two segment `env()` values become `--pane` and `--hinge`, and the first-state Auto seed in `jaryo.js` stops deleting the Foldable choice that `phone-b.js` just made. Each of those strings is pinned by `tests/test_span.py`, so rewording one of them fails a test instead of silently leaving the rewrite out. A few rules the segment rules do not cover (status line, shade handle, lock screen alignment) and a readout in Settings are added, and the style sheet's comments and the line breaks between its rules are dropped, which keeps the spanned page under `PAGE_BUDGET`.

## Script comments

The explanatory text of six full-line `//` comments was moved out of the page scripts for the same reason (about 323 bytes). The code around them is unchanged. As above, the internal change labels (UXP20, UXP23, UXP31) are left out of the text recorded here. Five of them keep a short marker in the script because tests depend on it: `tests/test_uxp20_continuity.py`, `tests/test_uxp04_launcher.py` and `tests/test_uxp31_helper_screen.py` slice `phone-a.js` and `phone-b.js` at `// UXP20:`, `// UXP23:` and `// UXP31:`, and `tests/test_uxp00_hooks.py` looks for the file names `jaryo.js` and `jaryo-layout.js` in their script elements. The comment in `js-1.js` was removed outright.

## js-1.js header (removed)

```js
// G: dynamic get-by-ID; E: element factory.
```

## phone-a.js, before the viewport-change block (script keeps `// UXP20:`)

```js
// Layout stays in CSS; preserve the live controls across viewport changes.
```

## phone-a.js, before the state-render enhancement block (script keeps `// UXP23:`)

```js
// Enhance only after a successful state render; the base controls stay intact.
```

## phone-b.js, before helperAsk (script keeps `// UXP31:`)

```js
// One Ask request and error mapping shared by the conversation and fallback button.
```

## jaryo.js header (script keeps `// jaryo.js`)

```js
// jaryo.js: Jaryo theme and motion
```

## jaryo-layout.js header (script keeps `// jaryo-layout.js`)

```js
// jaryo-layout.js: Jaryo layout screens
```

## The page heals itself when the server goes away or restarts (W1-02)

Where: `js-4.js` (`healReload`, the error branch of `render`), `js-8.js` (`fetchState`, `pollTick`, `oposPoll`), `phone-a.js` (the reload block), `jaryo.css` (the banner), the "Restart helper" handler in `web.py`.

- Every state poll reports how it went. `pollTick` calls `oposPoll(1)` after a state poll that was answered and drawn without an exception and `oposPoll(0)` when the request failed (`fetchState` gives up after 8 s: `AbortSignal.timeout`, so a request that never answers cannot freeze the poll). `oposPoll` is a no-op in `js-8.js` until `phone-a.js` installs the real one. A state poll that is answered with an error in it (a 403 after a restart) is still an answer: the server is there, so the miss count is not touched. A tap or a simulation tick goes through `api()` (`phone-a.js`), which counts a lost request, a 5xx and a 403 as a miss itself and clears the count on a good answer; `pollTick` therefore reports only the polls it makes itself, so a lost tick is one miss, not two (it showed the banner after one lost request whenever the simulation ran).
- Two misses in a row (also two failed taps) do three things: the Reload button appears at the bottom right, `data-stale` goes on `<body>`, and the error branch of `render` is asked for the `offline` text. `jaryo.css` turns that into a fixed boxed banner under the status bar (3 px double border, the Jaryo tokens) and a "?" before the battery figure. One good answer clears all three. The banner is a `position:fixed` box, so showing it moves nothing, and it takes no taps (`pointer-events:none`): at Largest text it wraps to two lines and would otherwise cover the Shade and Lock buttons of the status row. When the window spans two panels it is one pane wide (`span.py` EXTRA: `[data-stale] #notice{max-width:var(--pane)}`; the variable exists only in a spanned page, `tests/test_span.py` keeps it out of `page()`), and with `OPOS_PANES=1` it is the list pane's own box (`panes.css`, list tokens: white with dark ink on the right-hand setting, black with light ink on the left), so the thumb pane's Shade, Lock and Reload buttons are never under it and the white pane stays white (`docs/jaryo-panes.md`).
- A 403 means the server restarted with a new token. The error branch says "Restarting the screen. One moment." and, once per 10 s (`sessionStorage.opos_h`, in try/catch; without storage there is no guard, so no reload), calls `healReload` after 0.5 s. `healReload` fetches `/` and reloads only if the server answers 200, otherwise it says "Still not reachable. Try again in a moment." and the page stays where it is. A page that reloaded into a stopped server would be a browser error page, and the phone has no keyboard. The same function is the Reload button and the wait after "Restart helper" (every 2 s until the server answers; the old fixed 6 s landed on a refused connection whenever the server was late).
- Do not remove the 10 s guard or the fetch: together they keep this from becoming a reload loop. `/` is served with `Cache-Control: no-store`, so the probe is never answered from the cache.
- With a persisted token (`--token-file`, W1-03) a plain restart of the server never reaches the 403 branch: the open page just goes on.

## Taps and focus survive the poll (W1-09)

Where: `js-4.js` (`sync`), `js-5.js` (helper box, contacts, threads), `web.py` (the Messages thread line between `js-5.js` and `js-6.js`), `js-7.js` (features, events), `phone-a.js` (the helper enhancer, `put`), `js-8.js` (`seen`, `stateKey`: the poll draws only a changed state).

- `render` ran every poll and rebuilt the contacts, the threads, the open thread, the helper box (and its Why? button), the feature rows and the event list with `innerHTML`. A button replaced between the press and the release gets no click (about 3 % of taps at the 3 s poll, about 10 % at the 1 s poll of a call), and keyboard focus fell to `<body>` within one poll.
- `sync(el, html, wire)` keeps the markup it last wrote in `el._k` and does nothing when the new markup is the same string, so a node lives as long as its content does. `wire` runs only after a write: the buttons are wired in the callback, not by a second `querySelectorAll` pass on every poll. `#paused-list` already did this with its own key.
- The helper box is text plus a Why? button that `phone-a.js` then moves into the action row and wraps in Technical details. `js-5.js` rebuilds it only when `JSON.stringify(s.helper)` changed (`h._k`); the enhancer skips a box that already holds its `details` element, because its `button` branch would otherwise take Reset or Restart helper for Why?.
- The sync guards alone left about 57 DOM records per idle poll (the rest were writes of a value to a node that already had it, in files W1-09 does not own). The root fix is a11y-cost-05 (a): `pollTick` draws a state poll only if the state differs from what was last drawn. `render` (the outermost wrapper in `js-8.js`) keeps `seen`, the key of the last good state, and clears it first thing, so an exception, an error render or the stale banner (`render({error:'offline'})`) always forces the next good answer to be drawn. The key is `JSON.stringify(state)` with `lean` (the memory figures, read from the system on every call) replaced by a constant, plus `navigator.onLine`, which the status bar hook reads. Anything the page did itself between two polls is put back as before by clearing `seen` on `load` (scripts that load after `js-8.js` may have missed the first draw), `click`, `keydown` and `input` (a notice the page wrote, a field the person edited, a screen it opened): the next poll draws once more, exactly as it did on every poll before. A simulation tick goes through `api()` and is always drawn, since the simulated time moves. The two 15 s clock timers (`tick` for the status bar, `lockTick` for the lock screen) write their text only when it differs (`put` in `phone-a.js`); every write replaced a text node, which dirtied the layout, and with the status bar's `MutationObserver` re-ran `barPaint` four times a minute for nothing.
- Measured (headless Edge 390x844, 30 s idle, the demo's 3 s poll, Home and lock screen; the same script against the 11c39ba tree, the W1-09 commit, and this tree): DOM mutation records per idle poll 69.6, 57.6, __REC__; layouts per minute 24 (Home) and 24 (lock), 24 and 28, __LAYH__ and __LAYL__. The plan's targets (under 5 of each) are met. What is still written once a minute is the clock.
