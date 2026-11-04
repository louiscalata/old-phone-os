<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Public simulation demo

`python3 -m opos.public_demo` is a separate, explicit internet-facing entry point.
It reuses the phone page, `Phone`, the deterministic runtime and the in-memory
phone action dispatcher from `opos.web`. It does not run the local web server's
route dispatcher, model setup or startup. `opos.web` and `secure.check_bind`
continue to reject non-loopback binds. The public server also skips the standard
HTTP server's reverse-DNS startup lookup.

Hackathon prototype. This public demo simulates the battery, calls and messages;
it is not a phone operating system and has not run on a physical phone.
The public demo is live at https://opos-demo-production.up.railway.app.
Muse Glimmer is off there; Why, Plan and Ask use deterministic battery rules.

## Run locally

From the repository root, with Python 3.11 or newer:

```sh
export PYTHONPATH=src
OPOS_PUBLIC_HOSTS=localhost:8080 PORT=8080 python3 -m opos.public_demo
```

Open `http://localhost:8080/?demo=1` (it starts unlocked). Use exactly the host in `OPOS_PUBLIC_HOSTS`:
`http://127.0.0.1:8080/` returns 421 because only that host is accepted. If port 8080 is taken, change both
numbers, for example `OPOS_PUBLIC_HOSTS=localhost:8787 PORT=8787`. The server prints the URL to open. Change the battery
slider, then open the top battery bar. At the initial 60%, Undo on maps turns
the soft-paused task back on; low-battery and heat floors still protect calls
and messages. Why, Plan and Ask answer from the current rules and explicitly
identify the public mode.
Settings such as theme are browser preferences, never host preference files.

## Railway configuration

The checked-in Procfile and railway.json start `python3 -m opos.public_demo`.
The explicit Dockerfile builder uses `python:3.12-slim`, also documented by
`runtime.txt`. It copies only source and the license, sets `PYTHONPATH=/app/src`,
and runs as numeric non-root user 65532. There are no install commands: no pip,
package build, model weights or third-party dependencies. `.dockerignore`
excludes the rest of the checkout, including Git metadata and local secrets.

Railway's [current builder reference](https://docs.railway.com/config-as-code/reference)
lists Railpack and Dockerfile for new services. It also marks railway.json as a
legacy configuration mechanism: if the service uses the newer infrastructure
flow, set the same start command, one replica and `/healthz` health-check path
in that service's configuration.

Configure the service variables as follows:

```text
OPOS_PUBLIC_HOSTS=opos-demo-production.up.railway.app,healthcheck.railway.app
OPOS_TRUST_PROXY=1
```

Railway supplies `PORT`; the public process binds `0.0.0.0:$PORT` (8080 when
omitted locally). The host allowlist above uses the live demo domain.
Use one replica/process, no persistent volume, and no model credentials. All
state is process-local and disappears on restart or replacement.

`OPOS_PUBLIC_HOSTS` is mandatory, with up to 32 exact authorities, including a
port if the browser sends one. There are no wildcard, suffix, implicit localhost
or health-check exceptions. A Host mismatch or duplicate Host returns 421;
an Origin mismatch or duplicate Origin returns 403. Cross-site API/subresource
requests return 403; top-level GET navigation to the landing is allowed so judges
can follow a link from another website.
An Origin, when supplied, must exactly match the request's accepted Host and
scheme. Missing Origin is allowed for same-origin GETs and non-browser clients;
API tokens remain required for existing sessions.

