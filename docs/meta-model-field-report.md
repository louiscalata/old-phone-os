<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Meta Models in Old Phone OS

Field report · Meta Global AI Developer Hackathon · Snapshot of November 3, 2026 (event October 27 – November 4)

**Field observations from one project, not a benchmark.** Most settings rest on 1–3 distinct prompts.

We built Old Phone OS (OPOS), a prototype phone interface that pauses non-essential apps so a worn battery lasts until a time the owner picks, with four Meta models. Fixed rules make those decisions.

Muse Glimmer ran on the Mac that simulated the phone; it explains the decisions and may pause extra apps, but it cannot resume anything the rules paused. Muse Spark, Spark Open and Wonder did most of the model-credited coding. Here is what we learned, as eight findings and five requests.

- Muse Glimmer 30B · Q4_K_M GGUF · LM Studio
- Apple M5 Pro MacBook Pro, 64 GB
- Phone simulated on the Mac; no phone hardware tested
- Small samples: sample sizes are stated with the results

“The coordinator” means the Claude agent that coordinated the development work and merged the model contributions, as described in [Development contributions](../WORK-BY-MODEL.md) and [Provenance](provenance.md).

## Five headline numbers

| Number | Observation |
| --- | --- |
| 92% | of Glimmer’s output was hidden reasoning (median of 8 tasks, retries included) |
| 34 s | for a short helper answer (median of 11 benchmark prompts; in-app answers took 30–95 s) |
| 0 of 5 | reasoning settings turned reasoning off |
| 15 of 15 | agent runs hit a 300-second deadline before answering |
| 100% | of helper answers used every supplied number and invented none (6 of 6) |

## Anatomy of a one-sentence answer

| Phase | Time | Tokens |
| --- | --- | --- |
| Thinking | ≈33.4 s | 508 hidden tokens |
| Answering | ≈1.4 s | 21 non-reasoning tokens |

