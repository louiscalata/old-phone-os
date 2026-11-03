<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Why Old Phone OS

OPOS explores how an older phone can keep essential tasks usable while offering an optional local helper. Its rule router uses battery state and a daily goal to defer non-essential work; model advice may pause more work, but cannot override the essential-task rules.

The current web demonstration uses simulated battery and phone services. It is useful for inspecting policy decisions, asking why tasks are paused and trying a fixed-order offline Plan. When a compatible Meta model server is available, the helper can explain and answer through that server. It does not download weights automatically.

The prototype does not establish measured battery savings or reliable real calls on arbitrary phones. Current native evidence is a November 3, 2026 postmarketOS QEMU image built from this tree that booted to the OPOS lock screen with the packaged software-renderer hook and no manual override (see [pmos.md](pmos.md)); an earlier run used a QA-only renderer setting; physical-device and native-model limitations are described in [META.md](META.md) and [../README.md](../README.md).

OPOS is GPL-3.0-or-later. Recorded Meta contributions, Codex and Claude repairs and Grok Bot changes are credited in [../WORK-BY-MODEL.md](../WORK-BY-MODEL.md). Source authorship and runtime model selection are separate facts.
