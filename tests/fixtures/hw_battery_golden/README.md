<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Battery reader golden corpus

`cases.json` records complete returns, escaping exception classes and the
ordered calls made through patched globals and file operations. Expectations
come from executing the unmodified `src/opos/hw/battery.py` Git blob at the
revision in `generated_from`; they are never calculated by a second battery
implementation or maintained by hand.

The two test modules select the `sysfs` and `faults` groups. The shared replay
helper builds a temporary `sys/class/power_supply` tree for each case and
passes that root to the reader. Directory order follows the supplied list,
including deliberately unsorted supply names. File operations refuse paths
outside that case's temporary tree. Permission failures are injected rather
than relying on host permissions. No real power supplies are read.

Numeric values and text become real fixture files. A `hex` object supplies
raw bytes; a `directory` object supplies a directory in place of a file;
`null` omits a file. `helper_faults` override individual reader calls with
specified values or exceptions. `global_faults` inject numeric-conversion
exceptions at a selected call. Fault cases include values that the normal
integer parser cannot return, so they characterize the existing patch seams
as well as ordinary sysfs inputs. Synthetic `BaseException` faults test the
exceptions that escape `Exception` handlers. Exception messages are omitted
because standard-library wording varies between Python versions.

The corpus intentionally preserves unusual current behavior, including
partial state retained after a supply fails and integer truncation before
aggregation. It describes the implementation before a later split; it does
not prescribe new device behavior.

The saved generator is outside the repository. To regenerate into a separate
file for byte comparison, run it with the recorded revision and the same
replay helper:

```sh
python3 <lane folder>/reviews/opos-golden-hw-battery-generate.py \
  --repo . --revision <recorded revision> --output <temporary corpus>
```

The metadata records the source, generator and replay-helper digests. A
legitimate corpus change requires regeneration and review of the new inputs
and captured outputs.
