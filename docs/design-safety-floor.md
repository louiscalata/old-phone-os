<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Rules remain the safety floor in every mode

The Governor first computes the deterministic rule decision. An optional model advisor can suggest additional pauses and explanations, but it cannot enable a task that those rules defer. This applies in NORMAL, SAVER and CRITICAL modes.

For a valid advisor decision, `src/opos/governor.py` intersects its allowed tasks with the rule baseline's allowed tasks. The result can be more restrictive, never less restrictive. It also keeps the baseline mode if the advisor proposes a less restrictive mode.

The Governor rejects malformed task partitions or an advisor decision that drops an essential task, and falls back to the rule decision if the advisor fails validation or raises an exception. Essential tasks are defined by their priority in the supplied task list; this policy is not a guarantee of physical calling or modem service.

The former version of this note said SAVER deliberately allowed the model to re-enable optional work. That rationale is superseded by the current all-mode intersection. Runtime behavior should not be weakened to match the old prose.

The optional model is not bundled, and this policy does not establish an on-phone trained model or native inference. With no usable advisor, the rule baseline remains available. See `tests/test_governor.py` for the existing all-mode subset, validation and fallback checks, and the README for deployment and evidence limits.
