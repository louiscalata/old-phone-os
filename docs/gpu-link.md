<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# GPU Link

GPU Link lets the phone offer work and a model file to a PC over a USB forward and store streamed answer text. The phone service and AI mode page are implemented. Stage 1 is owner mode only. The Windows companion described here is a requirement for a separate implementation. It is not supplied by this repository. Its permission window, forwarding, model download and engine rules below are requirements, not recorded PC results. Physical-phone AI mode and GPU Link checks are **NOT_RUN**. The feature is off by default. `OPOS_GPU_LINK=1` turns on the link service; `OPOS_APPS` must name `ai` for the page.

## 1. Purpose

The intended companion runs a language model on a PC's NVIDIA GPU and sends answer text to the phone. The phone stores the question and answer and serves the configured model file. Loss of status pauses running GPU work. A later work request can resume the stored prefix under a new lease. This does not establish that a cable or GPU was used in a device run.

Terms:

- **Phone**: runs the OPOS server (Python, standard library only), on the OPOS OS image or inside Termux on stock Android.
- **Link service**: the new part of the OPOS server that a PC talks to.
- **PC**: a Windows 10 or 11 computer with an NVIDIA GPU.
- **Companion**: a per-user Windows app that the PC's owner installed. It never asks for administrator rights.
- **Engine**: llama.cpp's `llama-server`, started by the companion as a child process.

## 2. Two stages

- **Stage 1, owner mode.** For the owner's own PCs. The phone service supports one model, one GPU answer in progress and one enrolled PC. It binds to loopback. The developer forward gives the PC access to that listener.
- **Stage 2, guest mode.** For a PC with no earlier relationship to the phone. Not planned for the demo. Section 13 lists what it must meet, and it needs its own review before any code.

## 3. Not goals

- Starting or installing anything on a PC whose owner has not installed the companion.
- Any link that is not the USB cable.
- Hiding the conversation from the PC that runs the model. That PC processes the text.
- Defending against software running as the same Windows user as the companion, against root on the phone, or against someone who has an unlocked phone or an unlocked PC in their hands. Section 15 states these as accepted risks.

## 4. Stage 1 transport

- The link service listens on `127.0.0.1` on the phone, at port `8767` unless `OPOS_GPU_LINK_PORT` selects another port. The companion forwards to that phone port. It serves only link messages. The phone's page server stays separate.
- The computer's local forwarded port is the companion's choice. It must not be `8767` on a computer running the Inference monitor there. That monitor port is documented in `packaging/duo-os/README.md`, under Inference page. The phone port and the computer port need not match.
- The PC reaches that port through a channel the owner already has for their own PC: `adb forward` on Android, or an SSH local forward on the OPOS OS image. These are transport requirements for the companion; the listener does not check which transport reached it.
- The companion creates the forward itself, on a port that adb or SSH chose, and checks that the process listening on that port is the adb or SSH program it started. If not, it stops.
- That developer access can do far more than GPU Link: it can run commands on the phone. Owner mode is therefore only for PCs the owner already trusts with that access. It is not a way to use someone else's PC.
- Every TCP connection is made from the PC. The PC accepts no inbound connection from the phone.
- The owner chooses the physical path: the phone, the cable, the port and the PC. A device placed between the phone and the PC on that path is outside the scope of stage 1.

## 5. Enrolling a PC

Enrolling gives one PC and the phone a shared 32-byte key `K`. The companion must deliver the key through the owner's developer access, on the standard input of the remote command described in step 2. It must not put the key in a forwarded request from the PC, a command argument, an environment variable or a log. The phone accepts enrolment on the same loopback listener as other link requests. It does not enforce that delivery path. Any program that can reach it can start an enrolment; the owner must compare the code on both screens before allowing it (sections 12 and 14).

1. The companion notices the phone and shows its permission window (section 7). Nothing is sent until the PC owner chooses Allow.
2. The companion creates `K` (32 random bytes) and delivers one enrolment message, `{pc_label, key}` (`key` is `K` in lowercase hex), to the link service's loopback port on the phone. On Android the command is `adb shell` running the phone's `nc` to that port. On the OPOS OS image it is `ssh`, with strict host-key checking, running the same. The JSON body is at most 1,024 bytes and travels on the command's standard input. It is one HTTP request in the form below. The reply says only whether the phone is now waiting for its owner, and carries no secret and no code. If the command is not available on the phone, enrolling is not possible. This is the required companion delivery path; the listener cannot distinguish it from a forwarded request.
3. Both sides compute a code: SHA-256 over a fixed prefix and the length-framed values `pc_label` and `K`, read as a number modulo 1,000,000 and shown as six digits with leading zeros. Both also compute `pc_id`, the first 8 bytes of SHA-256(`K`).
4. The companion's window shows "Confirm on the phone: 482 913". The phone shows "Use the GPU of <pc_label>? Code 482 913. This PC will see what you ask in AI mode and will keep a copy of the model file." with Allow and Not now.
5. On Allow the phone stores the label and `K` with mode 0600, in one step. On Not now, or after 60 seconds, it forgets `K`. Only one enrolment can be pending. A pending key is accepted on no route.
6. Allow changes the epoch. The first signed request after Allow, if signed with the epoch read before Allow, gets an empty unsigned 401 even though the key is stored. The companion then says hello again, compares the epoch and repeats the request with that epoch and a new nonce. A 401 is not proof that Allow failed. Section 6 defines recovery and the pending-key case.
7. The companion stores `K` for the current Windows user, protected with DPAPI.

