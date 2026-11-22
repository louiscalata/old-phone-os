<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# GPU Link

GPU Link is a proposed USB link for a phone to use a language model on a PC GPU and receive streamed text. Stage 1 is owner mode only. Phone execution of this design is NOT_RUN. The feature is off by default. `OPOS_GPU_LINK=1` turns on the link service; `OPOS_APPS` must name `ai` for the page.

## 1. Purpose

A phone hands a question to a language model that runs on a PC's NVIDIA GPU, over a USB cable, and receives the answer as streamed text. The model file and the conversation belong to the phone. Pulling the cable pauses the answer. Plugging it back in continues it.

Terms:

- **Phone**: runs the OPOS server (Python, standard library only), on the OPOS OS image or inside Termux on stock Android.
- **Link service**: the new part of the OPOS server that a PC talks to.
- **PC**: a Windows 10 or 11 computer with an NVIDIA GPU.
- **Companion**: a per-user Windows app that the PC's owner installed. It never asks for administrator rights.
- **Engine**: llama.cpp's `llama-server`, started by the companion as a child process.

## 2. Two stages

- **Stage 1, owner mode.** For the owner's own PCs. This is planned for 2026-12-10: one model, one piece of work at a time, one enrolled PC. It adds no listener that another device can reach.
- **Stage 2, guest mode.** For a PC with no earlier relationship to the phone. Not planned for the demo. Section 13 lists what it must meet, and it needs its own review before any code.

## 3. Not goals

- Starting or installing anything on a PC whose owner has not installed the companion.
- Any link that is not the USB cable.
- Hiding the conversation from the PC that runs the model. That PC processes the text.
- Defending against software running as the same Windows user as the companion, against root on the phone, or against someone who has an unlocked phone or an unlocked PC in their hands. Section 15 states these as accepted risks.

## 4. Stage 1 transport

- The link service listens on the phone's loopback only, on its own port. It serves the link messages and nothing else. The phone's page server stays separate.
- The PC reaches that port through a channel the owner already has for their own PC: `adb forward` on Android, or an SSH local forward on the OPOS OS image. Every existing PC-to-phone channel in the repository already works this way.
- The companion creates the forward itself, on a port that adb or SSH chose, and checks that the process listening on that port is the adb or SSH program it started. If not, it stops.
- That developer access can do far more than GPU Link: it can run commands on the phone. Owner mode is therefore only for PCs the owner already trusts with that access. It is not a way to use someone else's PC.
- Every TCP connection is made from the PC. The PC accepts no inbound connection from the phone.
- The owner chooses the physical path: the phone, the cable, the port and the PC. A device placed between the phone and the PC on that path is outside the scope of stage 1.

## 5. Enrolling a PC

Enrolling gives one PC and the phone a shared 32-byte key `K`. In stage 1 the key travels only through the owner's developer access, on the standard input of one fixed command. It never travels through a forwarded port, a command argument, an environment variable or a log.

1. The companion notices the phone and shows its permission window (section 7). Nothing is sent until the PC owner chooses Allow.
2. The companion creates `K` (32 random bytes) and delivers one enrolment message, `{pc_label, K}`, to the link service's loopback port on the phone. On Android the command is `adb shell` running the phone's `nc` to that port. On the OPOS OS image it is `ssh`, with strict host-key checking, running the same. The message is at most 1 KB and travels on the command's standard input. It is one HTTP request, `POST /v1/enrol`, with `Content-Type: application/json`. The reply says only whether the phone is now waiting for its owner, and carries no secret and no code. If the command is not available on the phone, enrolling is not possible. There is no second path.
3. Both sides compute a code: SHA-256 over a fixed prefix and the length-framed values `pc_label` and `K`, read as a number modulo 1,000,000 and shown as six digits with leading zeros. Both also compute `pc_id`, the first 8 bytes of SHA-256(`K`).
4. The companion's window shows "Confirm on the phone: 482 913". The phone shows "Use the GPU of <pc_label>? Code 482 913. This PC will see what you ask in AI mode and will keep a copy of the model file." with Allow and Not now.
5. On Allow the phone stores the label and `K` with mode 0600, in one step. On Not now, or after 60 seconds, it forgets `K`. Only one enrolment can be pending. A pending key is accepted on no route.
6. The companion learns the outcome from its first signed request (section 6). That request is answered only if the phone stored `K`. Until then it gets status 401.
7. The companion stores `K` for the current Windows user, protected with DPAPI.

