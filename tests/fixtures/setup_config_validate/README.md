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

The worker compiles the actual source bytes after the hash seed is active.
Its source loader bypasses bytecode reads, including valid caches compiled
from the same source. Disabling bytecode writes alone does not prevent
those reads: serializing and loading a compiled frozenset can change its
iteration layout. The isolated module retains its file, loader, spec and
module identity. Exact ordering here describes this seeded, freshly
compiled replay context; it is not a universal ordering guarantee for
production sets. The cache regressions use only private source copies and
leave both source and cache bytes unchanged during replay.

The `recursive_dict` and `recursive_list` cases also have exact Python 3.11
overrides for seeds `0`, `17`, and `123`. These messages were observed on
CPython 3.11.18 in the original CI run `43974643713`, job `133675359039`,
for head `8fd3bc8875cb5b00ee9b8b6449f81878c94b747b` (checkout merge
`7bc414d70b1929f395002381dc41399178b62bf4`). The dictionary reported
`maximum recursion depth exceeded while calling a Python object`; the list
reported `maximum recursion depth exceeded while getting the str of an object`.
Their exception class, ordered calls and input-preservation observations
match the primary receipts. These are original hosted CI observations,
not a local Python 3.11 recapture. The existing `expected_by_python`
selector chooses these complete receipts by interpreter minor version;
no message is stripped, normalized or omitted. Fresh CI on Python 3.11 and
3.13 must verify the resulting change. A new message difference requires
diagnosis rather than another accepted message or a weaker assertion.

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

Run the replay, golden and ordering modules with the application on the import path:

```sh
PYTHONPATH=src python3 -m unittest \
  tests.test_setup_config_validate_golden \
  tests.test_setup_config_validate_ordering \
  tests.test_setup_config_validate_replay
```

Recapture outcomes with the repository's worker, checking the source
SHA-256 against `generated_from.sha256` before recording expectations:

```sh
PYTHONHASHSEED=0 python3 tests/fixtures/setup_config_validate/support.py \
  --source src/opos/setup/config.py \
  --cases tests/fixtures/setup_config_validate/cases.json --seed 0
```

Repeat with seeds `17` and `123` set in both the environment and argument.
Compare each complete outcome with its selected receipt using Python 3.13
as the primary interpreter and installed Python 3.12, 3.14 and 3.15 as
references. Preserve the original inputs, primary receipts, provenance
and existing overrides when adding another observed interpreter variant.

## Re-verification after the LOU-84 refactor

`src/opos/setup/config.py` was refactored without a behaviour change
(LOU-84: `validate` split into ordered check helpers). No receipt,
input, override or recorded message was changed. Only
`generated_from.sha256` now names the refactored source. The receipts
themselves still come from revision `1eddeb87…` (`generated_from.revision`
is unchanged). Before updating the hash, the complete golden and ordering
replays (580 cases under seeds `0`, `17` and `123`) passed unchanged
against the refactored source on Python 3.15. CI then re-checks them on
the hosted interpreters. Any future outcome difference still needs a
full recapture and diagnosis.
