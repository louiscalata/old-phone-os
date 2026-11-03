<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# New work and source scope

OPOS was developed for the Meta hackathon during the October 27–November 4, 2026 build period. This repository is a reviewed source snapshot. It does not reproduce the private development repository, request/output archives, spend timelines or full commit history.

The application, setup flow, web interface, rule router, model clients, installation scripts and postmarketOS packaging were developed for this project. The source uses Python's standard library and ships no model weights or inference server. Existing copyright and GPL-3.0-or-later notices are retained; see [THIRD_PARTY.md](THIRD_PARTY.md).

## Contributions

Recorded source contributions include Meta Muse Glimmer, Muse Spark, Muse Spark Open and Muse Wonder, together with implementation and repairs by Codex and Claude and hand-written Grok Bot changes. Codex integrated and verified the setup, UI, package-state and public-export corrections through November 2. On November 3 Claude coordinated the final fixes and reviews, and Muse Glimmer drafted the current README. The project does not claim that Meta models wrote every line. The submitter is responsible for the submission.

Claude's recorded implementation/disclosure contributions are distinct from a later independent UI review whose separate proposal was not adopted merely to create model-written code. Jev pre/post analysis covered one historical Spark task and the November 3 review, drafting and fix tasks; the November 1-2 UI, package, native QA and export checks are not represented as Jev-supervised. See [WORK-BY-MODEL.md](WORK-BY-MODEL.md) and the concise [public provenance](docs/provenance.md).

## Runtime and evidence

Source authorship and runtime routing are separate. The runtime clients target Meta models, with a deterministic offline floor. Development orchestration tools are not runtime dependencies. An endpoint or model-name label does not independently attest the weights a server runs.

Accepted private-baseline evidence includes actual desktop UI checks, one desktop Glimmer response, package-state checks and a postmarketOS QEMU kiosk using a documented QA-only software renderer. On November 3, an aarch64 image built from this tree reached the lock screen and Home in postmarketOS QEMU with the packaged tinydm renderer hook and no manual override; later changes have not been re-booted. The corrected public tree needs its own verification; earlier private counts and captures are not silently relabeled as public-tree acceptance. Native model inference, physical modem behavior and Windows runtime acceptance remain unverified.

Detailed historical prompts and outputs are preserved privately and have not been rewritten. This source snapshot omits those private records while retaining the editable runtime, build, installation, deployment, test, guard and license sources needed to inspect and modify OPOS.
