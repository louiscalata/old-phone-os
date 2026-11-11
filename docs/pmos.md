<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OPOS on postmarketOS

## What this is

`postmarketos-ui-opos` selects the OPOS Cage/Chromium kiosk. The source checkout contains both package recipes and their service/session inputs. These are QEMU development instructions, not hardware flashing or a verified stock-restore guide.

The kiosk's `opos-server` service runs `python3 -m opos.web`, the same server as the desktop demo, so the kiosk uses the same simulated battery (`DrainSim`) and simulated modem (`ScreenModem`). It does not read the phone's battery, place real calls or send real texts. The service is ordered after ModemManager when ModemManager runs, but OPOS does not use it yet: the ModemManager adapter in `src/opos/mm_modem.py` is not connected to the kiosk.

## Build

Use an isolated Linux builder with Python 3 and the official pmbootstrap toolchain. Earlier development builds used pmbootstrap 3.12.1 at `caef2d0218c9aa9953359d66b479c1201a63a370` and a complete pmaports checkout at `8ed0110d9a7a7351b80ba9e6b6bdda4520980a0f`. Those are toolchain identities, not OPOS source revisions. Package repositories and tool availability can change; record the actual resolved versions and input hashes for a new build.

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

`OPOS_LOCAL_SOURCE=1` is essential for an unpublished checkout. Staging copies the two recipes into that complete pmaports tree, builds the declared runtime input, removes only its remote archive URL in the staged recipe and measures the actual staged checksum. It leaves the caller's recipe/source unchanged. `pmbootstrap checksum` then verifies/refreshes those staged inputs. This flow needs neither the project's development Git history nor any extra configuration.

There are two source assets: `opos-runtime-source-20261102.tar.gz` contains the declared runtime files plus LICENSE and THIRD_PARTY; `opos-source-20261102.tar.gz` is the complete source archive with editable build/install/deployment scripts and tests. The small runtime input alone is not the complete corresponding source. Runtime input ordering/ownership/timestamps are normalized by `make-runtime-source.py`; record the actual hash of each archive you build or use. Do not infer bit-identical APKs across different toolchains from source equality.

The packaged recipe names a release archive and its measured runtime checksum. Its URL works only after that exact archive is published; until then, use local staging. Do not point the recipe at a different commit or archive, and do not guess a checksum for an archive that has not been published.

Ordinary runtime changes update `packaging/pmos/runtime-files.txt` when files are added or removed;
they do not refresh the release archive checksum. CI checks that this inventory matches every
editable runtime file and that building the current archive twice produces identical bytes.
Local staging measures its own archive as described above.

When publishing a new runtime tarball, choose a new immutable archive name and release URL in
`packaging/pmos/opos/APKBUILD`. Build it with `make-runtime-source.py` using the recipe's
`_source_root` and the chosen name, then measure that exact file:

```sh
python3 packaging/pmos/make-runtime-source.py --output /tmp/opos-runtime-source-YYYYMMDD.tar.gz
python3 packaging/pmos/update-runtime-checksum.py /tmp/opos-runtime-source-YYYYMMDD.tar.gz
python3 packaging/pmos/update-runtime-checksum.py --check /tmp/opos-runtime-source-YYYYMMDD.tar.gz
```

Replace `YYYYMMDD` with the chosen release date. The checksum helper requires the filename to
match `_source_archive` and changes only its SHA-512 entry; auxiliary checksums stay intact.
After publishing, download the asset to a separate directory with the same filename and run
the helper with `--check` on that download before committing the recipe. Release CI can run
the same command; a missing file or checksum mismatch fails without rewriting the recipe.
The committed checksum describes those release bytes, even when the editable tree advances.

## Try it in QEMU

```sh
pmbootstrap qemu --image-size=2G -m 2048 --no-gl
```

The tool's default SSH forward is loopback port 2222; use the account chosen during installation. `sh packaging/pmos/qemu-demo.sh` only prints the corresponding plan for `$HOME/opos-pmaports` unless `OPOS_APORTS_DIR` is set, and `--help` shows usage; it does not build, connect or flash anything.

The non-GL virtual GPU fails under the default GLES2/GBM renderer, and tinydm starts the cage compositor directly, so the renderer must be chosen before cage runs: `/etc/cage-ui/opos.sh` is only sourced later by cage's own session child. The package therefore installs `/usr/share/tinydm/env-wayland.d/50-opos-renderer.sh`, which tinydm sources before it starts cage and which sets `WLR_RENDERER=pixman` unless a value is already set; a device with working accelerated drivers can set `WLR_RENDERER` explicitly. On November 3, 2026 an aarch64 QEMU image (QEMU 9.1.2, TCG, OpenRC, no manual renderer override) built from this tree booted to the OPOS lock screen about 70 seconds after start, and Unlock reached Home; the same image without this hook stayed on the console with the GLES2/GBM failure. That is a virtual-machine result, not a physical-phone or accelerated-rendering result. Do not weaken the Chromium sandbox or host protections.

## Limits

An earlier QEMU run that only rendered after the Pixman renderer was set by hand for testing, mentioned in [META.md](META.md), [WHY.md](WHY.md) and [notices-verification.md](notices-verification.md), is a historical observation, separate from the November 3 packaged-hook QEMU boot above.

These instructions do not flash hardware. Physical-phone and modem behavior is unverified, and the kiosk simulates the battery and modem; an earlier Surface Duo installation report concerned Droidian, not a supported final postmarketOS port.

Model inference on the image is optional and unverified: the QEMU image that was checked had no model server and no route to a computer running one, and its free memory was below the local-model minimum. Its critical-battery Ask showed the rules' resting message. Do not equate that with the separately observed desktop Glimmer response.

Changed source or packages need their own staging, build and boot checks and an exact runtime comparison; earlier test counts or screenshots do not verify them. Full system-image redistribution additionally needs an exact component/license/source inventory; this source does not establish one.