Why this shape:

- No message returns a secret from the phone, and `K` never crosses a port that another program on the PC could have opened.
- What protects the delivery differs by route. SSH checks the phone's host key against the one the owner accepted earlier. adb does not identify the phone to the PC: it only lets the phone accept or refuse the PC. On Android the step therefore relies on the owner's own cable and port, as section 4 says.
- The code lets the person see that the request on the phone came from the window in front of them. A different program that enrols a key of its own shows a different code.
- `pc_label` is limited to 24 characters from letters, digits, space and hyphen, on both screens. It begins and ends with a letter or digit.
- After three denied or expired requests, enrolling closes until the owner reopens it on the phone. With the PIN lock on, the phone takes no answer from the lock screen.
- The phone keeps one enrolled PC. Enrolling another means removing the first. Removal takes effect on the next request. The companion can forget a phone.

Stage 2 cannot use this, because a stranger's PC has no developer access to the phone. Section 13 says what it needs instead.

## 6. Signed requests

Every request after enrolling is signed. The key itself is never sent again.

- After enrolling, `GET /v1/hello` is the only unsigned request. The enrolment message of section 5 is unsigned too: no key is shared yet. The reply to hello is `{proto, label, epoch}`. `epoch` is 16 random bytes that change when the link service restarts. The companion refuses a `proto` it does not know.
- Headers on every other request: `X-Link-Pc` (the `pc_id`), `X-Link-Nonce` (12 random bytes) and `X-Link-Mac`.
- `X-Link-Mac` = HMAC-SHA256(`K`, "opos-link-v1" ‖ `epoch` ‖ method ‖ path with query ‖ `Range` header or empty ‖ nonce ‖ SHA-256(body)), with each value length-framed.
- The phone compares in constant time. It checks and records the nonce in one step, under a lock, before the request has any effect. A nonce seen before in the current `epoch` is refused.
- The phone records at most 4,096 nonces per PC. When that is full it starts a new `epoch` and refuses everything signed for the old one. The companion says hello again.
- Every reply to a request that passed the check carries `X-Link-Mac` = HMAC-SHA256(`K`, "opos-link-v1-reply" ‖ request nonce ‖ status ‖ range headers or empty ‖ SHA-256(body)). A request that fails the check gets status 401 with an empty body and no `X-Link-Mac`, and has no other effect. The companion drops any other reply without a valid value. For a model file the body hash is left out, and the bytes are checked against the companion's own list (section 10).
- A retry is a new request with a new nonce.
- No clocks are compared. Time limits use each side's own monotonic clock.

The companion's HTTP client uses no proxy, follows no redirect, accepts no compressed reply, and limits header size, body size and time for every request.

## 7. Permission on the PC

- The companion shows its window each time a phone is noticed and there is no grant for it.
- A grant belongs to one enrolled phone (its `K`). It lasts from Allow until one of these: the phone has been unplugged for 2 minutes by the PC's monotonic clock, the user chooses Stop in the tray menu, the companion exits, or the user signs out. A replug inside those 2 minutes does not ask again.
- Stop cancels the current answer, any download and any waiting request, refuses further work, stops the engine and frees GPU memory.
- The default button is Not now. Escape means Not now. The window ignores keyboard and mouse input for its first 700 ms, so a key press meant for another app cannot grant access.
- The only text from the phone that the window shows is the label, with the same limits as `pc_label`.
- The window is the companion's own. It does not copy the look, wording or placement of the Windows permission prompt.

## 8. Messages

Bodies are JSON in UTF-8, written without `\u` escapes, with a size limit per message. A request that has not arrived completely within 10 seconds is closed.

