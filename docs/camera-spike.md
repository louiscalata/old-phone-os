<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Camera spike

Status: **NOT_RUN**. No capture command was run. No package was added. No camera code is in this tree.

The feature spec treats the camera as research until a still capture has been timed on the Duo by Louis. This note is that research. It is not a camera app.

## What the port would have to use

The Duo's camera sits behind the Android camera HAL. On this Droidian/Halium stack the usual path is `droidmedia` / `gst-droid` (`droidcamsrc`), which is what `droidian-camera` uses. libcamera does not cover this phone's camera path today: SM8150 is not in the upstream qcom-camss table. That is why a libcamera app would not be the first try.

A still capture would mean new packages. Adding a package needs Louis's approval, plus `docs/lean.md` and `tests/test_lean_image.py`. Nothing here adds a package.

## The check that was not run

Louis would run one still, on a phone he is watching, and record the time and the battery drop. The command the spec names is:

```
gst-launch-1.0 droidcamsrc ! jpegenc ! filesink location=/tmp/opos-spike.jpg
```

That command was not run. There is no receipt, no timing, and no drain number. Until that receipt exists, OPOS does not get a camera page.

## Shape if the still works

One Take photo button, 72 px, in the thumb pane. The preview is in the list pane. A photo only: no filters and no video at first. Files would stay in a folder on the phone. Delete would ask. Backup stays local (copy off the phone), not a cloud upload. The camera would be refused when the phone is too hot, with the reason on screen.

## Why there is no code

A camera module that cannot take a picture would pretend. The still has not been seen. Device check D9 stays NOT_RUN.