`GET /healthz` returns exactly `ok` with status 200. It creates no session, cookie,
phone or rate bucket and exposes no simulation state. It still checks Host and
Origin. Railway uses `healthcheck.railway.app` for health checks, so it must be
explicitly included in the variable. See [Railway health checks](https://docs.railway.com/deployments/healthchecks).

Trust forwarding only behind the hosting proxy, with direct origin access
restricted to that proxy. With `OPOS_TRUST_PROXY=1`, only the right-most
X-Forwarded-For hop appended by that trusted proxy is used for rate limits
(a valid IP is required); client-controlled earlier hops are ignored. An exact
X-Forwarded-Proto of `https` sets Secure cookies and HSTS (`max-age=31536000`).
The proxy must append the actual client address to X-Forwarded-For (or replace
it with that address), and strip/replace client-supplied X-Forwarded-Proto. This
configuration trusts one ingress proxy; do not add another hop without revisiting
the rate-limit identity. Without that
opt-in, forwarded headers have no effect. Missing forwarding uses the socket
address and HTTP. Ambiguous trusted forwarding headers are rejected.

## Isolation and limits

- Each browser cookie gets a random 256-bit session identifier, independent
  page token, `DemoPhone`, lock and action buckets. Identifiers supplied by a
  visitor never become new session keys. Existing sessions require their own
  constant-time-checked API token. Cookies are host-only, Path=/, HttpOnly,
  SameSite=Lax, Max-Age=900, and Secure on trusted HTTPS requests. Lax lets a
  cross-site top-level GET carry the existing cookie, preserving the visitor's
  session, token and phone state. Origin checks and cross-site API/POST denial
  remain in place.
- At most 300 sessions are retained. Activity refreshes the idle time; after 15
  idle minutes a session is expired on the next store access. Oldest idle
  sessions are reclaimed first; at capacity the least recently used session is
  evicted. No background work is scheduled for inactive phones.
- Expired, evicted or missing sessions get a new phone and cookie, without an
  error. The response supplies a new page token and the public wrapper updates
  it. A stale POST's mutation is **not replayed**; the page announces the reset.
  Concurrent in-flight references are additionally bounded by 32 connections.
- Per-IP buckets allow 8 requests/second with a burst of 40. The table holds at
  most 4,096 IPs, expires idle entries after 15 minutes and rejects new IPs at
  capacity rather than evicting active rate history. Health checks bypass it.
- Per-session buckets allow 20 actions/second, burst 40; calls and sends each
  allow 0.2/second, burst 5; receive allows 2/second, burst 20; Why, Plan and Ask
  each allow 0.2/second, burst 3. Rejections return 429 with Retry-After.
- Request bodies are capped at 4,096 bytes; unsupported transfer encoding,
  duplicate lengths, non-JSON bodies and invalid numeric shapes are rejected.
  Text/number/name lengths retain the local server's limits.
- Simulation speed is at most 60x, one tick covers at most five real seconds,
  and the existing tick loop has a finite work bound. Message and simulated
  modem event histories each retain at most 100 entries. Battery history,
  estimates, task lists and dashboard events are also bounded.
- The server retains the local plumbing's 32-connection cap, listen queue of 16,
  ten-second socket timeout and 15-second whole-request deadline. Public replies
  close connections, and response writes occur outside the simulation lock.

This is a small demonstration service, not a general-purpose hosting server.
Its limits bound memory and work per process; they do not guarantee availability
against distributed traffic. Platform-level traffic controls remain useful.

## What the public handler can do

Only `GET /`, `GET /?demo=1`, `GET /api/state`, `GET /healthz`, `GET /favicon.ico`
and `POST /api` are routed; HEAD follows the GET routes without a response body.
The API action allowlist covers battery/temperature, simulated
calls/messages, open thread, helper visibility, simulation ticks, charging,
goal, Undo, foreground task and the three deterministic helper answers.
Every other route, method or action returns 404 after the Host/Origin and
request-rate boundary. The public page uses the original phone DOM and scripts,
with a separate landing wrapper and public-only CSS/JS. The original `page()`
102,400-byte budget is unchanged.

There is no public route to setup, installation, AI tools, preference-file
reads/writes, host memory diagnostics, real battery or modem access, helper
restart or process exit. Public construction never installs GlimmerAdvisor.
Public helper dispatch never calls model selection, Muse Glimmer, a LAN server,
local port 8080, paid inference or Wikipedia, even if model environment variables
are present or a client sends `web: true`. Wikipedia and Restart controls are
hidden and disabled; server-side route restrictions enforce the boundary.
The reusable deterministic Router still makes the battery decisions.

Responses retain the nonce-based CSP (including frame-ancestors 'none'),
no-store, nosniff, no-referrer, same-origin resource/opener policies and denied
camera/microphone/geolocation permissions. Public code adds HSTS for trusted
HTTPS. Scripts/styles have nonces; there are no external assets or inline event
attributes. The two project links are ordinary navigation links, explicitly
allowed only as literal anchors in the landing by the repository guard; they
do not expand its model/network host allowlist.

## Verification

```sh
TMPDIR=$(mktemp -d)
export TMPDIR
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -B -m unittest tests.test_public_demo tests.test_web tests.test_web_security tests.test_demo_day tests.test_lock_story tests.test_panel_decision
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -B -m unittest discover -s tests -t .
python3 -B guard/check.py
```

`PublicBoundary` uses real TCP sockets. `PublicProtocolWithoutSockets` repeats
its HTTP parsing/dispatch assertions using an in-memory transport, for restricted
environments. The latter does not prove real binding, deadline enforcement,
connection admission or browser behavior. The outbound-denial test patches
urllib/http.client entry points and model/host functions to fail if called,
then exercises every allowed action with hostile model configuration present.