| Request | Limit | Purpose |
| --- | --- | --- |
| `POST /v1/status`, once a second | 2 KB | The PC's state: `idle`, `copying`, `loading`, `ready`, `busy`, `stopping` or `error`; the lease it is working on, or none; copy progress; GPU name, memory and driver; an error code. The reply can carry `cancel` with a lease. The companion acts on it only if that is the lease it holds. This is also the liveness signal. |
| `GET /v1/work?wait=20` | reply 64 KB | Long poll. Returns 204, or one piece of work: `{lease, model_sha256, question, partial}`. Generation settings are fixed in the companion's model list. One piece of work per phone at a time. A PC that asks again while it holds a lease gets that same work again. |
| `POST /v1/runs/<lease>/text`, at most 4 a second | 8 KB | `{offset, text, done, finish}`. Reply `{acked}`. |
| `GET /v1/models/<sha256>` with `Range` | file | The bytes of one model file. |

A question is one message of at most 4 KB. Stage 1 has no multi-turn history. An answer is at most 12 KB.

Status, a work request, text with the same offset and bytes, and model ranges may be sent again after a failure, each time as a new signed request. Nothing else is retried.

## Wire format

### Values

- All binary values travel as lowercase hexadecimal text, in headers and in JSON. Uppercase is refused.
- `frame(v)` is the length of `v` as 4 bytes, big-endian, followed by `v`.
- Sizes in bytes: key 32, nonce 12, epoch 16, lease 16, `pc_id` 8.
- A label (the PC's and the phone's) matches `[A-Za-z0-9](?:[A-Za-z0-9 -]{0,22}[A-Za-z0-9])?` in full: 1 to 24 characters, letters, digits, space and hyphen, beginning and ending with a letter or digit.
- `code(pc_label, K)`: SHA-256 over `frame("opos-link-v1-code") ‖ frame(pc_label as UTF-8) ‖ frame(K)`, read as a big-endian number, modulo 1,000,000, written as six digits with leading zeros. Showing it as "482 913" is a display matter.
- `pc_id(K)`: the first 8 bytes of SHA-256(`K`), as 16 hex characters.

### Signatures

Request headers: `X-Link-Pc` (16 hex), `X-Link-Nonce` (24 hex), `X-Link-Mac` (64 hex).

`request_mac` = HMAC-SHA256(`K`, `frame("opos-link-v1") ‖ frame(epoch) ‖ frame(method) ‖ frame(target) ‖ frame(range) ‖ frame(nonce) ‖ frame(SHA-256(body))`)

- `method` is `GET` or `POST` in ASCII.
- `target` is the request target exactly as it stands on the request line, path and query, ASCII.
- `range` is the value of the `Range` header exactly as sent, or empty when there is none.
- `body` is the raw request body, which is empty for `GET`.

`reply_mac` = HMAC-SHA256(`K`, `frame("opos-link-v1-reply") ‖ frame(request nonce) ‖ frame(status) ‖ frame(content range) ‖ frame(body hash)`)

- `status` is the status code in ASCII decimal, such as `200`.
- `content range` is the value of the reply's `Content-Range` header exactly as sent, or empty.
- `body hash` is SHA-256 of the raw reply body. For a model file reply (status 200 or 206 on a models target) it is the empty value, so its frame is four zero bytes.

Every reply to a request that passed the signature check carries `X-Link-Mac`, whatever its status. A request that fails the check gets status 401 with an empty body and no `X-Link-Mac`, and has no other effect.

Comparison uses constant-time `hmac.compare_digest`. A header that is not lowercase hex of the exact length fails the check.

The method, the target, the range value and the content range are ASCII. Each signed value has one way to be written. Before anything is computed, both sides refuse a key that is not 32 bytes, an epoch that is not 16, a nonce that is not 12, a method other than `GET` or `POST`, text outside ASCII, and a status that is not a whole number from 100 to 599.

### Targets

Exactly these, matched in full, case-sensitive. Anything else is refused.

| Target | Method | Signed |
| --- | --- | --- |
| `/v1/hello` | GET | no |
| `/v1/enrol` | POST | no |
| `/v1/status` | POST | yes |
| `/v1/work?wait=N`, N from 0 to 20 with no leading zero | GET | yes |
| `/v1/runs/<lease, 32 hex>/text` | POST | yes |
| `/v1/models/<sha256, 64 hex>` | GET | yes |

### Bodies