The short helper prompt with `reasoning_effort = none`, 34.8 s end to end. Time split by token share. One run; the other short answers we timed had a similar split ([finding 1](#finding-1-reasoning)).

## The eight findings

1. [Hidden reasoning was 71%–99.98% of the output on all eight tasks listed below, retries included.](#finding-1-reasoning)
2. [Speed held at 12.5–16 tokens/s in 39 of 43 timed runs, so reasoning sets the wait: 34 s for a short answer.](#finding-2-latency)
3. [No request setting turned reasoning off, on the setup we tested.](#finding-3-controls)
4. [max_tokens includes reasoning: at 64- and 256-token caps the answer came back empty.](#finding-4-budgets)
5. [Fixed deadlines turn long reasoning into agent failures: 15 of 15 hit the deadline.](#finding-5-agents)
6. [Grounded helper answers kept to the supplied facts 6 of 6 times; tool calls and images were rejected.](#finding-6-what-worked)
7. [As a decision-maker Glimmer beat doing nothing but not the best fixed choice: one of the best options in 69% of states, against 54.5% for always keeping the policy and 73.5% for always raising the battery threshold.](#finding-7-decisions)
8. [Hosted Meta models did most of the building: Spark alone holds 68% of the model credits in Git.](#finding-8-building)

## Finding 1: Reasoning

### Hidden reasoning was most of the output, on every task listed below

**92%** of output tokens were reasoning (median, retries included)

Glimmer reasons before it answers. On this setup that reasoning was most of the response: across eight tasks and two retries, from a one-line helper answer to a code draft, it took 71% to 99.98% of the output tokens.

Long drafts that finished were the most efficient, at 71%–76%. Short answers and plans ran 89%–98% in these runs, which is where it hurts, because users wait for those. One earlier plan, on October 25 before the event, was the exception at 50% (159 of 317 tokens).

The first code draft spent 11,997 of its 12,000 tokens reasoning and returned nothing. A retry that supplied the worked-out design and asked for under 3,000 tokens of reasoning finished normally, with 6,938 reasoning tokens (71%) and a 12,033-character draft.

#### Share of output tokens spent reasoning, per task

| Task | Context | Reasoning (hidden) | Everything else | Total output tokens | Reasoning share |
| --- | --- | --- | --- | --- | --- |
| Plan | OPOS planner, desktop | 1,246 | 28 | 1,274 | 97.8% |
| Helper answer | OPOS helper, desktop | 478 | 47 | 525 | 91.0% |
| Final helper answer | OPOS helper, desktop | 356 | 42 | 398 | 89.4% |
| Short helper answers | 6 synthetic phone states, pooled | 3,067 | 241 | 3,308 | 92.7% |
| README draft | long prose | 4,262 | 1,469 | 5,731 | 74.4% |
| Submission draft | long prose | 2,917 | 936 | 3,853 | 75.7% |
| README opening | short prose | 2,327 | 105 | 2,432 | 95.7% |
| README opening, retry | short prose, coached retry | 4,782 | 147 | 4,929 | 97.0% |
| Code draft, first try | hit its 12,000 cap, 0 characters | 11,997 | 3 | 12,000 | 99.98% |
| Code draft, retry | design supplied, asked for `<3,000` reasoning | 6,938 | 2,871 | 9,809 | 70.7% |

Server-reported completion and reasoning tokens. n = 8 tasks, 15 runs in 10 rows (the short-helper row sums 6; two rows are retries). Not in the table: the October 25 plan at 50%. November 2–3.

## Finding 2: Latency

### Reasoning length, not speed, decides the wait

**34 s** median wait for a short answer (11 benchmark prompts)

Generation ran at about 16 tokens/s on the Mac (streaming estimate 15.8–16.4 tokens/s across 9 runs on November 3). The coordinator’s separate check, whose setup was not recorded, saw 14.8–15.0 with one or three copies loaded and read that as a memory-bandwidth limit.

Most listed runs went 12.5–16 tokens/s end to end, from a one-line answer to a 9,809-token draft. The three slowest drafts, at 8.7–9.8, spent most of their run sharing the LM Studio server with another Glimmer request. The November 3 benchmark also timed 23 short requests (streaming, cap and JSON tests): 22 ran at 13–16 tokens/s, and the first long-prompt request waited 11.7 s for any text and averaged 8.1.

So when Glimmer is not sharing the server, wait time is roughly output tokens divided by 12.5 to 16. A short answer took 34 s, a plan 81 s, a README draft 7.4 minutes. In the app, helper answers took 30 s and 38 s in two saved runs; the coordinator reported 42–95 s for others. On our Windows PC (RTX 3080 Ti, running mostly on CPU) Glimmer managed about 4.2 tokens/s, so the one-sentence answer above would take an estimated 2 minutes (we did not time it there).

#### Seconds against output tokens, 20 timed runs

| Run | Tokens | Seconds | Tok/s |
| --- | --- | --- | --- |
| Plan | 1,274 | 80.6 | 15.8 |
| Helper answer | 525 | 38.4 | 13.7 |
| Final helper answer | 398 | 30.0 | 13.3 |
| README draft | 5,731 | 443.7 | 12.9 |
| Submission draft | 3,853 | 307.6 | 12.5 |
| README opening (shared server) | 2,432 | 263.5 | 9.2 |
| README opening, retry (shared server) | 4,929 | 569.8 | 8.7 |
| Code draft, first try (shared server) | 12,000 | 1,225.0 | 9.8 |
| Code draft, retry | 9,809 | 691.0 | 14.2 |
| Short answer | 481 | 31.8 | 15.1 |
| Short answer | 516 | 33.2 | 15.5 |
| Short answer | 513 | 33.2 | 15.4 |
| Short answer | 438 | 28.5 | 15.3 |
| Short answer | 738 | 47.5 | 15.5 |
| Short answer | 622 | 40.1 | 15.5 |
| Reasoning-setting test | 529 | 34.8 | 15.2 |
| Reasoning-setting test | 529 | 34.0 | 15.6 |
| Reasoning-setting test | 628 | 40.2 | 15.6 |
| Reasoning-setting test | 529 | 33.8 | 15.7 |
| Reasoning-setting test | 567 | 36.2 | 15.7 |

Original chart annotations: 13 short answers; Plan; 5 drafts, 4–12 min; Code draft, no output. Reference lines: 10 and 16 tokens/s.

| Original logarithmic chart axis | Tick labels |
| --- | --- |
| Output tokens (reasoning + answer) | 300; 1,000; 3,000; 10,000 |
| Seconds | 30; 100; 300; 1,000 |

n = 20 runs on the Mac. Runs marked “shared server” shared the server with another Glimmer request for most of their run; the README and submission drafts overlapped each other briefly. Not included: 23 benchmark runs under 300 output tokens. The table includes every number from “Show the data”.

## Finding 3: Controls

### No request setting turned reasoning off

**0 of 5** settings turned reasoning off

We tried five request settings on the same short prompt: `reasoning_effort` none, low, medium and high, and `enable_thinking=false`. All five still reasoned for 508–607 tokens before a short answer (21 non-reasoning tokens). In one run each, none, low and high produced identical token counts, which points to those values being ignored rather than weak. Medium and `enable_thinking=false` did change the output, but toward more reasoning, not less. We sent no request without a setting, so the default is unknown.

LM Studio’s server log, from an agent run that asked for no reasoning, shows it could not map that request onto this model:

> Reasoning setting 'off' cannot be converted to any custom KVs.

One run per setting. A separate `thinking_budget_tokens` field exists in the template; we did not test it.

#### Reasoning tokens per setting (an “off” switch would read 0)

| Setting | Reasoning tokens |
| --- | --- |
| `effort: none` | 508 |
| `effort: low` | 508 |
| `effort: medium` | 607 |
| `effort: high` | 508 |
| `enable_thinking: false` | 546 |

Original chart scale: 0–700 tokens.

Same prompt, 768-token cap, temperature 0, 1 run each. November 3 benchmark.

## Finding 4: Budgets

### max_tokens includes reasoning, and reasoning goes first

**Empty** answers at 64- and 256-token caps: this prompt reasons for 254 tokens before answering (285 in all)

With a 64- or 256-token cap the answer came back empty: reasoning used the whole budget. At 600 the same prompt fit, with 254 tokens of reasoning and a short answer, 285 tokens in all. Requests ran at temperature 0, and the three runs at each cap returned identical token counts. All 9 streaming runs, capped at 192 tokens, did the same: 189 reasoning tokens each, no visible text. A different helper prompt spent 588 of 600 on reasoning and cut its answer off.

At the extreme, a code draft capped at 12,000 tokens ran for 20 minutes and returned zero characters. These empty responses end with the same `length` finish as a long answer cut short, so an app must check for empty content itself to learn the budget went to reasoning. A coached retry of the same task finished in 11.5 minutes, 71% of it reasoning.

#### Token use against the whole budget

| Token cap (whole budget) | Context | Reasoning tokens | Answer tokens | Result |
| --- | --- | --- | --- | --- |
| 64 | same short prompt, 3 identical runs | 61 | 0 | Empty |
| 256 | same short prompt, 3 identical runs | 253 | 0 | Empty |
| 600 | same short prompt, 3 identical runs | 254 | 31 | Answered |
| 600 | a different helper prompt | 588 | 12 | Answer cut off |
| 12,000 | code draft, 20 minutes | 11,997 | 0 | Empty |

The rest of each whole budget represents other tokens or unused budget.

Caps 64/256/600: one prompt at temperature 0; its 3 runs at each cap returned identical token counts, November 3. The other two rows are single real runs from the same week.

## Finding 5: Agents

### Fixed deadlines turn long reasoning into agent failures

**15 of 15** agent runs hit the 300-second limit with no answer

Our agent pipeline (Nisi, in the AGIW suite) gives the author model 300 seconds. In the one run we have a server log for, Glimmer spent the whole time reasoning: the log shows 4,084 reasoning tokens and empty content at 301 s.

Nisi logged all 15 author runs, across 6 work lanes, as adapter errors between 300.96 and 301.19 s, with no files written. Of these, 12 had asked for `reasoningEffort: none`. None returned an answer before the deadline; in the logged run, the answer never started.

#### What the agent saw: 300 seconds of thinking, then the deadline

| Observation from the one logged run | Value |
| --- | --- |
| Generated tokens used for the chart line | 4,069 |
| Generation rate | 13.76 tokens/s |
| Time represented by that line | about 296 s |
| Agent deadline | 300 s |
| Server log: reasoning tokens, empty content | 4,084 at 301 s |
| Answer | never started |

| Original chart axis | Tick labels |
| --- | --- |
| Tokens | 0; 2,000; 4,000 |
| Seconds since the request was sent | 60 s; 120 s; 180 s; 240 s; 300 s |

One logged run: 4,069 tokens at 13.76 tokens/s, about 296 s. The original chart marked where an answer would begin; the answer never started. All 15 runs stopped at about 301 s. November 3.

## Finding 6: What worked

### Grounded answers held up every time

**6 of 6** helper answers used every supplied number and invented none

Given a phone’s battery state, Glimmer explained a paused task using exactly the numbers we supplied, with no invented numbers, in 6 of 6. Our first grader failed all six because its regex expected different wording for the labels; a second, agent-run check confirmed every number and reason. Our one JSON prompt also parsed and matched in 5 identical replays at temperature 0. LM Studio’s strict JSON-schema mode constrains GGUF decoding with a llama.cpp grammar, and our schema fixed every value, so this tests the runtime’s constraint more than the model.

In earlier app use, the coordinator saw it hedge when facts were thin, and once add a “[1]” citation with no source behind it; none appeared in the 6 benchmark answers.

Required tool calls (0 of 3) and an image (0 of 1) were rejected with HTTP 400 by LM Studio’s GGUF path in under 10 ms, before any generation. We did not save the error text, so the cause may be the runtime or chat template rather than the model.

#### Results by check

| Check | Result | Note |
| --- | --- | --- |
| Named the right paused task | 6 of 6 | — |
| Used every number we supplied | 6 of 6 | — |
| Invented no numbers | 6 of 6 | — |
| Added no citation marker | 6 of 6 | — |
| Original label matcher (regex) | 0 of 6 | wording, not wrong numbers |
| Strict-schema JSON, 1 prompt replayed 5× | 1 of 1 | grammar-enforced |
| Required tool call | rejected | HTTP 400 |
| Image input | rejected | HTTP 400 |

November 3 benchmark, same machine. The grounded checks used 6 synthetic phone states. “Rejected” means rejected by the server, so the model was not tested.

## Finding 7: Decisions

### As a decision-maker, Glimmer beat doing nothing but not the best fixed choice

**69%** of picks were one of the simulator’s best options (ties counted)

In an offline test, we asked Glimmer to pick one battery-saving option from a fixed menu, the kind of choice our rules make. In 200 simulated phone states, its pick was one of the simulator’s best options 69% of the time. Always keeping the current policy managed 54.5%. Always raising the battery threshold (battery_up) managed 73.5%.

Glimmer’s picks were no worse than our rule baseline in 95.5% of states and strictly better in 69%. Always keeping the current policy was also no worse in 95.5%, and every fixed choice scored 92%–97%, so that test cannot tell a good decider from a fixed one. In 76 states all five options scored the same, and the simulator’s single “best” label then defaults to keep. That is why an exact-name match gives only 33.5%.

In an early run, 1 answer in 6 decorated the option name with extra words, which broke exact matching. Constrained choice needs exact names.

#### 200 picks compared with the rule baseline

| Compared with the rule baseline | Count of 200 picks | Share |
| --- | --- | --- |
| Better | 138 | 69% |
| Same | 53 | 26.5% |
| Worse | 9 | 4.5% |

#### One of the simulator’s best options (ties counted)

| Choice | One of the simulator’s best options (ties counted) |
| --- | --- |
| Glimmer’s picks | 69% · 138 of 200 |
| Always battery_up | 73.5% · 147 of 200 |
| Always keep | 54.5% · 109 of 200 |

Each pick is one simulated phone state, scored by our simulator. Always keeping the current policy scores 127 better, 64 same and 9 worse on the same test. The set was pre-filtered so every state had at least one option no worse than the baseline. Synthetic, not real batteries. October 27.

## Finding 8: Building

### Glimmer powered the helper; hosted Meta models did most of the coding

**68%** of the model credits in Git went to Muse Spark

Muse Spark was the main author. Its logged code drafts (1,659–3,315 tokens from 21,000–21,700-token prompts) came back in a reported 7–11 seconds; those were different tasks on hosted hardware, so this is not a speed comparison with Glimmer. Muse Spark Open (816B total, 42B active, on Baseten) took over a governor task Glimmer could not finish. Its code and its own tests both still needed fixes for limit precedence and timeouts. Muse Wonder’s reviews flagged rollback and connectivity hazards that other reviewers also raised.

The three hosted models cost $294.26 in total. Glimmer ran locally with no API cost.

#### Model-credited commits

| Model | Model-credited commits | Share of model credits |
| --- | --- | --- |
| Muse Spark | 822 | 68% |
| Muse Spark Open | 200 | 16% |
| Muse Glimmer | 179 | 15% |
| Muse Wonder | 16 | 1% |

#### API spend

| Model | API spend | Share or context |
| --- | --- | --- |
| Muse Spark | $163.27 | 55% |
| Muse Spark Open | $87.95 | 30% |
| Muse Wonder | $43.04 | 15% |
| Muse Glimmer | $0 | ran locally |

Credits come from “written by” lines in Git history; a commit credited to two models counts for both. Spend comes from our own journal, not invoices.

## Five requests for Meta’s model teams

1. **Make reasoning controllable where people run it**

   `reasoning_effort` and `enable_thinking` did not turn reasoning off or reduce it through LM Studio’s OpenAI-compatible server with the Q4_K_M GGUF: none, low and high gave identical token counts, and medium and `enable_thinking=false` reasoned longer (607 and 546 tokens against 508). Map them in the chat template, and return an error when a setting is ignored.

   Findings [1](#finding-1-reasoning), [3](#finding-3-controls).

2. **Give reasoning its own budget**

   Separate limits for reasoning and answer, or a finish reason such as `reasoning_exhausted`, so an empty answer is not mistaken for a normal stop.

   Findings [4](#finding-4-budgets), [5](#finding-5-agents).

3. **Ship a short-answer mode for on-device helpers**

   A short reply now costs about 500 hidden tokens, or 34 s on a 64 GB laptop. A phone assistant should show its first word within a few seconds.

   Findings [1](#finding-1-reasoning), [2](#finding-2-latency).

4. **Publish typical reasoning lengths per task**

   So apps and agents can set honest timeouts. Ours: about 250–700 tokens for a short answer, 150–1,250 for a plan (two runs), 2,300–4,800 for prose, and about 6,900 for a code change even with the design already worked out (a first try without it hit our 12,000 cap still reasoning).

   Findings [2](#finding-2-latency), [5](#finding-5-agents).

5. **Publish a tool and image support table per runtime**

   Which runtimes and quantizations support tool calls and image input, with one working request for each.

   Finding [6](#finding-6-what-worked).

## What we did not test

- **Muse Code:** planned as a builder, but we found no verified run, so we do not rate it.
- **Llama 3.2 1B/3B on the phone:** the optional on-phone route was off in every run here; we never ran it on a phone.
- **Meta’s reference serving stack** and other quantizations: every Mac Glimmer number above is Q4_K_M on LM Studio.
- **Image understanding and native tool calls** on any other runtime.
- **Repeat trials:** repeats of one prompt at temperature 0 returned identical token counts, so they show reproducibility, not independent samples. Most settings rest on 1–3 distinct prompts. Treat these as field observations, not a benchmark.

## How we measured

- Token counts are the server’s own usage fields; times are client wall clock, end to end.
- The November 3 benchmark sent 38 requests in about 12 minutes: 34 completed and 4 were rejected with HTTP 400. Only 19 of the 34 had a visible answer; the other 15 hit a small token cap (64, 192 or 256) while still reasoning and returned empty content.
- Benchmark requests used temperature 0. Repeats of a prompt returned identical token counts (all 9 cap runs, all 9 streaming runs, all 5 JSON runs), so they are replays, not independent samples.
- Other Glimmer runs are saved receipts from real Old Phone OS work, October 25 – November 3.
- Decision results use 200 synthetic phone states scored by our simulator, not real batteries.
- No private code, prompts or hidden reasoning are reproduced here; only counts and timings.

Old Phone OS · Meta Global AI Developer Hackathon 2026 · Every number in this report comes from the project’s saved records; model sizes and coordinator, Hub and provenance figures are as reported, not re-measured.