### Enrolment HTTP bytes

Build the command's standard input as these ASCII header lines, an empty line, then the UTF-8 JSON body. Each line, including the empty line, ends in CRLF (`\r\n`). The display below omits those line-ending bytes. Replace `<n>` with the body's UTF-8 byte length. Send exactly that many body bytes, with no added newline.

```text
POST /v1/enrol HTTP/1.1
Host: 127.0.0.1
Content-Type: application/json
Content-Length: <n>
Connection: close

<compact JSON body with pc_label and key>
```

The version must be `HTTP/1.1`. `Content-Type` is exactly `application/json`, case-sensitive, with no parameters or trailing space. `Host` is lowercase `127.0.0.1` or `localhost`, optionally followed by a colon and one to five decimal digits with a value at most 65535. An empty port, IPv6 literal and trailing space are refused. The port in `Host` need not equal the phone port. Header names are case-insensitive and each may appear once. Values contain printable ASCII only; tabs are refused. Leading spaces after the colon are removed; trailing spaces are retained. `Content-Length` is decimal without a sign or leading zero, except `0` itself. `Transfer-Encoding` is refused. These framing rules apply to every request; every POST needs `Content-Length`.

Read the HTTP reply from `nc` standard output. Read its status and headers through CRLF CRLF, then the number of bytes in its `Content-Length`. Every reply says `Connection: close`. Keep reading to EOF rather than relying on a particular `nc` exit option. A framing refusal is sent before the phone half-closes writing and drains input as described in section 12. A command failure or an incomplete reply does not establish an enrolment outcome. Availability and behavior of `nc` on the phone remain **NOT_RUN**.

Why this shape:

- No reply returns a secret from the phone. The required command path avoids delivering `K` to a substituted local forward. This is a companion requirement, not a route restriction enforced by the phone.
- What protects the delivery differs by route. SSH checks the phone's host key against the one the owner accepted earlier. adb does not identify the phone to the PC: it only lets the phone accept or refuse the PC. On Android the step therefore relies on the owner's own cable and port, as section 4 says.
- The code lets the person see that the request on the phone came from the window in front of them. A different program that enrols a key of its own shows a different code.
- `pc_label` is limited to 24 characters from letters, digits, space and hyphen, on both screens. It begins and ends with a letter or digit.
- After three denied or expired requests, enrolling closes until the owner reopens it on the phone. With the PIN lock on, the phone takes no answer from the lock screen.
- The phone keeps one enrolled PC. Enrolling another means removing the first. Removal takes effect on the next request. The companion can forget a phone.

Stage 2 cannot use this, because a stranger's PC has no developer access to the phone. Section 13 says what it needs instead.

## 6. Signed requests

After enrolling, status, work, text and model requests are signed. The key itself is not sent on those routes.

- Exactly two routes are unsigned: `GET /v1/hello` and `POST /v1/enrol`. Enrolment has no shared key yet. The reply to hello is `{proto, label, epoch}`. `epoch` is 16 random bytes. It changes on service start-up, on Allow, on Forget and when the nonce table is full. The companion refuses a `proto` it does not know.
- Headers on every other request: `X-Link-Pc` (the `pc_id`), `X-Link-Nonce` (12 random bytes) and `X-Link-Mac`.
- `X-Link-Mac` = HMAC-SHA256(`K`, "opos-link-v1" ‖ `epoch` ‖ method ‖ path with query ‖ `Range` header or empty ‖ nonce ‖ SHA-256(body)), with each value length-framed.
- The phone compares in constant time. It checks and records the nonce in one step, under a lock, before the request has any effect. A nonce seen before in the current `epoch` is refused.
- The phone records at most 4,096 nonces per PC. When the table is full, the next otherwise valid signed request starts a new epoch, clears the table and gets the empty unsigned 401. A bad signature cannot rotate the epoch. A new epoch does not end a live lease.
- After sending enrolment, the companion says hello before each signed attempt. On an empty unsigned 401, it says hello again and compares the epoch. It retries the request once with the returned epoch and a new nonce. The first signed request after Allow using the pre-Allow epoch therefore gets 401 and recovers through hello. If hello is rate limited, wait and try hello again. A 401 with an unchanged epoch can mean a pending, denied, expired or removed key, a reused nonce, or refused framing. Check the request before concluding that the key is no longer accepted. A retry that succeeds after an epoch change does not count toward the two failed status posts that mean link down.
- Every reply to a request that passed the check carries `X-Link-Mac` = HMAC-SHA256(`K`, "opos-link-v1-reply" ‖ request nonce ‖ status ‖ range headers or empty ‖ SHA-256(body)). A request that fails the check gets status 401 with an empty body and no `X-Link-Mac`, and performs no requested operation. The valid request that rolls a full nonce table changes only the epoch and nonce table. The companion drops any other reply without a valid value. For a model file the body hash is left out, and the bytes are checked against the companion's own list (section 10).
- A retry is a new request with a new nonce.
- No clocks are compared. Time limits use each side's own monotonic clock.

