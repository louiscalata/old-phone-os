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
