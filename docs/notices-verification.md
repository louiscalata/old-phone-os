<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Notices verification

`src/opos/notices.py` is the authoritative shared text; `docs/NOTICES.md` is generated with `PYTHONPATH=src python3 -m opos.notices --markdown`. This record separates what was checked by reading the current code from earlier, historical observations. It does not certify the base system's network behavior or image redistribution completeness.

## Privacy

| Claim | Source evidence | Scope |
|---|---|---|
| Old Phone OS Interface sends no telemetry or analytics. | Network clients are explicit in `src/opos/muse.py`, `src/opos/assist.py`, `src/opos/monitor.py`, `src/opos/setup/selftest.py`, `src/opos/setup/apply.py` and `src/opos/aitools/installer.py`. `src/opos/aitools/sessions.py` uses local stderr diagnostics; `src/opos/notices.py` imports no network library. | Static code inspection; not a packet capture of third-party programs. |
| The phone and setup pages are loopback-only; the public demo server listens on the network. | `src/opos/secure.py` validates loopback bind addresses; constructors in `src/opos/web.py` and `src/opos/setup/server.py` apply it. `src/opos/public_demo.py` uses its own server constructor with a `0.0.0.0` bind and exact allowed-host checks. | The desktop/kiosk and setup services are loopback-only; the separate public simulation is network-accessible. This does not describe arbitrary system software. |
| By default the Helper uses its built-in rules and programs on this phone only. | Default route construction in `src/opos/muse.py` and `src/opos/assist.py` uses loopback; `src/opos/router.py` is deterministic. LAN and cloud require explicit configuration. | A configured remote deployment is a separate opt-in. |
| Local phone and setup code sends nothing off the phone unless you opt in to Wikipedia search, pointing the Helper or the Inference page at a computer you own on your private network, paid cloud use with your key, or coding tool installation. | `src/opos/assist.py` gates Wikipedia per question and validates LAN routes; `src/opos/muse.py` gates the hosted route; `src/opos/monitor.py` sends nothing off the phone unless `OPOS_AGIW_URL` is set; it always asks the phone's own loopback model port (`GET /v1/models` on `127.0.0.1:8080` by default, loopback only, on every uncached poll while the page is open); with `OPOS_AGIW_URL` set it also reads from a loopback or private-network address it has checked (`GET /api/snapshot`, read only); `src/opos/setup/config.py`, `src/opos/setup/apply.py` and `src/opos/aitools/installer.py` gate selected tool installation. | Applies to OPOS code; installed third-party tools have their own behavior. |
| Hosted demo: entered numbers, messages and questions go to its server. Use fictional information. | `src/opos/public_static/public-demo.html` discloses hosted processing; `src/opos/public_demo.py` routes simulated call, message and helper requests to its HTTP handler. | Hosted demo inputs reach the server. The deployment environment and retention have not been verified; see [public-demo.md](public-demo.md) for the conditional call-log persistence limit. |
| System software, including postmarketOS and Chromium, may use the network independently of OPOS. | The kiosk starts Chromium; the platform packages include independently maintained services. | No claim that a static OPOS scan controls the full operating system. |

## Tested devices and configurations

| Claim | Evidence | Scope |
|---|---|---|
| Physical phones, including Surface Duo, are untested for this corrected source export. | Earlier developer install and self-test reports concerned an older source snapshot. They are not published and do not show that this source, the modem or calling works on a phone. | No physical-device claim for this source. |
| A November 3 build booted to Home in postmarketOS QEMU with software rendering; that does not validate physical devices. | The unpublished November 3 boot record summarized in [pmos.md](pmos.md): QEMU TCG aarch64, with the packaged tinydm renderer hook and no manual override, reached the lock screen, then Home after Unlock. Without the hook the same image stayed on the console with the GLES2/GBM failure. An earlier run only rendered after the Pixman renderer was set by hand for testing. | Virtual machine only; packages changed after that build need a fresh boot; native model inference was not established. |

## Source, contact and version fields

- SOURCE_URL is the plainly displayed project address `github.com/louiscalata/old-phone-os`, consistent with the prospective public repository in README. Displaying this text makes no network request. The release owner must verify the actual published destination and corresponding-source archive before distributing binaries.
- CONTACT refers readers to the project repository without inventing an email, issue-channel configuration or promised support service.
- RESTORE_STOCK remains unpublished because no verified stock-restore procedure is supplied. [The historical Duo notes](duo-usb-install.md) contain rootfs, recovery and sparse-image flashing instructions, and `packaging/pmos/easy/easy-install.sh --flash` checks the image and then refuses without issuing an erase or flash command; those paths remain unverified for this corrected source and are not a verified stock-restore procedure.
- VERSION remains synchronized with `packaging/pmos/opos/APKBUILD`; a missing generated build identity is honestly shown as unpublished. Each release should record its actual source and package identities.
- THIRD_PARTY follows platform dependencies. `THIRD_PARTY.md` separately links model terms; no weights or inference server are bundled. A full system image needs its own exact package/license/source inventory.

`tests.test_notices` verifies shared-text parity, complete fields, source-address consistency, escaping, privacy patterns and the absence of network imports. `tests.test_setup_notices` exercises the setup presentation. Neither test accepts licenses or proves native hardware behavior.