JSON objects in UTF-8. A sender writes compact JSON with no `\u` escapes (`ensure_ascii=False`, separators `,` and `:`). A receiver checks the size of the raw bytes first, then parses, and refuses: anything that is not an object, a duplicate key, an unknown key, a missing key, a wrong type (a boolean is not a number), `NaN` and `Infinity`, and a body nested more than 4 levels deep (the outer object is level 1; the deepest valid message has 2). The depth is counted on the raw bytes, so the limit does not depend on a parser's own recursion limit.

Numbers have one written form. A whole number is digits only, with no sign, fraction, exponent or leading zero, and is at most 9,007,199,254,740,991 (2^53 - 1). `offset` is at most 12,288. `tps` is a whole number, or digits with a fraction of one to three digits and no exponent, from 0 to 100,000. Any other number token in a body is refused, also where a number is not expected.

A sender refuses to build a message that breaks these rules. In a work reply the question is at most 4,096 bytes and the partial text at most 12,288 bytes, and neither holds a character below U+0020 other than newline and tab. No sender writes a `\u` escape.

| Message | Raw limit | Keys |
| --- | --- | --- |
| hello reply | | `proto` (the number 1), `label`, `epoch` (32 hex) |
| enrol request | 1,024 | `pc_label`, `key` (64 hex) |
| enrol reply | | `state`: `pending`, `busy`, `enrolled` or `closed` |
| status request | 2,048 | `state` (`idle`, `copying`, `loading`, `ready`, `busy`, `stopping`, `error`), `lease` (32 hex or null), `copied` and `total` (whole numbers from 0 or null), `gpu` (null, or an object with `name` up to 64 characters, `memory_mb` whole number, `driver` up to 32 characters), `tokens` (whole number or null), `tps` (number from 0 to 100,000 or null), `error` (null or `[a-z0-9_-]{1,32}`) |
| status reply | | `cancel` (32 hex or null) |
| work reply | 65,536 | `lease`, `model_sha256`, `question`, `partial` |
| text request | 8,192 | `offset` (whole number from 0), `text`, `done` (boolean), `finish` (null, `stop` or `length`; not null exactly when `done` is true) |
| text reply | | `acked` on 200, `stored` on 409 and 413 |
| error reply | | `error`: `lease`, `state`, `text`, `too_long`, `rate`, `range`, `model` or `request` |

`gpu.name` and `gpu.driver` are shown on the phone. They may hold printable ASCII only (0x20 to 0x7E).

`tokens` and `tps` in the status request exist for the tokens-per-second display on the phone. They are numbers and change nothing else.

In a status request, `error` is not null exactly when `state` is `error`, and `copied` and `total` are either both null or both numbers with `copied` not above `total`. Any other combination is refused.

Enrol reply statuses: 202 `pending`; 409 `busy` (another request is waiting) or `enrolled` (a PC is already enrolled); 403 `closed` (three requests were denied or expired); 400, 413 and 429 with an error body. The same label and key sent again while that request is pending gets 202 again and does not restart its 60 seconds.

### Answer text rule

Text is accepted when its bytes are well-formed UTF-8 (no surrogates, no overlong forms, no cut character) and hold no character of Unicode category Cc other than U+000A and U+0009. Anything else is refused whole. Carriage return is refused.

### Ranges

`Range` must match `bytes=<start>-<end>` in full, each a decimal number without leading zeros, with start ≤ end < total. One range only. The reply's `Content-Range` is `bytes <start>-<end>/<total>`.

The shared example messages for both sides are planned for `tests/fixtures/gpu_link/examples.json`. That file arrives with a later change.

## 9. Answer text, pause and resume

**Lease.** Each time the phone hands out work it creates a new `lease` of 16 random bytes and ties it to that PC. Text is accepted only when the lease is the current one for that answer, the signature is from the PC it was given to, and the answer's state is `loading` or `answering`. The phone ends the lease before it marks an answer paused, cancelled or done. A lease that ended with the final post is remembered for one purpose: an exact repeat of that post gets the same reply.

**Bytes.**

- `offset` is the number of bytes of the answer the phone has stored. The phone appends only when `offset` equals that number.
- The phone stores the text exactly as received. A post that contains invalid UTF-8, a partial character at its end, or a control character other than newline and tab is refused whole, with no change.
- A post that repeats bytes the phone already has is acknowledged only if the bytes are identical. Otherwise it returns 409 with the stored length.
- The check and the append happen under one lock. The phone acknowledges only after the text and the answer's state are written and flushed to storage.
- After the final post, with `done`, the phone accepts nothing more for that lease except an exact repeat of that post. The repeat gets the same reply and changes nothing.
- The companion keeps the text of the current run in memory until the run ends, so it can send again from any earlier offset.
- The phone shows answers as plain text only.