The companion's HTTP client uses no proxy, follows no redirect, accepts no compressed reply, and limits header size, body size and time for every request.

## 7. Permission on the PC

These are requirements for the separate Windows companion.

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
| `GET /v1/work?wait=20` | reply 64 KB | Long poll. Returns 204, or one piece of work: `{lease, model_sha256, question, partial}`. Generation settings are fixed in the companion's model list. One piece of work per phone at a time. A PC that asks again receives the same answer under a new lease, with `partial` set to the stored text. The previous lease is replaced and its text posts receive 404 `lease`. |
| `POST /v1/runs/<lease>/text`, at most 4 a second | 8 KB | `{offset, text, done, finish}`. Reply `{acked}`. |
| `GET /v1/models/<sha256>` with `Range` | file | The bytes of one model file. |

A question is at most 4,096 UTF-8 bytes. Stage 1 has no multi-turn history. An answer is at most 12,288 UTF-8 bytes. In this document KB means 1,024 bytes.

Status, a work request, text with the same offset and bytes, and model ranges may be sent again after a failure, each time as a new signed request. The identical pending enrolment may also be repeated as described below; it does not extend the deadline.

## Wire format

### Values

- All binary values travel as lowercase hexadecimal text, in headers and in JSON. Uppercase is refused.
- `frame(v)` is the length of `v` as 4 bytes, big-endian, followed by `v`.
- Sizes in bytes: key 32, nonce 12, epoch 16, lease 16, `pc_id` 8.
- In the enrolment code and `pc_id` calculation, `K` is the raw 32 bytes. In MAC inputs, epoch, nonce and SHA-256 digests are raw bytes, never hex text. `K` is the raw HMAC key, not an additional framed value. Hex is only the wire representation in headers and JSON.
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
- `body hash` is SHA-256 of the raw reply body. For an empty body, such as a 204 work reply, it is the SHA-256 of zero bytes: the 32-byte value `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`. For a model file reply (status 200 or 206 on a models target) it is the empty value, so its frame is four zero bytes.

Every reply to a request that passed the signature check carries `X-Link-Mac`, whatever its status. A request that fails the check gets status 401 with an empty body and no `X-Link-Mac`, and performs no requested operation. The valid request that rolls a full nonce table changes only the epoch and nonce table.

Comparison uses constant-time `hmac.compare_digest`. A header that is not lowercase hex of the exact length fails the check.

The method, the target, the range value and the content range are ASCII. Each signed value has one way to be written. Before anything is computed, both sides refuse bad inputs. They refuse a key that is not 32 bytes, an epoch that is not 16, a nonce that is not 12, a method other than `GET` or `POST`, text outside ASCII, and a status that is not a whole number from 100 to 599.

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

JSON objects in UTF-8. A sender writes compact JSON with no `\u` escapes (`ensure_ascii=False`, separators `,` and `:`). A receiver checks the size of the raw bytes first, then parses. It refuses anything that is not an object, a duplicate key, an unknown key, a missing key, a wrong type (a boolean is not a number), `NaN` and `Infinity`, and a body nested more than 4 levels deep (the outer object is level 1; the deepest valid message has 2). The depth is counted on the raw bytes, so the limit does not depend on a parser's own recursion limit. A receiver accepts JSON whitespace between tokens and `\uXXXX` escapes, including valid surrogate pairs. It refuses a UTF-8 byte order mark. A lone surrogate in answer text gets 400 `text`; in another request field it gets 400 `request`. Offsets count UTF-8 bytes after unescaping, not JSON bytes, characters or UTF-16 code units. Escapes and whitespace still count toward the raw body limit. A sender must use compact JSON without `\u` escapes.

Numbers have one written form. A whole number is digits only, with no sign, fraction, exponent or leading zero, and is at most 9,007,199,254,740,991 (2^53 - 1). `offset` is at most 12,288. `tps` is a whole number, or digits with a fraction of one to three digits and no exponent, from 0 to 100,000. Any other number token in a body is refused, also where a number is not expected.

A sender must refuse numbers outside the written forms above. The phone's `gpu_link_proto.dumps` checks those forms before writing a number, including a float's decimal representation. In a work reply the question is at most 4,096 bytes and the partial text at most 12,288 bytes. The builder rejects invalid UTF-8 and characters below U+0020 other than newline and tab. Stored questions and partial answers also pass the full answer text rule below. The generic builder is not a substitute for that rule: it does not reject U+007F to U+009F. No sender writes a `\u` escape.

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

`gpu.name` is shown on the phone. `gpu.driver` is accepted in a status and is not displayed. Both may hold printable ASCII only (0x20 to 0x7E).

`tokens` and `tps` in the status request exist for the tokens-per-second display on the phone. They are numbers and change nothing else.

