<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Lean by design

Focused package inputs; performance gains remain unmeasured.

The OPOS image keeps its direct dependencies and installed OpenRC service scripts
limited to these essentials. Base packages bring their own dependencies; this is
not a complete inventory of transitive packages or enabled system services.
Adding a package or service requires the owner's approval and an accompanying
update to this document and `tests/test_lean_image.py`.

| Essential | Why it is there |
| --- | --- |
| python3 | Runs the OPOS server and setup tools. |
| modemmanager | Provides modem control for phone connectivity. |
| polkit | Provides permission rules for modem access. |
| dbus | Provides communication with system services such as ModemManager. |
| postmarketos-base-ui-cage | Provides the base Cage kiosk UI environment. |
| opos | Installs the OPOS assistant, setup tools and service scripts. |
| chromium | Displays the OPOS web interface in the kiosk. |
| psmisc | Provides `killall`, which the postmarketOS kill-pbsplash boot step needs to close the boot splash. |
| opos-server | Runs the OPOS web server as the dedicated OPOS user. |
| opos-firstboot | Runs first-boot setup tasks. |

## Optional init integration

The optional `opos-systemd` subpackage declares
`systemd $pkgname=$pkgver-r$pkgrel` explicitly and supplies
`opos-server.service` and `opos-firstboot.service`. Its automatic selection
remains conditional on `install_if="$pkgname=$pkgver-r$pkgrel systemd"`.
The default guide uses OpenRC: neither top-level OPOS/UI dependency set gains
systemd, the OpenRC dependency remains `openrc $pkgname=$pkgver-r$pkgrel`,
and no new OPOS service names or UI recommendations are introduced.

The main package also carries a fixed state-validation helper for
installed-mode use; this is additional file payload, not a running daemon.
This optional init route does not broaden the direct dependency inventory above. Validation covers
source and staging checks; actual APK installation and systemd startup remain unverified.
Actual package/image size and performance overhead are unmeasured. No formal
`opos-bench.sh` measurements or measured performance improvements are recorded here.
The separate November 3 QEMU boot observation is documented in [pmos.md](pmos.md);
it is not a formal boot benchmark.

## What is not installed

No app store, no preinstalled apps beyond the essential OPOS kiosk interface,
no telemetry, no cloud account, and no model weights.

## How we measure

Numbers come only from `tools/opos-bench.sh` runs. Results are labelled
QEMU or device with date and image, and are never estimated. Record the image
identifier alongside each result's target. No formal `opos-bench.sh` measurements
are recorded in this table yet; the separate QEMU observation above does not fill it.

| date | target (QEMU/device) | boot_to_login_s | idle_ram_mb | idle_cpu_pct | pkg_count | enabled_services |
| --- | --- | --- | --- | --- | --- | --- |