**States** of one answer on the phone: `queued`, `waiting`, `loading`, `answering`, `paused`, `done`, `failed`, `cancelled`.

| Event | Phone | PC |
| --- | --- | --- |
| Cable pulled while answering | No status for 2.5 seconds means the link is down. The lease is cancelled, then `answering` becomes `paused`. Text is kept. | Two failed status posts mean the link is down. Generation stops. The model stays loaded while the grant lasts. |
| Cable back while the grant lasts | Hands out the same answer under a new lease, with `partial` set to the stored text. | Applies the model's chat template to the question, appends `partial`, and continues from there. Sends only new text, starting at `offset` = length of `partial`. |
| Cable back after the grant ended | Same. | Shows the permission window first, loads the model, then the same. |
| The question and `partial` no longer fit the context | Marks the answer `done` with the reason `length`. | Reports `finish: length` without generating. |
| OPOS restarts | Reads answers from storage. An answer that was `loading` or `answering` becomes `paused`. | Its lease is refused. It asks for work again. |

What is promised: after a resume, the text on the phone begins with exactly the bytes it had before, and continues from there. What is not promised: that the result equals what an uninterrupted run would have produced. The engine continues from a text prefix.

Resume on a different PC is not part of stage 1.

## 10. Model files

- The companion carries a built-in list of accepted model files. In stage 1 the list has one entry: the SHA-256, the exact size in bytes, the name and the engine settings. The phone's own description of a file is not trusted.
- The companion refuses any other hash before it downloads a byte.
- It accepts a 200 reply only for the whole file from byte 0, and a 206 reply only when the range and the total size are exactly what it asked for and what its list says. It stops at the listed size. It refuses a 200 reply to a request that named a range.
- The phone answers a valid `Range` with 206, also when the range covers the whole file, and a request without `Range` with 200.
- It writes to one temporary file, hashes the bytes, deletes the file on a mismatch, and renames it to `<sha256>.gguf` on a match. A temporary file left behind is removed at start-up and on Stop.
- It refuses to start a download that would not fit in free disk space.
- Reason: llama.cpp published eight security advisories in the last 24 months, and five are triggered by a crafted model file. A hash supplied by the phone proves nothing. A hash the companion already knows does.
- The phone never receives a file or code from the PC.

## 11. Engine rules on the PC

- A pinned llama.cpp build (`b12359`, tagged v1.0.0 on 2026-11-13) with pinned archive hashes. It is newer than every fix in the advisories above.
- `--host 127.0.0.1`, a port chosen by the companion, `--no-webui`, `--offline`, one slot, context 4,096, reasoning off.
- A fresh random API key for each start, in a file that only the current user can read, deleted when the engine has started.
- Before it sends a key or a prompt, the companion checks that the process listening on the port is the child it started. If not, it stops.
- An explicit `--device` taken from `--list-devices`. When CUDA cannot start, the engine falls back to the CPU without an error, and that must never be reported as GPU work.
- Stage 1 uses the CUDA build only. If it does not offer the NVIDIA GPU, both screens say why.
- The engine runs in a job object that ends it when the companion ends.
- Neither side writes question or answer text to a log. The companion keeps no conversation on disk and switches engine logging off.

## 12. Limits on the phone

- At most 4 open link connections, one pending long poll and one piece of work per PC.
- A request whose headers do not arrive within 5 seconds is closed.
- One enrolled PC and at most 50 stored answers. The oldest finished answer is removed first.
- Hello requests and enrolment attempts are rate limited without regard to who sends them.
- The `Host` header must be `127.0.0.1` or `localhost`, with any port or none. An enrolment message must say `Content-Type: application/json`. A page open in a browser on the PC can therefore not send one.
- Every reply closes its connection. One request travels on one connection.

## 13. Stage 2 requirements (guest mode)

Guest mode must meet all of these before it is built, and gets its own review:

1. Pairing that does not rely on a verified channel: each side commits to a random value before it sees the other's, and the code covers the key of an encrypted channel, so a relay in the middle cannot learn the key or show a matching code.
2. A way to create that channel's key on each phone image. Python's standard library can serve TLS but cannot create a key. Whether the images carry the `openssl` tool is NOT_RUN.
3. A listener that accepts only packets that arrive on the USB interface from the address at the other end of the cable. Binding to the USB address is not enough when the PC routes or bridges.
4. A stated answer to what the phone reveals before pairing. A long-lived certificate is an identifier.
5. A tested TLS client on Windows 10, which has no TLS 1.3, with an explicit check of the pinned certificate and no general bypass of certificate checks.
6. Limits on unauthenticated connections strong enough that an unpaired PC cannot keep the phone busy.
7. On the OPOS OS image: proof that a PC that has never seen the phone gets a driver and a working address with no administrator step. NOT_RUN.

## 14. What each side can do in stage 1

| Actor | Can | Cannot |
| --- | --- | --- |
| A PC with the owner's developer access | Everything that access allows, which is more than GPU Link | Not limited by this design |
| The companion on an enrolled PC | Read the model file; read the questions asked in AI mode; append text to the answer it holds a lease for; stop | Change stored text; write to another answer; act after its key is removed |
| The phone | Use the GPU while a grant lasts; make the PC keep a copy of the one model file the companion lists | Supply code; make the engine load an unlisted file; read PC files; put its own wording in the permission window beyond the label |
| Another program on the PC without the owner's developer access | Reach a forwarded port | Sign a request; enrol a key; use the engine without its key; read `K` if it runs as a different user |

## 15. Risks accepted

- An enrolled PC reads the conversation and can return wrong text. The phone names the PC that answered, and says so before enrolling.
- Software running as the same Windows user can read `K`, use the owner's developer access and read the engine's memory.
- On Android, a device placed between the phone and the PC on the cable could read or change the enrolment, because adb does not identify the phone to the PC. The owner uses their own cable and port.
- Someone with an unlocked phone can approve a PC, and the PC then appears in the list. Someone with an unlocked PC can grant access or copy `K`.
- `K` does not expire. It ends when the owner removes the PC on the phone. Removal is checked on the next request, not in the middle of one.
- A model copy of about 2 GB stays on the PC until the user removes it from the tray menu or uninstalls. The window and the phone both say so.
- Prompts may remain in the PC's memory, page file or crash dumps.
- A flaw in the engine reachable through prompt text. Reduced by the pinned build, loopback, the API key and the job object.

## 16. Open points

1. Whether the chosen phone has `nc` for the enrolment command: NOT_RUN. To check in the first hour. Without it, stage 1 cannot enrol on that phone.
2. Settled: the reply to the enrolment message says whether the phone is waiting for its owner, is busy with another request, already has a PC, or has closed enrolling. A command that fails gives no such reply.
3. How the phone notices the cable inside Termux without root. Stage 1 uses the status timeout only.
4. Whether listening on 127.0.0.1 shows the Windows firewall prompt to a standard user: NOT_RUN. Believed not. To test.
5. The owner's yes to guest mode, after the demo.

## 17. Tests before any device run

- A fake PC in the phone's unit tests and a fake phone in the companion's tests, both driven from one shared file of example messages, signatures included.
- Enrolment: a second enrolment while one is pending, expiry, three denials, a signed request made with a pending key, a second PC while one is enrolled, a message over 1 KB.
- Signatures: wrong key, replayed nonce, a retry with a reused nonce, two copies of one request at the same moment, changed body, changed path, changed range, a reply without a signature, a request signed for an old epoch.
- Text: wrong lease, cancelled lease, wrong PC, wrong offset, repeat with different bytes, invalid UTF-8, a cut inside a character, control characters, a 12 KB overflow, a cancel that names an old lease, a restart between a post and its acknowledgement, a lost work reply followed by a new work request, a repeat of the final post, a different post after the final one.
- Seam: cut the answer at random byte positions, resume, and check the promise in section 9.
- Model file: one changed byte, a wrong size, a reply that ignores the range, a 200 reply to a range request, an unlisted hash, a full disk, a download interrupted by Stop.
- Limits: slow headers, a slow body, too many connections, enrolment floods.