In a status request, `error` is not null exactly when `state` is `error`, and `copied` and `total` are either both null or both numbers with `copied` not above `total`. Any other combination is refused.

Enrol reply statuses: 202 `pending`; 409 `busy` (another request is waiting) or `enrolled` (a PC is already enrolled); 403 `closed` (three requests were denied or expired); 400 `request`, 411 `request` for missing `Content-Length`, 413 `too_long` and 429 `rate`. The same label and key sent again while that request is pending gets 202 again and does not restart its 60 seconds.

### Reply handling

A request that reaches the service and passes authentication gets a signed reply, including errors. A request refused before authentication may get an empty unsigned 401. The listener rejects malformed HTTP, a bad Host or a method other than GET or POST with unsigned 400 `request`. For a signed target, missing POST Content-Length, a malformed Content-Length, Transfer-Encoding, a disallowed Range or an oversized body gets the empty unsigned 401. These framing refusals consume no nonce. A request that passed authentication consumes its nonce even when its operation is refused.

| Reply | Meaning and companion action |
| --- | --- |
| 200 status or text | Verify the reply MAC. Read `cancel` or `acked` as appropriate. Act on `cancel` only for the held lease. |
| 200 work | Accept the new lease and stored prefix. Stop using any replaced lease. |
| 204 work | No work from this poll; poll again. The empty body is hashed normally for the reply MAC. |
| 200 or 206 model | Check the status, exact byte range and size, then verify the completed file against the companion's list. Only these replies use an empty body-hash frame in the MAC. |
| Connection closed before a status line, or before `Content-Length` bytes arrive (model request) | The file changed or the service is closing. Discard the partial bytes, say hello, and repeat the range as a new signed request. Verify the whole file against the companion's list as usual. |
| Empty unsigned 401 | Refresh hello, compare the epoch and retry once with a new nonce as in section 6. Check framing if the epoch is unchanged. |
| 400 `request` | Correct the request. An authenticated request to an unlisted target, including `/v1/work` without `?wait=N`, gets this signed error. A bad signature to that target gets 401. |
| 400 `text` | The answer text breaks the text rule. Remove refused characters before counting offsets and sending again. |
| 400 `state` | The service is closing. Stop using this connection and wait for the service to return. |
| 404 `lease` | The lease is no longer usable. Drop it and ask for work again. A status naming a lease that is neither live nor remembered also gets this error. Paused and replaced leases are not remembered. |
| 404 `model` | The model is not ready or the requested hash differs. Stop this download and report the model error. |
| 409 with `stored` | An offset or repeated text disagrees with storage. Reconcile the retained text with the stored count before resending. |
| 413 with `stored` | An append would exceed 12,288 answer bytes. Fit any remaining text within the limit, then finish with `length`; do not repeat the oversized append. |
| 413 `too_long` | The raw JSON body exceeds its size limit. Reduce it. Oversized bodies on signed targets are normally refused as 401 before authentication. |
| 416 `range` | Correct the range. This reply has no Content-Range header. |
| 429 `rate` | Wait for the relevant token bucket, then retry with a new nonce on a signed route. |
| 500 `state` | A storage read, recovery or write failed. A startup notice alone does not produce this reply or stop an intact open GPU answer resuming. Retain the text and retry an allowed operation with a new nonce. A final-post retry may reveal that the earlier write completed. |

Enrolment uses the state replies and framing errors listed under Bodies. Its replies and hello replies are unsigned.

### Answer text rule

Text is accepted when its bytes are well-formed UTF-8 (no surrogates, no overlong forms, no cut character) and hold no character of Unicode category Cc other than U+000A and U+0009. Anything else is refused whole. Control character means Unicode category Cc: U+0000 to U+001F and U+007F to U+009F. Carriage return is refused. The companion must remove every Cc character except newline and tab from model output before it counts offsets or posts text. The phone refuses the whole post with 400 `text`; it does not clean incoming answer text. Hold an incomplete UTF-8 character until its remaining bytes arrive. Bytes that are not valid UTF-8 anywhere in the JSON body get 400 `request`. Only well-formed JSON whose text value breaks the rule (a lone surrogate escape, a control character, a carriage return, or empty text with `done` false) gets 400 `text`. A post with `done` false must carry at least one byte of text. An empty post with `done` true is accepted at an offset equal to the stored count.

### Ranges

`Range` must match `bytes=<start>-<end>` in full, each a decimal number without leading zeros, with start ≤ end < total. One range only. The reply's `Content-Range` is `bytes <start>-<end>/<total>`.

The shared example messages and signatures for both sides are in `tests/fixtures/gpu_link/examples.json`. Its keys, nonces and epochs are test values.

## 9. Answer text, pause and resume

**Lease.** Each time the phone hands out work it creates a new `lease` of 16 random bytes and ties it to that PC. Text is accepted only when the lease is the current one for that answer, the signature is from the PC it was given to, and the answer's state is `loading` or `answering`. Replacing a live lease keeps the answer `loading` or `answering`. Callbacks from the replaced work reply cannot change the new lease. The phone drops the lease before saving a pause, cancellation or final post; a failed storage write restores the lease.

