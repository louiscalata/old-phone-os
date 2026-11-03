<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OPOS on postmarketOS

## What this is

`postmarketos-ui-opos` selects the OPOS Cage/Chromium kiosk. The source checkout contains both package recipes and their service/session inputs. These are QEMU development instructions, not hardware flashing or a verified stock-restore guide.

## Build

Use an isolated Linux builder with Python 3 and the official pmbootstrap toolchain. The accepted private baseline used pmbootstrap 3.12.1 at `caef2d0218c9aa9953359d66b479c1201a63a370` and a complete pmaports checkout at `8ed0110d9a7a7351b80ba9e6b6bdda4520980a0f`. Those are toolchain identities, not OPOS source revisions. Package repositories and tool availability can change; record the actual resolved versions and input hashes for a new build.

Prepare a separate complete pmaports checkout before staging; do not replace it with an overlay-only directory:

```sh
git clone https://gitlab.postmarketos.org/postmarketOS/pmaports.git ../opos-pmaports
git -C ../opos-pmaports checkout --detach 8ed0110d9a7a7351b80ba9e6b6bdda4520980a0f
```

Install pmbootstrap from its official source/version using its documented Linux prerequisites. Verify `pmbootstrap --version` is the intended version. From a clean OPOS source checkout or extracted full source archive:

```sh
export PMAPORTS_CHECKOUT="$(cd ../opos-pmaports && pwd)"
COPYFILE_DISABLE=1 OPOS_LOCAL_SOURCE=1 sh packaging/pmos/stage-aports.sh "$PMAPORTS_CHECKOUT"
pmbootstrap config aports "$PMAPORTS_CHECKOUT"
pmbootstrap config device qemu-aarch64
pmbootstrap config ui opos
pmbootstrap init
pmbootstrap checksum opos postmarketos-ui-opos
pmbootstrap build opos
pmbootstrap build postmarketos-ui-opos
pmbootstrap install
```

During init confirm the intended QEMU device, `opos` UI and OpenRC. Choose `qemu-amd64` for an appropriate x86 builder instead of the ARM example. Normal install creates a private image; do not add disk/flasher arguments. No password is supplied on a command line in this guide.

`OPOS_LOCAL_SOURCE=1` is essential for an unpublished checkout. Staging copies the two recipes into that complete pmaports tree, builds the declared runtime input, removes only its remote archive URL in the staged recipe and measures the actual staged checksum. It leaves the caller's recipe/source unchanged. `pmbootstrap checksum` then verifies/refreshes those staged inputs. This flow needs neither private Git history nor coordinator configuration.

There are two source assets: `opos-runtime-source-20261102.tar.gz` contains the declared runtime files plus LICENSE and THIRD_PARTY; `opos-source-20261102.tar.gz` is the complete source handoff with editable build/install/deployment scripts and tests. The small runtime input alone is not the complete corresponding source. Runtime input ordering/ownership/timestamps are normalized by `make-runtime-source.py`; release manifests record actual hashes. Do not infer bit-identical APKs across different toolchains from source equality.

The public remote-input recipe names a proposed release asset and measured runtime checksum. Its URL is usable only after the owner publishes the exact asset. Local staging is the supported prepublication route. Never substitute a private development commit into a public clone/archive URL or invent a future GitHub-generated checksum.

## Try it in QEMU

```sh
pmbootstrap qemu --image-size=2G -m 2048 --no-gl
```

The tool's default SSH forward is loopback port 2222; use the account chosen during installation. `sh packaging/pmos/qemu-demo.sh` only prints the corresponding plan, and `--help` shows usage; it does not build, connect or flash anything.

The non-GL virtual GPU fails under the default GLES2/GBM renderer, and tinydm starts the cage compositor directly, so the renderer must be chosen before cage runs: `/etc/cage-ui/opos.sh` is only sourced later by cage's own session child. The package therefore installs `/usr/share/tinydm/env-wayland.d/50-opos-renderer.sh`, which tinydm sources before it starts cage and which sets `WLR_RENDERER=pixman` unless a value is already set; a device with working accelerated drivers can set `WLR_RENDERER` explicitly. On November 3, 2026 an aarch64 QEMU image (QEMU 9.1.2, TCG, OpenRC, no manual renderer override) built from this tree booted to the OPOS lock screen about 70 seconds after start, and Unlock reached Home; the same image without this hook stayed on the console with the GLES2/GBM failure. That is a virtual-machine result, not a physical-phone or accelerated-rendering result. Do not weaken the Chromium sandbox or host protections.

## Limits

The earlier QA-only Pixman accounts retained in [META.md](META.md), [WHY.md](WHY.md) and [notices-verification.md](notices-verification.md) are historical observations, separate from the November 3 packaged-hook QEMU boot above.

These instructions do not flash hardware. Current physical-phone/modem behavior is unverified; an earlier Surface Duo installation report concerned Droidian, not a supported final postmarketOS port.

Native AI is optional and unverified in the accepted isolated QEMU profile: no backend listener or authorized computer route existed and free memory was below the local-model minimum. Its critical-battery Ask displayed rules behavior. Do not equate that with the separately observed desktop Glimmer response.

A new corrected source/package needs its own staged/build checks and exact runtime comparison. Old private test counts or framebuffers are not fresh verification of changed artifacts. Full system-image redistribution additionally needs an exact component/license/source inventory; this source export does not establish one.
