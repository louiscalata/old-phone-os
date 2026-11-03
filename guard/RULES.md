# Local repository checks

The optional hooks in `.githooks/` run `guard/check.py` when configured with `git config core.hooksPath .githooks`. These are project-maintained static checks; they cannot certify eligibility, rights, model identity or compliance with an event's official rules. The source includes adopted work written by non-Meta models; that mixed authorship is disclosed in [WORK-BY-MODEL.md](../WORK-BY-MODEL.md).

| Local check | What it examines |
|---|---|
| Commit window | Recorded author/committer dates against the configured event window. |
| Provenance | A `Provenance:` trailer and matching recorded model/contribution entry. Existing mixed-author exceptions are part of the implementation; a model name is not proof of sole authorship. |
| Runtime routing text | Non-Meta names and URL hosts in selected runtime/deployment/install sources. It does not observe actual network traffic or server weights. |
| Source overlap | Configured historical source roots, when a private `guard.json` exists. Missing configuration is reported; no public export contains private root paths. |
| Privacy and secrets | Static patterns for credentials, private paths/addresses and configured identifiers. A clean scan is not a guarantee that all sensitive content was found. |
| Dependencies and licensing | Declared dependency-license labels in `guard/deps.json`, GPL markers in `LICENSE`, SPDX headers, and the presence of `NEW-WORK.md`; it does not substantively audit notices or source scope. |
| Artifacts | Disallowed device/vendor blobs and oversized tracked files. |

The public snapshot keeps a concise [provenance summary](../docs/provenance.md); private prompts and full execution history are excluded. A new parentless export needs its own truthful aggregate provenance trailer. Do not borrow a task identity or assign a false writer merely to satisfy a parser.

Before submission the owner must separately review the actual participant guide, portal fields, team/rights declarations and final published source/video. See the [participant guide](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/details) and [submission portal](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit). No hook accepts legal terms or submits an entry.

## Commit messages

`Provenance: <id>` must be in the final trailer block, directly above `Co-Authored-By` with no blank line between them. Otherwise `git interpret-trailers` and the guard will not see it. Replace the placeholders in this six-line template with the change's truthful provenance ID and co-author:

```text
Describe the change

Explain its scope and checks.

Provenance: <id>
Co-Authored-By: Name <email@example.com>
```

Every commit needs its own truthful row, or must be folded into an aggregate commit whose truthful row covers everything squashed. Never borrow an ID.

The `uiux-*`/`fix-F*` exceptions in `check.py` cover private-history IDs; no commit or row in this snapshot uses them.
