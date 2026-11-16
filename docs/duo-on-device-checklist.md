<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# On-device checklist

Each line needs the physical phone and a person present. None of them has been run for this tree. Every item is **NOT_RUN**.

Do not record device identifiers, radio identifiers, or hardware addresses in the repository. Do not flash the phone from this checklist.

| Item | State |
|---|---|
| Container debug port answers only on loopback, not on Wi-Fi | NOT_RUN |
| Passwordless USB root shell stays closed on a normal image | NOT_RUN |
| Polkit lets the OPOS service user change Wi-Fi, and an ssh login still cannot | NOT_RUN |
| Join a saved Wi-Fi network, including a wrong password and a following successful join | NOT_RUN |
| Clock is set from the network after Wi-Fi has full connectivity | NOT_RUN |
| First-run Wi-Fi joins a network from the setup screen | NOT_RUN |
| Bluetooth shows on when the radio is on | NOT_RUN |
| Battery percent, health, and temperature match the gauges | NOT_RUN |
| Cellular status text matches the radio, with no SIM and with a SIM | NOT_RUN |
| The window spans both screens and nothing sits on the hinge | NOT_RUN |
| Both panels use the same brightness, including after unfold | NOT_RUN |
| Folding blanks softly and unfolding restores the screen | NOT_RUN |
| Posture follows the hinge angle | NOT_RUN |
| Folded back, only the screen that faces you is lit, and it follows a flip within about a second | NOT_RUN |
| Folded back, touch on the dark screen is ignored | NOT_RUN |
| Both screens light again when the lid opens after back mode | NOT_RUN |
| Ten cold boots in a row with the sensor service started after boot, none stalls or resets the phone | NOT_RUN |
| Idle CPU of the shell and the sensor service per 30 seconds, unfolded, folded back and lid closed, against the peer-measured figures in NATIVE.md | NOT_RUN |
| Airplane mode turns the radios off and back on | NOT_RUN |
| Restart Wi-Fi and restart modem recover a stuck radio | NOT_RUN |
| Heat above the battery limit slows the phone and says so | NOT_RUN |
| Calls and texts through the modem, only after a SIM is present | NOT_RUN |
| Voice over LTE registers, or it is confirmed unavailable | NOT_RUN |
| A short alert plays and stops, and does not crash audio | NOT_RUN |
| An alarm ring is heard, steps from quiet to the cap, and Stop ends it at once | NOT_RUN |
