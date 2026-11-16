# Setup factory golden corpus

`cases.json` records inputs and observations from the unmodified revision named
in `generated_from`. No response, exception or expected call sequence was
written by hand. Its input fields define the boundary cases. `record.py` reads
those inputs without their expectations, loads production code from an archive
of that revision, verifies the source hash and import location, and records the
observations through the current fixture driver. It uses local Git objects and
does not fetch anything.

The two test modules replay HTTP cases and factory/connection lifecycle cases.
`support.py` drives the real handler with in-memory sockets. It records numeric
HTTP status, ordered stable headers, exact body bytes encoded as base64,
ordered calls at the patched seams, raised exception types/messages, remaining
connection slots, stderr and files beneath a fresh temporary state directory.
The published progress state is observed after construction and each request,
including whether the synthetic apply has finished. Date and Server response
headers are omitted because their values depend on the runtime.
Paths are substituted only in recorded call arguments, never in response or
file bytes.

For an unsupported method with no `do_METHOD` handler and a 501 status, the
standard library owns the error page. Its body and Content-Length are omitted;
status, remaining stable headers, exceptions and the ordered call sequence stay
recorded. The calls establish that setup dispatch and its security checks were
not entered. Factory-produced response bytes remain exact. Separate tests vary
the stdlib error template and ambient release metadata to check isolation.

Clocks, entropy, server construction, scheduling, configuration discovery,
device discovery and apply execution are controlled. Security checks, request
parsing, response rendering and preference persistence execute their real code.
The synchronous apply substitute records all forwarded context arguments and
writes a synthetic `applied.json`; it does not install anything. Deferred apply
cases preserve the in-flight state. Synthetic `FixtureAbortError` values test
that narrow exception catches propagate process-control-like exceptions without
raising an actual interrupt or exit.

The page body is recorded in full with fixed session and nonce values. The
notice version and build are pinned to `0.1.0` and `unpublished`, isolating the
factory from release metadata and environment differences. Rendering stays
real. If a legitimate page change requires new bytes, record a new corpus from
the intended unmodified baseline and explain the page change in the test
comment. Do not edit expected bytes or digests manually.

Run the new tests from the repository root with a private temporary directory:

```sh
PYTHONPATH=src python3 -m unittest tests.test_setup_server_golden tests.test_setup_server_lifecycle_golden
PYTHONPATH=src python3 -m tests.fixtures.setup_server_golden.record --check
PYTHONPATH=src python3 -m tests.fixtures.setup_server_golden.check --output "$TMPDIR/setup-golden-check.json"
```

Set `TMPDIR` to an existing private directory before running these commands.
To replace expectations, run `record` without `--check`; `--revision REV`
selects another explicit baseline. Add or change input fields in `cases.json`
before recording. An expectation mismatch requires investigation before
regeneration.

`check.py` refuses real socket and subprocess operations while driving the
factory. Its focused mutations restore request logging, admit POST to each
GET-only API route, accept `/index.html`, remove progress-state publication or
publish a detached holder. It requires every mutation to differ from the
corpus, checks unmodified controls before and after mutation, and compares the
pinned and current factories on the corpus plus deterministic extra inputs.
The mutation catalog requires the recorded production source. These focused
mutations are regression checks, not a comprehensive mutation-coverage claim.
