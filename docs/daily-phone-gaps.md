<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Daily phone gaps

What main already has, set against a short list of everyday phone jobs. Read from the source. Nothing in this note was run on a Surface Duo. Every on-device check is **NOT_RUN**.

Page size depends on the source revision and enabled features. The November 12 development record in [ROADMAP.md](../ROADMAP.md) reports 101,895 bytes with volume and the existing switches enabled; this is a recorded local result, not a phone-performance measurement. Use [ui-byte-ledger.md](ui-byte-ledger.md) to measure the current checkout against the 102,000-byte gate.

Historical figures recorded in `5905ea3a` on November 10: `web.page()` was 99,973 bytes, or 99,988 bytes with `OPOS_SPAN=675,42`. The served home page was 100,202 bytes with no switch, 101,249 bytes with the six switches, and 101,317 bytes with `OPOS_APPS=clock,contacts,notes,calc` added. These figures describe that revision only.

## Already on main

The Duo image drop-in `packaging/duo-os/opos-apps.conf` names `clock,contacts,notes,calc,calls`. The demo and the postmarketOS build do not set `OPOS_APPS`. Phone, Messages, and Lock are part of the main page, not that list.

| Piece | What the source does |
|---|---|
| Clock and alarm | `/clock`: alarms, one timer, a stopwatch, and a bedside clock. The timer is one countdown in whole minutes and is stored with the alarms. The stopwatch runs in the page and is cleared when the page is closed. A tone exists in `alarm_sound.py`. Hearing it on a phone is NOT_RUN. |
| Calculator | `/calc`. Arithmetic runs in the page. Nothing is stored. |
| Contacts | `/contacts`. A local book, search, and vCard import and export. Call and Text on that page say the call is practice and that nothing was sent. |
| Notes | `/notes`. Plain text files on the phone. |
| Phone | The front page dials and hangs up through the in-memory modem. Messages are threads in the same page. `src/opos/mm_modem.py` is not wired in. The Duo image carries ModemManager through ofono2mm. Calls and texts on that image are still the simulated modem. |
| Music | No player, no library, no page. |
| Memos | No voice memo. Written notes are the Notes app. |
| Lock | A lock screen with the time, the battery line, and Call. An optional PIN (`OPOS_LOCK=1`) is enforced only when that variable is set. |
| Battery | Home, the lock screen, and the battery panel already show level, charge, and time left. This is not a gap. |

## Gaps

A gap fits the ethos when it is a plain local tool, keeps its own data on the phone, and does not add an account. The policy preserves simulated calls, messages and alarms; this does not establish working phone service. Anything that needs a new Debian package or a new service is listed here and is not built.

| Gap | Fits | New package or service | This round |
|---|---|---|---|
| Messages and SMS through oFono | Yes. Calls and messages are the essential work. | Yes. oFono is not the dialer. The image already has ModemManager, and the adapter is not connected. Using either one is a service change. | Listed only. |
| Call log | Yes. A local list of calls recorded at the modem boundary. | No. | Implemented at `/calls`, enabled by `OPOS_APPS=calls`. Up to 200 local entries, incoming/outgoing/missed, contact name at start, connected duration, Practice/Real labels, callback through Phone, confirmed clear, and API missed count. Current modem is simulated. Every on-device check is NOT_RUN. |
| Flashlight | A pocket light fits. | The LED is a kernel path, not a package in this tree, and writing it is not approved. A white screen would need no package and is a poor stand-in. | Listed only. |
| Timer and stopwatch | Already in Clock. Not a gap. | No. | Do not add a second one. |
| Battery readout | Already on the phone. Not a gap. | No. | None. |
| Storage readout | Yes. One factual line for free space, from the standard library. | No. | Not this round. The call log is the higher daily need. |
| Do Not Disturb | Only if calls, messages, and alarms still come through. A switch that hides optional noise can stay local. | No. | Not this round. |
| Unit converter | Yes. The same kind of tool as the calculator. | No. | Build. Second, after the call log. |
| Music | A local player is a weaker fit than calls and notes. | Files the browser can already play need no package. Further codecs would be a new package. | Listed only. |
| Voice memos | Notes already keep text. | No Debian package for a browser recording. The page currently refuses the microphone, so this is a permission change. | Listed only. |

Calendar is not in the examples above. A local calendar would fit and would need no new package. It is not one of the two builds.
