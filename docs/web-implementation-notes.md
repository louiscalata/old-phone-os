<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Web implementation notes

These explanatory comments are retained from the stylesheets. They are kept outside the served page to keep the served payload within the original byte budget; CSS declarations, layout behavior and licensing notices remain unchanged.

## jaryo-layout.css

```css
/* The jump row precedes the list in DOM order. Keep both full-width on a
   foldable, with matching two-column gaps so no jump button crosses the hinge. */
```

## jaryo-layout.css

```css
/* JRY30 landscape (docs/jaryo.md 4.1, 4.3, 4.6 and section 13). Rules only: ids, DOM order and Tab order are unchanged,
   no order:, no transition or animation, colour only through currentColor. Slab keeps its phone column and the foldable
   keeps its UXP21 hinge grid (phone-b.css), so both are excluded. Outside the two >=840px blocks JRY30 adds only the lock
   button margin below; phone-a.css owns the page's single max-width:839px block (UXP23). Home's four landscape tracks are
   JRY04 (jaryo.css) and the slide-out sticky #controls row is UXP21 (phone-b.css). */
```

## jaryo-layout.css

```css
/* Lock buttons fit the lock's width at every size. The base button rule (web.py) gives every button margin-right:8px and
   UXP23 makes #lock buttons width:min(100%,360px); centred by the lock's flex column, that 368px margin box sits at
   -4px to 356px in a 360px viewport (measured in the round-2 gate), so each button lost 4px at both edges. The 4px top
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
   JRY08 letter row on the Foldable (UXP21 hinge grid, phone-b.css): jaryo-layout.js places #jaryo-letters as a #phone child just
   before #apps-list. DOM order is reading order, so a full-width item may not follow a pane item (test_uxp21_profiles order check):
   the row spans both columns like the list, and its content box is padded down to the left pane so no letter button sits in the
   24px centre gutter. With real viewport segments it keeps to column 1, like #apps-list. Below 840px #phone is one column.
   4.3 Shade at >= 840px (JRY30): a sidebar column from the left edge; the page stays visible beside it. The base transition
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
/* phone-a.css: phone-shell hook point (UXP00) */
```

## phone-b.css header

```css
/* phone-b.css: phone-shell hook point (UXP00) */
```
