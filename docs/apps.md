<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Everyday apps

`OPOS_APPS` names the extra apps, comma-separated (`clock,contacts`). When the variable is absent or empty, the main page is unchanged and `/apps` is not served. The Duo image names clock and contacts. Calendar stays off until it is named. The demo and the postmarketOS build do not set the variable.

Each named app is its own page. The main page gains one link, More apps, inside All apps. `web.page()` does not grow.

App files live under `$XDG_STATE_HOME/opos` (the default state directory when that variable is unset). Writes are atomic and mode 0600. A file that cannot be read is moved aside and the app starts empty. Nothing in this path uses the network.

Each app has a disclosure line: works offline, sends data, uses, real or practice. The helper is not given a way to write these files.

An app that fails to open shows a short page with Home. Phone and the front page keep working.

## Calendar

`calendar` is the local calendar. It is off until `OPOS_APPS` names it. The Duo image does not name it. Events are one `calendar.ics` file: a title, a date, a time or all day, a daily or weekly repeat, and a reminder of none, 10 minutes, 1 hour or 1 day. Import and export are a file in the calendar directory. There is no account and no network.

The lock screen shows the next timed event as "Next: 15:00". The title stays off that line. Reminders use the same grace as an alarm. A reminder more than 10 minutes late is marked missed once. An unknown clock does not remind. The calendar does not write alarms.

On-device checks for this page are NOT_RUN.

## Not run on a phone

Clock is the first named app (`OPOS_APPS=clock` on the Duo image). It is its own page. The home page only gains the short `/apps-home.js` tag, which adds the More apps link and, when an alarm is armed, the words "Alarm" and "Alarm 07:00" on the lock screen.

No install has been made for this work. Every on-device check in the phone-features spec (D1-D11) is NOT_RUN. Sound and vibration are not played yet.
