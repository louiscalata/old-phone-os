<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OPOS and Meta models

## Meta models in OPOS

Mission: free AI for people who cannot buy it. The free AI route needs hardware the owner already has: a computer with an estimated 24 to 32 GB of memory for Muse Glimmer (the timed run used a 64 GB MacBook Pro), or a phone with enough free memory for the smaller Llama 3.2 models, whose phone speed is unmeasured. The default path costs $0 and needs no account, key or card. The paid path (Muse Spark Open through Baseten with OPOS_SPARK_OPEN=1 and the user's own key) is off by default and never needed; when enabled it is used only for Why and Plan when the battery estimate falls short of a daily goal, never for Ask or background routing.

The helper is opt-in and shows which engine answers. The default UI never names AI.

Why? restates the computed `why_paused` reason and validated `paused_by_glimmer` attribution,
without asking the model to reconcile the raw estimates again. It allows four short sentences
and uses Ask's budget: 400 tokens plus 1,200 tokens of reasoning headroom for Glimmer, with low
reasoning effort (200 tokens for the small local Llama route). The 1,600-token total is shared,
not a separate reasoning cap or a reserved answer allowance. These are token limits,
not latency guarantees. Why, Plan and Ask retain a 115-second shared route deadline; earlier
routes share that time and the last route gets what remains. Empty or token-truncated replies
are rejected. In the final November 3 desktop capture, all four Ask and all four Why? requests
returned Muse Glimmer with no fallback, within 115 s. Ask took 51.699/53.077 s in dark appearance
and 42.914/44.782 s in light; Why? took 66.549/60.696 s in dark and 63.853/78.651 s in light.
These are UI response times on the owner's computer, not pure inference times or phone-speed
measurements; see the [README capture table](../README.md#muse-glimmer-answering).
The helper contribution line uses actual paused-task attribution and the
background advisor's observed status, not the configured answer route's name.

Muse Glimmer and Muse Spark contributed code alongside the recorded non-Meta repairs. The use of a development model is distinct from whether a runtime model has been tested on particular hardware.

| Tier | Model | Where | Cost | Needs |
|------|-------|-------|------|-------|
| 0 local | Meta Llama 3.2 1B | on this phone at 127.0.0.1:8080 via llama.cpp | $0 | about 1.5 GB free memory, Q4_K_M build |
| 0 local | Meta Llama 3.2 3B | on this phone at 127.0.0.1:8080 via llama.cpp | $0 | about 4 GB free memory, Q4_K_M build |
| 1 online per question | Meta Llama or Glimmer with Wikipedia snippets | en.wikipedia.org search API | $0 | tick "Search Wikipedia", no key, no account |
| 2 LAN | Muse Glimmer 30B (Apache-2.0) | on a computer you own (OPOS_LAN_URL, private addresses only) | $0 | computer with an estimated 24 to 32 GB of memory (timed on 64 GB) |
| Paid opt-in | Muse Spark Open | Baseten | user pays | OPOS_SPARK_OPEN=1 and BASETEN_API_KEY |

Llama 3.2 models are Meta open-weight models under the Llama 3.2 Community License. Attribution is needed if redistributed. OPOS does not ship their weights. OPOS does not download weights by itself.

Tier 1: with Search Wikipedia ticked, the question is sent to the Wikipedia search API (en.wikipedia.org) and up to three snippets go into the prompt. The model is asked to answer from them and cite them as [1], [2]; OPOS shows the source links but does not verify the model's citations.

If nothing is reachable the helper says so and shows fallback text and any found sources.

What does not run on old phones: Muse Glimmer was tested on a computer; this project does not establish that it runs on an old phone. Llama 3.2 1B needs about 1.5 GB free and 3B needs about 4 GB free. Many old phones do not have this.

What is unmeasured: no phone-speed number exists for Llama on any phone. The Llama tier is not yet measured on any phone.

## What is not a model

The governor is a transparent rule router (src/opos/router.py). It is plain arithmetic, not a model.

Hard floors apply before anything else and nothing the owner does overrides them: calls, messages and alarms are always allowed; at or above 45 C (HOT_C) only ESSENTIAL tasks run, charging or not; on battery, when less than 15 percent of the charge is usable (rule_decide's CRITICAL mode), only ESSENTIAL tasks run; below the reserve kept for calls (opos.battery.essentials_reserve, at least 0.7 percent of charge) only ESSENTIAL tasks run, even while charging; and on battery the worn-battery reserve (opos.reserve.apply_reserve), which engages only when battery health is below 50 percent, may refuse tasks, including the app in use. Within those floors the Router allows tasks while charging. With a daily goal, the energy budget is cumulative: non-essential tasks are funded in a fixed order (USEFUL before OPTIONAL, then lower cost first, then name) and each funded task's cost is subtracted before the next is checked. Without a goal, optional tasks need an effective charge of at least 80; useful tasks need enough charge for a 4-hour horizon (HORIZON_H=4.0). Effective charge is level × battery health. Governor intersects the Router's allowances with rule_decide's floor. Goal-saving pauses, the Router's affordability pauses (even when the Router reports CRITICAL), rule_decide's SAVER pauses and Glimmer's extra pauses are soft: the owner's Undo or foreground use may override them, and an Undo lasts until the mode changes. A task held by a hard floor shows that floor as its reason, and Undo has no effect on it. The home screen, battery panel, lock-screen story, helper facts and simulated drain all read the same final decision.

Every decision is explained in words. The helper's "Why?" button asks a Meta model to restate it. Glimmer routing (src/opos/advisor.py): when the helper is visible and the web server runs, Muse Glimmer (loopback, or your own LAN computer) is asked in the background which allowed non-essential tasks (optional or useful) to pause. It can only pause more tasks. It cannot allow anything the router deferred and cannot touch calls, messages or alarms. Answers are cached per battery state, the rule router answers instantly meanwhile, and if Glimmer is unreachable the rule router decides alone (offline floor). Turn it off with OPOS_GLIMMER_ROUTING=0. Hiding the helper prevents new advisor work and rejects new Why, Plan and Ask requests; it does not cancel a model request already in flight.

A previous learned classifier was removed. It was effectively a smoothed 4 hour rule. In our simulations during development the router gave about the same important minutes (those comparison runs are not kept in this repo) with no model file, no training and no hash check.

Agentic loop (visible helper): observe battery state -> decide with the router -> plan (the Plan button shows the order Glimmer suggests for the saving steps, or the fixed order when no model answers; it does not execute them by itself) -> act (pause tasks, apply savings steps) -> explain ("Why?") -> undo (dashboard undo). Helper text never changes what the phone does. The model Explanations and the Plan order are advisory; Glimmer routing may only pause extra non-essential tasks.

Measured in simulation (PYTHONPATH=src python3 -m opos.profiles, 6 device profiles; important minutes = simulated elapsed minutes before depletion or brownout; useful minutes = summed simulated minutes useful tasks run; missing_kept checks calls, messages and alarms): fresh battery: rules 437 important, router 743, static low-power 316. Worn battery: rules 325, router 518, static 160. Cold: rules 376, router 619. Hot: rules 378, router 619. Low start: rules 155, router 156. End-of-life battery: rules 225, router 338, static 97 (Static browns out in 92.5 percent of runs). The recorded runs report 0 brownouts and 0 missing_kept counts for router and rules in every profile; missing_kept covers calls, messages and alarms. The router figures come from the full runtime, so the worn and end-of-life figures include the worn-battery reserve's task pauses. The router gives up useful minutes to buy important minutes in every profile (useful minutes, router vs rules: fresh 252 vs 403, worn 23 vs 180, cold 105 vs 281, hot 104 vs 283, low start 0 vs 1, end of life 0 vs 88).

Worn-battery hours of working calls (PYTHONPATH=src python3 -m opos.evaleol), averaged over 40 simulated worn phones, without and then with OPOS's worn-battery reserve and burst shaping added to each policy (the router's runs include the reserve's task pauses both times): plain 0.76 and 1.91; static 1.62 and 2.74; rules 3.67 and 4.73; router 5.52 and 6.54. In the first set every simulated phone browned out; in the second none did. These are simulations, not measurements on a real worn phone.

## Open-weight check

`PYTHONPATH=src python3 -m opos.muse --check` prints which route is configured, whether its endpoint returns a readable `/models` list, the model ids it serves, and `model_served`, which is true only when the route's exact model id `muse-glimmer-30b` is in that list (default endpoint http://127.0.0.1:1234/v1; `OPOS_GLIMMER_URL` selects another loopback port). `--check --probe` also sends one chat request ("Reply with OK.") with `max_tokens=1800`: the default helper budget of 600 answer tokens plus the 1,200-token reasoning allowance, `reasoning_effort` low, temperature 0, and a 90 s timeout, or the helper timeout when that is lower. The helper timeout is `OPOS_MUSE_TIMEOUT` in seconds, held between 5 and 600; when it is unset or invalid it is 120 s. It adds `answered`, and for a readable completion also `latency_s`, `finish_reason`, `reply` (the first 60 characters of visible text, when present), and `reply_model` (the name the server reports, which does not attest the weights). Errors are `HTTP <code>`, `unreachable`, `bad response`, `empty reply`, and `reasoning used the whole token budget`, which means the server stopped at the token limit (`finish_reason` `length`) even if some text came back. Exit 0 needs a reachable endpoint and `model_served`, and with `--probe` also `answered`; otherwise the output adds a `hint` and the exit code is 1. When the model id is served but the reply has no complete visible text, the hint says the server answered and suggests lower reasoning effort or checking `message.content`; otherwise it says how to serve the model. `answered` is true only when the reply contains visible text and was not cut off at the token limit; listed ids do not attest the weights a server runs. If a server returns reasoning inline in `<think>` tags, OPOS uses only the text after the last `</think>` and before any later `<think>`. Helper replies cut off at the token limit are not used either: Why, Plan and Ask try the next route or fall back, and Glimmer routing waits a minute before asking again. A plain `--check` only reads `/models`: on November 3, 2026 a proxy on port 1234 listed `muse-glimmer-30b` while every chat request returned HTTP 502, which only `--probe` revealed. Ask the helper a question to check a real answer.

## AI coding CLI

A free open-weight coding path is the open-source opencode CLI pointed at local Glimmer through an OpenAI-compatible endpoint.

Muse Code (Meta) is paid, needs an account, and runs on Mac and Windows. It is optional and OPOS never stores its sign-in. Pointing Muse Code at a local endpoint is community-documented but has not been verified by us.

## Guard

`guard/check.py` is an optional static repository check. Its fixed non-Meta-name pattern scans `src/`, `deploy/`, `install.sh`, `install.ps1` and `requirements.txt`; its URL-host check scans `src/` and `deploy/`. A violation makes the check fail when it is run. It reads source text and does not monitor or restrict runtime network traffic.

Configured starting destinations are loopback model servers, a user-supplied private-IP LAN endpoint, en.wikipedia.org for an explicitly requested search, dev.meta.ai for Muse Code installation, and Baseten for explicit paid opt-in. This is not an enforced network allowlist: Wikipedia search and model requests ignore HTTP_PROXY and system proxy settings and do not follow redirects, and the OPOS Muse Code installer downloader also refuses redirects. This describes the initial installer download; downloads performed by the vendor script are a separate scope.

## How OPOS was built

This is a reviewed source snapshot with recorded Meta contributions and adopted Codex, Claude and Grok Bot work. It does not contain the original development Git history or private activity/prompt archives. See [../NEW-WORK.md](../NEW-WORK.md), [../WORK-BY-MODEL.md](../WORK-BY-MODEL.md) and [provenance.md](provenance.md) for concise public credits.

Actual desktop Glimmer answering was observed in development. The postmarketOS QEMU image that was checked had no model server and no route to that computer, so model inference on the image remains unverified. Its critical-battery Ask showed the rules' resting explanation; that is not a model reply.

An earlier postmarketOS QEMU kiosk rendered only after the Pixman renderer was set by hand for testing, because the default GLES2/GBM renderer failed. On November 3, 2026 an image built from this tree reached Home with the packaged tinydm renderer hook and no manual override ([pmos.md](pmos.md)); both are virtual-machine results, not physical-phone results. Setup Finish, reopening saved choices and normal service-restart readback were checked separately. A build from changed source needs its own tests and its own recorded source and image identity; test counts from earlier, unpublished runs do not verify this source.
