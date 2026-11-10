<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Third-party notices

Old Phone OS Interface is GPL-3.0-or-later (see LICENSE).

## Code

No third-party code is bundled. The software uses only the Python standard library (Python 3.11 or newer); `requirements.txt` and `guard/deps.json` list no packages.

`src/opos/sounds/alarm.wav` is a 0.4 second tone made for OPOS. It is released CC0. It is not taken from another project.

## Models

OPOS ships no model weights. It talks over HTTP to model servers that you run or opt into. The models it is written for carry their own licenses:

| Model | Used for | License |
|---|---|---|
| Meta Llama 3.2 1B / 3B | Tier 0 on-device answers through a llama.cpp server you start | Llama 3.2 Community License |
| Muse Glimmer 30B (meta-models) | helper routing, Why, Plan and Ask on a computer you own | Apache-2.0 |
| Muse Spark Open | optional hosted model, off unless you set OPOS_SPARK_OPEN=1 with your own key | the hosting provider's terms |

### Llama 3.2

Llama 3.2 is licensed under the Llama 3.2 Community License, Copyright © Meta Platforms, Inc. All Rights Reserved.

OPOS does not bundle Llama weights or other Llama Materials. Llama 3.2 has its own Community License and incorporated Acceptable Use Policy. Section 1(b) covers distributing or making available Llama Materials or derivatives, and products or services containing them. Its requirements include providing the agreement with the Materials, prominently displaying “Built with Llama,” and retaining the required attribution notice in distributed copies. Consult the complete agreement for its other conditions: [Meta Llama 3.2 license](https://raw.githubusercontent.com/meta-llama/llama-models/main/models/llama3_2/LICENSE).

### Muse Glimmer

Muse Glimmer 30B's published license is Apache-2.0. Meta also publishes a model-specific usage policy. OPOS does not bundle its weights or inference server; consult the upstream documents for the model you deploy: [model LICENSE](https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/LICENSE) and [usage policy](https://huggingface.co/meta-models/Muse-Glimmer-30B/raw/main/USAGE_POLICY.md). The separate usage-policy document is not described here as text incorporated into the Apache license.

## Development tools

Development contributions and reviews are credited in NEW-WORK.md and WORK-BY-MODEL.md. Those credits are separate from the runtime integrations listed above. Some Meta model families appear in both. Development orchestration tools and private execution records are not bundled with OPOS.
