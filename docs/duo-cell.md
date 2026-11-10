<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Cellular status

The network page reads a plain cellular line from oFono when `gdbus` can ask `org.ofono`. If that does not answer, the line is taken from the ModemManager fields the page already reads.

The lines are fixed: "No SIM card", "SIM locked (PIN)", "SIM not accepted (carrier lock)", "Searching", "LTE, operator X, signal N bars" (or another technology name), "Data off: using Wi-Fi", and "Calls need VoLTE, not available on this build".

Equipment and subscriber identifiers are not copied into the status, the diagnostics dump, or a log. The tests use a sentinel value and assert it is absent.

This has not been run on a phone. **NOT_RUN.**
