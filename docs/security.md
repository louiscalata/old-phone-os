<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Security: threat model for Old Phone OS Interface

This is a historical Droidian and security-branch assessment. Its "fixed" statuses and test counts describe that branch-era review; they are not fresh security acceptance of this source export or a physical phone. See [README.md](../README.md) for current validation scope.

## Scope
Old Phone OS Interface runs on a Droidian phone as a Python stdlib-only layer with a local web UI at 127.0.0.1:8765 shown in a kiosk browser plus SMS, call, contact, modem, and install code. Developer machine tooling, keys, and spend logs are out of scope except secrets or personal data committed to git.

## Assets
- SMS bodies and phone numbers in `~/.local/state/opos/messages.json`
- Contacts and call log on the single-user phone
- Phone control: sending SMS and placing calls via ModemManager
- Install payload copied to the phone
- Device login whose default droidian password is publicly known
- Developer API keys, never stored on the phone

## Actors and trust boundaries
- Other apps and processes on the phone
- Any browser web page on the phone reaching 127.0.0.1:8765 via cross-site request or DNS rebinding
- Network attackers on Wi-Fi if SSH or a port is exposed
- Senders of malicious SMS text
- Anyone with USB or physical access
- Tampered install payload suppliers
- Developer mistakes putting secrets or personal paths in git history
- Boundary: Browser tab -> local server
- Boundary: Server -> modem and files
- Boundary: Install machine -> phone

## Top risks, ranked
| # | Risk | Likelihood | Impact | Fix status |
|---|---|---|---|---|
| 1 | Forged or hijacked web actions (cross-site POST or DNS rebinding to 127.0.0.1:8765, including forged incoming SMS via receive) | High | High | fixed in security branch |
| 2 | Default droidian password and SSH reachable over Wi-Fi | High | High | owner action |
| 3 | Tampered install payload (unverified scp/adb push) | Medium | High | fixed in security branch |
| 4 | Tampered or swapped model file | n/a | n/a | removed: the governor is now code (src/opos/router.py), there is no model file to tamper with |
| 5 | Untrusted SMS text (script injection in the UI, log leakage, oversized text) | High | Medium | fixed in security branch |
| 6 | Plaintext messages on an unencrypted disk | Medium | High | owner action |
| 7 | Unsandboxed services (kiosk and server run with full user privileges) | Medium | Medium | fixed in security branch |
| 8 | Web server DoS (huge body, slow clients, unbounded threads) | Medium | Medium | fixed in security branch |
| 9 | Secrets and personal data in git history | Medium | High | fixed in security branch |
| 10 | Supply chain of tooling (pip packages, spend keys) | Low | Medium | partly: stdlib-only code, guard scans; pip-audit not run |

## Fixes and the tests that prove them
- Web server (`src/opos/web.py`, `src/opos/secure.py`) binds 127.0.0.1 only and refuses other hosts, checks a per-process session token in a meta tag and `X-OPOS-Token` header, allow-lists Host and Origin, accepts JSON Content-Type only, caps bodies at 4096 bytes with digit-only Content-Length, where a negative, non-numeric or oversized Content-Length gets 400 or 413, and out-of-range or overflowing numbers get a JSON 400 or an error status, never a dropped connection, sends nonce CSP with `X-Content-Type-Options` and frame denial and no-store, returns 405 with Allow and JSON error pages, rate-limits per action with string length bounds, uses a 10 s socket timeout and a 15 s whole-connection deadline, daemon threads, request queue 16, and at most 32 concurrent connections (extra ones are closed), accepts forged receive SMS only on the simulated phone with 403 on the real device phone, and uses data-go listeners instead of inline onclick handlers. Tests: `tests/test_web_security.py` (27), `tests/test_secure.py` (16), token headers in `tests/test_web.py` and `tests/test_web_battery.py`.
- SMS text (`src/opos/essentials.py`) strips control characters and caps stored text at 1600 and log text at 500, and the UI `esc()` escapes quotes and angle brackets. Tests: `tests/test_essentials_security.py` (18).
- Install integrity (`deploy/make-manifest.sh`, `deploy/verify-manifest.sh`) records SHA256SUMS over `src/`, `deploy/`, and `LICENSE`, fails unlisted or modified files, and the operator pins the manifest hash. Tests: `tests/test_manifest.py` (29).
- systemd (`deploy/opos-server.service`, `deploy/opos-sysusers.conf`, `deploy/50-opos-modemmanager.rules`) runs as a per-user unit rendered from `deploy/opos-server.service` with NoNewPrivileges, ProtectSystem=strict, private tmp, an empty capability set, and a syscall filter; the system-unit form has not been hardened the same way yet. The kiosk drop-in `deploy/opos-kiosk.service.d/10-hardening.conf` applies to a kiosk unit that is not shipped in this repository. Tests: `tests/test_deploy_hardening.py` (12).
- Device runbook (`deploy/harden-device.sh`) defaults to check and changes nothing, requires root and the typed word yes-apply for `--apply`, backs up every changed file and restores it with `--rollback`, covers SSH key-only login without root login with AllowUsers and a new password prompt, covers an nftables inet opos_filter table with default drop inbound and SSH only on usb0 and rndis0, turns avahi and cups and rpcbind off, applies Debian security updates only with sysctl hardening, and reports FDE read-only. Tests: `tests/test_harden_device.py` (15) against a fake root.
- Repo scanning (`guard/historyscan.py`) scans every commit on all branches for secrets and personal paths and LAN addresses, and `tests/test_no_unsafe_code.py` (4) fails on eval and exec and pickle and shell=True and os.system and on logging calls with arguments named text, message, body, or number. Tests: `tests/test_historyscan.py` (16). `ci/security.sh` runs the full suite, the security tests three times, guard, history scan, and shellcheck, ruff, bandit, and semgrep only if installed.

## Residual risks
- Other apps on the phone can still read files of the same user.
- `messages.json` stays plaintext.
- The kiosk browser is not in private mode.
- Full-disk encryption is unverified.
- The manifest itself has no signature, only a pinned hash.
- The loopback token is readable by any process of the same user via HTTP GET.

## Open findings
| file:line | finding | fix |
|---|---|---|
| src/opos/mm_modem.py:329, 357, 441-452 | `GdbusBus` raises `OSError` with gdbus stderr, and `send_sms` passes that text on; it may echo message text | never log them |

The same review flagged code that is not in this repository: an install script (no pipefail; copies without a checksum), a kiosk launcher and unit (inherited `PYTHONPATH` and `OPOS_HOME`; no sandboxing), a kiosk browser profile under `/tmp` instead of private mode, plaintext `messages.json`, and a receive path on the real device phone that is safe only because it is not wired.

## Not done by this review
These steps change the phone, so the review left them to its owner:
- Running `deploy/harden-device.sh --apply` on the Duo, after adding an SSH public key (`--add-key`) so key-only login does not lock the owner out.
- Choosing a new device password.
- Installing nftables and unattended-upgrades (`--install-packages`) and the optional scanners (bandit, semgrep, pip-audit, ruff, shellcheck).
- Installing the systemd units, sysusers file and polkit rule on the phone.
- Enabling full-disk encryption. Feasibility is unverified; it is likely not possible without reflashing, since the Duo boots from RAM.

## How to re-check
- `ci/security.sh`
- `python3 guard/historyscan.py`
- `deploy/harden-device.sh --check`