The service remembers the last 64 finished and page-cancelled leases, oldest first, until Forget or service restart. Only a remembered finished lease accepts an exact final-post retry: the decoded offset, text bytes, done and finish must match. A replaced or cancelled lease's text post gets 404 `lease`. A forgotten lease also gets 404 `lease`. After restart an exact repeat of a final post gets 404 `lease` even though its stored answer is correct and complete. This record is separate from the 50-answer store.

**Bytes.**

- `offset` is the UTF-8 byte position in the answer after JSON unescaping. The phone appends only when it equals the stored byte count. `acked` is the total UTF-8 bytes stored after the post. An identical repeat of earlier bytes returns the current total, which may exceed `offset` plus the post's byte length. An exact final-post repeat returns the remembered total. `stored` on 409 or on an append overflow (413) is the same total count; it is not the size of the refused post.
- The phone stores the text exactly as received. A post that contains invalid UTF-8, a partial character at its end, or a Cc character other than newline and tab is refused whole, with no change to the answer text.
- A post with `done` false whose bytes lie wholly inside the stored text is acknowledged if they are identical, with the current total. A post that starts before the stored end and runs past it, a post whose bytes differ, and any post with `done` true at an offset other than the stored count get 409 `stored`. Resend from the stored count.
- The check and the append happen under one lock. The phone acknowledges only after the text and the answer's state are
  written, the file is flushed and synced, and the renamed file's directory is synced. A directory open or sync failure
  fails the write; every failed write gets signed 500 `state`, with no new acknowledged bytes
  in the page snapshot. A failed write is never accepted from a read-back, even when matching bytes are visible.
  The PC can repeat the identical post; the retry rewrites and syncs it before acknowledging. Filesystems that reject directory sync with
  EINVAL, ENOTSUP, EOPNOTSUPP or ENOSYS offer no better guarantee; those unsupported-operation errors are ignored.
  Other apps' writes sync the directory best effort and ignore its errors; only GPU Link's answer records and pairing
  file are acknowledged on a synced directory.
- After the final post, with `done`, the phone accepts nothing more for that lease except an exact repeat of that post. While the lease is remembered, the repeat gets the same reply and changes nothing.
- The companion keeps the text of the current run in memory until the run ends, so it can send again from any earlier offset.
- The phone shows answers as plain text only.

The phone's link snapshot includes `storage`: `"error"` while an answer-store startup notice stands, after a signed
request returns 500 `state` for a storage error, or after a timer pause or service reconciliation write fails.
The request or write fault belongs to that GPU answer. It stays set while
that answer is open until a successful append stores new bytes, or a begin, pause, Stop, or terminal transition for that
answer succeeds. This includes a status that pauses work or records the PC's error as a failed answer. Ordinary status
posts, metadata saves, a save for another answer or the phone pane, and a repeat that stores no new bytes do not clear
it. A snapshot also clears it when its answer is final or gone. A failure with no answer remains an error for that
service instance; unrelated answer saves do not clear it. This request/write flag is in memory and resets when LinkService is
reconstructed. AnswerStore's deferred startup recovery writes do not set it: a recovery write that remains unwritten
is not represented by this flag. A successful status alone is not evidence that unsaved text reached storage.
When a connected PC holds unacknowledged text, keep it connected while it retries after storage recovers. Reconnect a
disconnected PC while its grant lasts so it can retry. Enrol an unpaired PC for new work; a revoked lease cannot save
its unsent text. For a fault with no answer, check storage and reconstruct the service after recovery; the instance
flag is not a pending text count.

Answer files alone use strict Store reads: an I/O error other than a missing file leaves the original bytes in place.
Invalid JSON, a wrong schema or an invalid answer record is moved to the Store's `.bad-<time>` recovery file without
rewriting its bytes. A locked re-read uses the same strict reading and record checks before moving anything aside.
Both cases set `storage_notice` on AnswerStore until it is reconstructed, even if a phone question or a resumed GPU
answer is saved successfully. Only new GPU questions are refused; a readable open GPU answer still goes to the PC
with its saved prefix. A missing answer file is an ordinary empty result, without a startup notice.

The notice says to restart AI mode; this means restarting the OPOS server process, which constructs a new AnswerStore.
For an unreadable file, restore access first. For a quarantined file, the next start scans the remaining files and
clears the notice if they can be read and validated. Reconstructing LinkService alone does not clear it. The default
`/ai` view refuses its combined question and presets while the notice stands. Phone-only questions still work through
`/ai?pane=phone`, `/api/ai` with `panes: ["phone"]`, and `AnswerStore.ask("phone", ...)`.
The current page also appends "Wait before unplugging." to a startup notice when the PC is live; waiting cannot clear
that notice, and it replaces the cable line's saved-answer sentence. Follow the notice's restart instruction instead.

Pairing and model-cache files use ordinary Store reads, like the everyday apps: an unreadable or corrupt file is
quarantined and the caller starts empty. The link can start without that cache or pairing; it rehashes the model and
requires enrolment again if the pairing was moved aside. Recovery itself can still fail, for example if a backup
cannot be created. This change preserves the everyday apps' existing recovery behavior, including the PIN lock's
existing loss of the saved PIN when its file is quarantined; it does not add a lock-file recovery policy.

