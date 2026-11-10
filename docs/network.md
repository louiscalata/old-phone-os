<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Network: Wi-Fi and radio status on the Duo OS

Status, November 7, 2026: **written and tested against fake `nmcli`, `mmcli`, `rfkill`, `ip` and `bluetoothctl` scripts on a
computer; not tested on the phone.** Whether the phone's NetworkManager, ModemManager and polkit behave as the fakes do is what
the live checks below are for. This describes the OPOS OS image for the Surface Duo
([packaging/duo-os/README.md](../packaging/duo-os/README.md)). Over-the-air updates, Wikipedia search and a computer-hosted
helper all need a network, and until now the image had no way to join one.

## What the page shows

Settings > Network (a link the server adds to the main page only on the Duo OS) opens `/network`, a separate page, because the
main page is at its size budget. It reads, never raising when a tool is missing or slow (that reads as "unavailable"):

| Line | From |
|---|---|
| Wi-Fi: on, off, blocked, connected to which network, signal, address, whether NetworkManager sees internet | `nmcli` (`general status`, `device status`, `device wifi list`, `device show`) |
| Bluetooth: on, off, blocked, no controller | `rfkill list`, `bluetoothctl show`, and `read_radios` (sysfs) |
| Modem, SIM card | `mmcli -K -m any`: state, operator, technology, signal, SIM present or locked. No IMEI, IMSI, ICCID or number is read into the state |
| Mobile data | NetworkManager's `gsm` connection. The port switches it off while Wi-Fi is connected, and the page says so |
| Airplane mode | every radio blocked in `rfkill`, or switched off in NetworkManager or (when `rfkill` does not list it) off in `bluetoothctl`. A radio whose state was not read keeps the answer at "unknown"; it is never counted as blocked. With NetworkManager not answering and no modem row in `rfkill`, the modem is read through ModemManager: registered, connected, searching or enabled means it is on, and a modem that is there but unread, or in any other state, is such a radio (the summary names "mobile data") |
| USB network | `ip -br addr`: `usb0`, `rndis0`, `ncm0` and the like, and their address |

Calls and texts are **still simulated**: `src/opos/mm_modem.py` is not wired in yet. The page says so under the status, and every
status carries the sentence, so the modem line is the real radio's state, not a working phone line.

## What it can change

Scan, join (with a password), connect to a saved network, forget, disconnect, and switch Wi-Fi on or off, all through
NetworkManager as the unprivileged user the server runs as (`droidian`). Nothing here uses `sudo`.

- **Switched on by the install.** Changes work only where `OPOS_NETWORK=1` (the image's `opos-server.service.d/20-opos-network.conf`).
  Anywhere else (the desktop demo) the page only reads, so a demo can never change the Wi-Fi of the computer it runs on.
- **Permission is checked first.** `nmcli general permissions` is read; if NetworkManager would refuse (`no`) or ask an administrator
  (`auth`), the page says so in plain words and does not try. Scanning, joining, forgetting and switching Wi-Fi each need their own
  permissions (`wifi.scan`; `network-control` and `settings.modify.system`; `settings.modify.system`; `enable-disable-wifi`).
- **Passwords.** A password goes to `nmcli --ask device wifi connect` on standard input, one line, so it is never in the process list.
  nmcli's documented behaviour is that `--ask` prompts for a password that was not given; that it reads the prompt from a pipe is
  from nmcli's documentation and common use, not from a run on the phone. If the live check shows it does not, `OPOS_NET_TTY=1` types
  the password into a pseudo terminal instead. The password is not kept, logged or returned by OPOS: NetworkManager stores the
  profile (so it is on the phone, in NetworkManager's connection file) and "Forget" deletes it. It is not part of `/api/state`, so
  the helper never sees it, and no network name, saved or visible, is part of the state the helper reads. A password for a saved
  network replaces the saved entry only after the new one is up: a failed join (a wrong password, no address, the network gone) keeps
  the old entry and deletes only the profile that attempt itself left behind, so a wrong password is not retried forever and a
  right one is not lost. "Forget" and that cleanup look at every saved Wi-Fi profile, not the first few that the page lists, and
  say "gone" or "Nothing was saved" only when they have checked: a saved profile whose network name NetworkManager will not give
  could be an entry for the network being joined, so after a failed join the text then says that OPOS could not read every saved
  network and cannot tell whether this one is saved, as "Forget" does.
- **Limits.** A Wi-Fi password is 8 to 63 characters (or 64 hex digits). Networks that need a user name (802.1X, "company or school")
  and WEP are listed but not joinable. Hidden networks can be joined by typing the name. Names starting with a dash are refused.

If NetworkManager does not allow the user to change Wi-Fi, build the image with `OPOS_NET_POLKIT=1` to add
`packaging/duo-os/50-opos-network.rules`, or install it by hand. It lets `droidian` do four NetworkManager actions without being
asked. They also let that user connect or disconnect any connection and add, change or delete system connections (mobile data and
VPNs included), so it is opt-in and off by default.

**Who can change Wi-Fi.** The page token is served on `GET /network` like the home page, so any local user or process on the phone
can read it and change Wi-Fi as `droidian`: the accepted posture is a single-user phone, where every local process is the owner's.
The optional polkit rule is wider than Wi-Fi: it also lets the server activate, modify and delete every NetworkManager connection,
mobile data and VPN profiles included.

## Files and settings

| What | Where |
|---|---|
| status and control, command line (`python3 -m opos.net [status\|scan\|probe]`, `join SSID` with the password on standard input) | `src/opos/net.py` |
| the page, the `net_status`, `net_scan`, `net_join`, `net_forget`, `net_wifi` actions | `src/opos/net_page.py`, a block in `src/opos/web.py` |
| turn control on; the opt-in polkit rule; the boot diagnostics "radios" lines | `OPOS_NETWORK=1`, `OPOS_NET_POLKIT=1` at image build, `packaging/duo-os/opos-diag` |
| tests | `tests/test_net.py` |

The actions follow the existing rules: the page token, a loopback origin, JSON only, and their own rate limits.

## Checking it on the phone

As `droidian` over ssh, on the image's checkout (`/opt/opos/src`):

```sh
python3 -m opos.net probe     # the raw nmcli, rfkill, ip, bluetoothctl, mmcli output, to compare with the parsers
python3 -m opos.net status    # what the page will show
nmcli -t general permissions  # wifi.scan, network-control, settings.modify.system, enable-disable-wifi should say yes
```

Then open Settings > Network on the phone, scan, and join a network. The phone's clock reads 1970 until set, which does not stop
a WPA join but does stop secure websites and updates until the time is set.

## Not covered

No airplane-mode switch, no Bluetooth pairing, no mobile-data switch, no SIM PIN entry, no VPN, no enterprise (802.1X) Wi-Fi, and
no change of the NetworkManager or polkit configuration other than the opt-in rule. Wi-Fi joining in the setup wizard
(`src/opos/setup`) is still record-only. Typing a password uses the image's on-screen keyboard (`OPOS_OSK`, [packaging/duo-os/OSK.md](../packaging/duo-os/OSK.md)): `/network` is served through the same injector as the home page, so the password field gets it; seen in a desktop browser, not on the phone.
