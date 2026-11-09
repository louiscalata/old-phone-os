<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Development contributions

This is a role summary for the exported source, not a count of lines, effort or commits in the new public repository. Detailed private commit lists, prompts and job records are not published here.

| Contributor or tool | Recorded role and scope |
|---|---|
| Meta Muse Glimmer, Muse Spark, Muse Spark Open and Muse Wonder | Historical source generation and scoped development contributions recorded in the private evidence ledger. On November 3 local Muse Glimmer 30B drafted the current README and its opening (both edited by Claude) and the submission text, and wrote the demo-goal and battery-empty changes in src/opos/web.py from a design Claude specified in a coached retry, with Claude's edits. Glimmer drafts requested through the AGIW Nisi workflow returned no output (the model's reasoning used the whole request time); those tasks were written by Claude. Model-name metadata is not independent served-weight attestation. |
| Codex | Repository integration, setup and UI repairs, package-state fixes, deterministic verification and public-export preparation through November 2. On November 3 Codex (gpt-6.1-sol and gpt-6-astra) reviewed the source for defects, rules fit, privacy and packaging, and wrote the postmarketOS packaging fixes, the setup preference-directory and wording fixes, and the Windows installer fixes. |
| Claude | Recorded implementation, security-test and disclosure work, plus independent review. A later UI-review proposal was not adopted merely to claim code authorship. On November 3 Claude (Opus 5.5) coordinated the final round, wrote the home-status, stylesheet, service re-run and documentation fixes, and Claude (Fable) wrote the helper-facts, demo-start, advisor, route and plan-parser fixes, the UI polish and the postmarketOS renderer hook, checked the install path, and booted the package image in QEMU. Claude also wrote the model-check probe and the guard and docs wording after Glimmer drafts through the AGIW Nisi workflow returned no output. |
| Grok Bot | Recorded hand-written UI/guard changes and development coordination. On November 9, 2026, Grok 4.7 in Cursor prepared the v0.2.0 release candidate (docs aligned to the easy-setup scripts, version 0.2.0, CI checks). No Meta model wrote that change. |
| Jev | Pre/post analysis for one historical Spark paragraph task and for each November 3 review, drafting and fix task. No Jev coverage is claimed for the November 1-2 UI, packaging, native QA or clean export. |

The source includes adopted non-Meta work. It is inaccurate to describe it as entirely Meta-written. Historical marker counts from an older private branch exclude later integration work and are not presented as totals for this public snapshot.

Runtime integrations are listed in [docs/META.md](docs/META.md). Some Meta model families appear in both development records and runtime routes; a development credit does not prove a particular native runtime was tested. OPOS does not ship model weights, development orchestration processes or private execution records.

## Names used above

- Opus 5.5 and Fable: Anthropic Claude models.
- gpt-6.1-sol and gpt-6-astra (Sol, Astra): OpenAI models run through the Codex CLI.
- Jev: a TypeSafe model that returns typed judgments, used only for the advisory pre/post analysis described above.
- AGIW Nisi workflow: the developer's own local development tool that routed drafting requests to models (here local Muse Glimmer 30B) and recorded the results. It is not part of OPOS or a runtime dependency.