**States** of one answer on the phone: `queued`, `waiting`, `loading`, `answering`, `paused`, `done`, `failed`, `cancelled`.

| Event | Phone | PC |
| --- | --- | --- |
| Cable pulled while loading or answering | At least 2.5 seconds since the later of the last status and the completed work reply means the link is down. The lease is dropped, then the GPU answer becomes `paused`. A failed pause is retried by the timer. Text is kept. | Two failed status posts mean the link is down. Generation stops. The model stays loaded while the grant lasts. |
| Cable back while the grant lasts | Hands out the same answer under a new lease, with `partial` set to the stored text. | Applies the model's chat template to the question, appends `partial`, and continues from there. Sends only new text, starting at `offset` = UTF-8 byte length of `partial`. |
| Cable back after the grant ended | Same. | Shows the permission window first, loads the model, then the same. |
| The question and `partial` no longer fit the context | On the PC's final post, marks the answer `done` with the reason `length`. | Reports `finish: length` without generating. |
| Forget this PC | Cancels handed-out open work; keeps untouched queued work. | Key and lease refused. |
| Allow a different PC key | Cancels handed-out open work before replacing the PC. | Receives queued work only. |
| OPOS restarts | GPU loading/answering pauses; phone queued/loading/answering fails with `restart`. | Lease refused. |

GPU answers still queued at restart stay queued. The first queued answer is shown as derived `waiting` in snapshots
only when no loading, answering or paused answer already occupies that pane.
Existing paused phone records are outside the restart-failure rule.

Answer-store startup keeps recovery states in memory even if their saves fail. The next successful answer save retries
pending recovery writes using the current records, and a later restart recomputes recovery from disk. Startup also
sweeps all matching answer and pairing temporary files under each destination's lock, regardless of age. A writer
holds that same lock from before temporary-file creation through writing, file sync, rename and directory sync;
a sweep waits for a live writer, so temporary files it finds under the lock are stale. An unlink failure does not
prevent valid saved answers or the pairing from loading. Only failed cleanup remains pending: answer cleanup retries
after a successful answer save, and pairing cleanup retries at Allow and Forget. Forget and removal of an answer
no longer held by the answer store also sweep matching temporary files under the destination lock.

What is promised: after a resume, the text on the phone begins with exactly the bytes it had before, and continues from there. What is not promised: that the result equals what an uninterrupted run would have produced. The engine continues from a text prefix.

Two status posts received after a completed work reply that do not name the live lease also pause that work. A newer long poll wakes an older pending poll with 204. A status with state `error` that names the live lease ends the answer: it becomes `failed` with that error code, the lease is dropped and no resume follows. An error status with no lease, or naming another lease, changes only the PC state shown.

Forget and replacement through Allow cancel work through the same operation as Stop, keeping its text and recorded PC
label and dropping its lease. An open answer counts as handed out if it is `paused`, `loading` or `answering`, or has
stored text or a recorded PC label. A PC is replaced when its key changes; a label change with the same key does not
replace its identity. Never-handed-out queued work stays available. If a Forget cancellation fails before directory
sync, its lease is retained for retries while pairing revocation can still succeed. A `DurabilityError` during
cancellation propagates, keeping the PC enrolled for a retry. The timer, Allow and work hand-out retry the cancellation
before a new PC can receive work. After service reconstruction, Allow also checks stored answer states when there is no
enrolled PC or its key changes, and cancels handed-out open work before enrolling the PC. This does not depend on an
in-memory cancellation marker. Stopping the link service attempts to pause running work. A failed pause during shutdown
is retried by a later service when it reconciles a running GPU answer without a lease before offering it. The timer also
pauses a running GPU answer that has no lease, so the page does not keep showing it as running until the next work
request. If the model file changes after a hand-out, the timer pauses the work. Text and status under the old lease then
get 404 `lease`, and the PC's next work request gets a new lease with the new `model_sha256`.

The service records the PC label and model on the answer independently of speed reports. A failed metadata write is retried on accepted text posts. This recording does not depend on `tokens` or `tps`.

Page requirements for authentication and enrolment: an expired page token requires a reload and stops stale polling
and queued mutations. A `403 locked` response immediately redacts private answer text, discards queued mutations and keeps polling for unlock.
A new enrolment must preserve focus while the owner is typing in an input or textarea, and Allow
still requires an explicit tap. These page behaviors are tested separately from the service and store contracts here.

Resume on a different PC is not part of stage 1.

## 10. Model files

