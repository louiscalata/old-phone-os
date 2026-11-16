# Setup configuration validator goldens

`cases.json` records complete `opos.setup.config.validate` outcomes from
revision `1eddeb87dc3aa0025d3f29ffe497e8feb322d4f4`. Its provenance includes
the SHA-256 of `src/opos/setup/config.py`. Expected values are generated
by running that unchanged source, including escaping exception types and
messages, ordered global calls, and observed input state.

Each case contains an input descriptor and exact expectations for hash
seeds `0`, `17`, and `123`. Replay starts a fresh interpreter with the
requested seed. Error lists are never sorted. Python 3.13 is the primary
recording runtime; separately generated overrides preserve observed set
iteration differences on Python 3.14 and 3.15. Python 3.12 recordings match
the primary expectations. Other runtimes use the primary expectations
unless a recorded override exists.

`support.py` restores tagged bytes, tuples, nonfinite floats, non-string
mapping keys, cycles, and explicitly scripted string subclasses. Use the
`$dict` pair-list descriptor to represent a literal dictionary containing
a reserved tag key. Scripted strings retain their behavior through slices
and stripping so unusual `split`, `startswith`, and hashing behavior can
exercise accepted subclass paths. Their method cursor is part of the
input-state snapshot.

The replay wraps `default_config`, `re.fullmatch`, and `math.isnan` at the
configuration module boundary. Calls retain their original implementation
unless the case explicitly injects a return or exception. The validator
has no filesystem, clock, subprocess, device, or service operation.
Recursion is bounded consistently in the replay worker; cyclic inputs
record their escaping `RecursionError`.

The recorded implementation has no dedicated `jaryo` block: such a key is
unknown, and its contents are still visited by the forbidden-token scan.
Long inputs and diagnostics remain complete; no truncation is introduced.

Run the two test modules with the application on the import path:

```sh
PYTHONPATH=src python3 -m unittest \
  tests.test_setup_config_validate_golden \
  tests.test_setup_config_validate_ordering
```

The saved generator is kept outside the repository at
`<lane folder>/reviews/opos-golden-setup-config-validate-generate.py`.
It accepts `--worktree`, `--output`, and repeated `--reference-python`
arguments. Regenerate with Python 3.13 as the primary interpreter and
Python 3.12, 3.14, and 3.15 as references, in that order, to reproduce the
corpus bytes. The generator checks source identity against the pinned
revision before recording any expectation.