The phone serves only the file named by `OPOS_GPU_LINK_MODEL`. `OPOS_GPU_LINK_MODEL_NAME` sets its display name; otherwise the file stem is used, limited to 64 printable ASCII characters. `OPOS_GPU_LINK_LABEL` sets the phone label (default `OPOS phone`). The phone hashes the file in a background thread. Its restart cache in `gpu-link-model.json` binds the digest to the path, size, nanosecond modification time, device and inode. A digest is published only if the service is still open and the file identity still matches. It makes the model ready even if writing the restart cache fails. A failed hash read is retried after five seconds unless the file identity changes, which permits an immediate retry. No work or model bytes are offered until the model is ready. The listener checks the open file identity before headers and after each chunk. A changed file stops the transfer. The phone answers a valid `Range` with 206, including a range covering the whole file. A request without `Range` gets 200. The phone accepts no file upload or executable code from the PC.

The companion must verify the completed download against its own list. Its requirements are:

- The companion carries a built-in list of accepted model files. In stage 1 the list has one entry: the SHA-256, the exact size in bytes, the name and the engine settings. The phone's own description of a file is not trusted.
- The companion refuses any other hash before it downloads a byte.
- It accepts a 200 reply only for the whole file from byte 0, and a 206 reply only when the range and the total size are exactly what it asked for and what its list says. It stops at the listed size. It refuses a 200 reply to a request that named a range.
- It writes to one temporary file, hashes the bytes, deletes the file on a mismatch, and renames it to `<sha256>.gguf` on a match. A temporary file left behind is removed at start-up and on Stop.
- It refuses to start a download that would not fit in free disk space.
- A hash supplied by the phone does not establish that the companion accepts the file. The companion must compare it with its own built-in list and verify the downloaded bytes.

## 11. Engine rules on the PC

These requirements are not enforced by the phone service. This repository does not supply the Windows companion, an engine build, archive hashes or a verified engine command line.

- Pin the llama.cpp build and archive hashes after checking that build's security fixes and supported options.
- Bind the engine to `127.0.0.1` on a port chosen by the companion. Disable its web interface and network downloads. Use one slot, context 4,096 and reasoning off.
- A fresh random API key for each start, in a file that only the current user can read, deleted when the engine has started.
- Before it sends a key or a prompt, the companion checks that the process listening on the port is the child it started. If not, it stops.
- Select the NVIDIA device explicitly from the pinned build's device list. Verify that inference uses CUDA before reporting GPU work. CPU work must not be reported as GPU work.
- The companion must use the CUDA build for stage 1 and report an error if the NVIDIA GPU is unavailable. The phone shows the PC's error state; it does not verify the device or engine backend.
- The engine runs in a job object that ends it when the companion ends.
- Neither side writes question or answer text to a log. The companion keeps no conversation on disk and switches engine logging off.

## 12. Limits on the phone

- At most 4 open link connections, including long polls and file transfers. At capacity a new connection replaces the oldest connection still reading its request. A request handed to the service for an answer is never replaced. If all four are being answered, the new connection is closed. A refusal still draining input remains eligible for replacement. A request that has been accepted but not yet fully read can therefore be closed without a reply while the listener is full.
- There is one pending long poll and one GPU answer in progress per PC.
- Headers must arrive within 5 seconds. The complete request must arrive within 10 seconds from the start of handling. The request line is at most 2,000 bytes including CRLF. The header block is at most 8,000 bytes including CRLF and its blank terminator, with at most 40 fields.
- The answer store admits at most 50 unfinished answers and trims finished answers oldest first after accepting a
  question. Start-up scans do not trim answer history; they do sweep temporary files as described in section 9.
  Failed history cleanup is retried on later questions. The 50-answer retention target is separate from the 64-lease
  retry history.
- Hello uses one shared token bucket of 10 tokens, refilled at 10 per second. Enrolment has a separate shared bucket of 2, refilled at 2 per second. Text has 4 tokens, refilled at 4 per second for the enrolled PC. Exceeding a bucket returns 429 `rate`.
- The HTTP framing rules in section 5 apply, including the exact Host and enrolment Content-Type values. Every reply closes its connection. One request travels on one connection.
- For a framing refusal, the phone sends the reply, half-closes writing and discards at most 64 KB of input, including buffered input, for at most one second and within the original request deadline. It then shuts down reading and closes. The client can read the refusal; a reset after it is not expected.
- Replies held in memory have a 10-second write deadline. Model transfers have a 10-second deadline per chunk, with no overall transfer deadline.

These fixes remove or change none of the six known limits below. Connection, framing, rate, answer-capacity and
lease-history limits are unchanged.

Known limits:

1. Any program that can reach the phone's loopback port, including through the forwarded port, can start an enrolment. The owner's comparison of the code on both screens is the only barrier. A separate enrolment port that is never forwarded would close this and is not built.
2. A signed request that the phone never processed because the connection was cut before it was read stays valid for the rest of its epoch. An observer on the path who recorded it could send it later. That observer is outside the scope of stage 1.
3. A flood of unsigned hello requests can use up the shared hello bucket and deny the PC a new epoch. The four-connection limit is the other bound.
4. A repeat of a final post after the phone restarted gets 404 `lease`, although the stored text is correct and complete.
5. The phone refuses carriage returns and U+007F to U+009F in answer text with 400 `text`. The companion must remove them before counting offsets.
6. While all four connection slots are taken, a request that has been accepted but not yet fully read can be closed without a reply. The companion repeats it as a new signed request. Peers that reconnect as soon as they are dropped can keep causing such closures, which delays the PC but does not change any answer.

## 13. Stage 2 requirements (guest mode)

Guest mode must meet all of these before it is built, and gets its own review:

1. Pairing that does not rely on a verified channel: each side commits to a random value before it sees the other's, and the code covers the key of an encrypted channel, so a relay in the middle cannot learn the key or show a matching code.
2. A way to create that channel's key on each phone image. Python's standard library can serve TLS but cannot create a key. Whether the images carry the `openssl` tool is NOT_RUN.
3. A listener that accepts only packets that arrive on the USB interface from the address at the other end of the cable. Binding to the USB address is not enough when the PC routes or bridges.
4. A stated answer to what the phone reveals before pairing. A long-lived certificate is an identifier.
5. A tested TLS client on the supported Windows versions, with an explicit check of the pinned certificate and no general bypass of certificate checks.
6. Limits on unauthenticated connections strong enough that an unpaired PC cannot keep the phone busy.
7. On the OPOS OS image: proof that a PC that has never seen the phone gets a driver and a working address with no administrator step. NOT_RUN.

## 14. What each side can do in stage 1

| Actor | Can | Cannot |
| --- | --- | --- |
| A PC with the owner's developer access | Everything that access allows, which is more than GPU Link | Not limited by this design |
| The companion on an enrolled PC | Read the model file; read the questions asked in AI mode; append text to the answer it holds a lease for; stop | Change stored text; write to another answer; act after its key is removed |
| The phone | Use the GPU while a grant lasts; make the PC keep a copy of the one model file the companion lists | Supply code; make the engine load an unlisted file; read PC files; put its own wording in the permission window beyond the label |
| Another program on the PC that can reach the forward | Reach the phone listener and start an enrolment with its own key | Use the enrolled key without knowing it; approve its own enrolment through the link protocol. The owner must compare the code on both screens before Allow |

The table's PC protections depend on the companion meeting sections 4, 5, 7, 10 and 11. The phone does not enforce those Windows behaviors.

Known limits of this threat model: the six known limits in section 12 apply here.

## 15. Risks accepted

- An enrolled PC reads the conversation and can return wrong text. The service records the answering PC's label and model independently of speed reports, with failed metadata writes retried on accepted text posts. The enrolment screen discloses that the PC sees the question.
- Software running as the same Windows user can read `K`, use the owner's developer access and read the engine's memory.
- On Android, a device placed between the phone and the PC on the cable could read or change the enrolment, because adb does not identify the phone to the PC. The owner uses their own cable and port.
- Someone with an unlocked phone can approve a PC, and the PC then appears in the list. Someone with an unlocked PC can grant access or copy `K`.
- `K` does not expire. It ends when the owner removes the PC on the phone. Removal is checked on the next request, not in the middle of one.
- The companion is required to retain its model copy until the user removes it from the tray menu or uninstalls. The phone discloses that the PC keeps a copy. Its size is determined by the companion's accepted model list.
- Prompts may remain in the PC's memory, page file or crash dumps.
- An engine flaw reachable through prompt text remains a PC risk. Section 11 requires build pinning, loopback, an API key and a job object.

## 16. Open points

1. Whether the chosen phone has `nc` for the enrolment command: NOT_RUN. To check in the first hour. Without it, stage 1 cannot enrol on that phone.
2. Settled: the reply to the enrolment message says whether the phone is waiting for its owner, is busy with another request, already has a PC, or has closed enrolling. A command that fails gives no such reply.
3. How the phone notices the cable inside Termux without root. Stage 1 uses the status timeout only.
4. Whether the companion's loopback listeners show a Windows firewall prompt to a standard user: **NOT_RUN**.
5. The owner's yes to guest mode, after the demo.

## 17. Tests before any device run

This is a pre-device checklist, not a record that every case passed. Phone-side unit tests and shared vectors are present. The separate Windows companion needs its own tests. Physical-phone checks remain **NOT_RUN**.

- A fake PC in the phone's unit tests and a fake phone in the companion's tests, both driven from `tests/fixtures/gpu_link/examples.json`, signatures included.
- Enrolment: a second enrolment while one is pending, expiry, three denials, a signed request made with a pending key, a second PC while one is enrolled, a message over 1 KB.
- Signatures: wrong key, replayed nonce, a retry with a reused nonce, two copies of one request at the same moment, changed body, changed path, changed range, a reply without a signature, a request signed for an old epoch.
- Text: wrong lease, cancelled lease, wrong PC, wrong offset, repeat with different bytes, invalid UTF-8, a cut inside a character, control characters, a 12 KB overflow, a cancel that names an old lease, a restart between a post and its acknowledgement, a lost work reply followed by a new work request, a repeat of the final post, a different post after the final one.
- Seam: cut the answer at random byte positions, resume, and check the promise in section 9.
- Model file: one changed byte, a wrong size, a reply that ignores the range, a 200 reply to a range request, an unlisted hash, a full disk, a download interrupted by Stop.
- Limits: slow headers, a slow body, too many connections, enrolment floods.
