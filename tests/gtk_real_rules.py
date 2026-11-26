# SPDX-License-Identifier: GPL-3.0-or-later
"""The plain rules behind the real-GTK check (tests/gtk_real_worker.py, tests/test_native_gtk_real.py): the screen size, the
apps that must be on, what counts as fitting, and when the check is skipped. Nothing here imports GTK, so the rules are
tested on every machine (tests/test_native_gtk_real.py, class Rules)."""

W, H = 1392, 900  # the headless Weston of tools/gtk-headless.sh; the Duo's two panels side by side
SLACK = 0.5  # GTK's rectangles are floats; half a pixel is rounding, not an overflow
NO_GTK = 77  # the worker's exit status for "no GTK 4 or no display here"
# Enable every server app so the check measures every available native page.
# Notices is browser-only and does not add a native page.
ALL_APPS = "clock,contacts,notes,calc,calls,convert,notices,ai"
SPAN = "675,42"  # OPOS_SPAN: the pane width and the hinge gap, as on the phone
# OPOS_SERVER_URL when the run has none: a loopback address nothing listens on (port 1), in the one form the shell accepts
# (opos.native.logic.server_url), so its poll thread gets "connection refused" and never reaches a real OPOS server.
NO_SERVER = "http://127.0.0.1:1"


def row_problem(label, index, minimum, height=H):
    """Words for a row that needs more height than the screen has at its minimum, or None."""
    if minimum <= height:
        return None
    return f"{label}: row {int(index)} needs at least {int(minimum)} px of height (the screen has {int(height)})"


def pane_problems(label, part, rect, mapped, size=(W, H)):
    """Words for each way a pane misses the screen. rect is (x, y, width, height) in window coordinates, or None when
    GTK has no bounds for it. A pane that is not on screen (not mapped) is not measured."""
    if rect is None:
        return [f"{label}: {part} pane has no bounds"]
    if not mapped:
        return []
    x, y, w, h = rect
    out = []
    if y < -SLACK or y + h > size[1] + SLACK:
        out.append(f"{label}: {part} pane runs off the screen: y {y:.0f} to {y + h:.0f} of {int(size[1])}")
    if x < -SLACK or x + w > size[0] + SLACK:
        out.append(f"{label}: {part} pane runs off the screen sideways: x {x:.0f} to {x + w:.0f} of {int(size[0])}")
    return out


def not_headless(env):
    """Why the check does not run here, or None. It runs only under tools/gtk-headless.sh, which sets
    OPOS_GTK_HEADLESS: another display has another size, and the numbers would mean something else."""
    if env.get("OPOS_GTK_HEADLESS"):
        return None
    return f"not under tools/gtk-headless.sh (a {W}x{H} headless Weston); another display has another size"


def skip_after_run(returncode, stderr, env):
    """Why a finished run counts as a skip, or None. Only the worker's "no GTK or no display" status skips, and
    OPOS_REQUIRE_GTK=1 turns even that into a failure."""
    if returncode == NO_GTK and not env.get("OPOS_REQUIRE_GTK"):
        return stderr.strip() or "no GTK 4 here"
    return None


def failure_lines(output):
    """The lines of a worker's output worth showing when it failed; the whole output when there are none."""
    keep = [line for line in output.splitlines() if line.startswith(("FAIL", "checked", "no GTK", "no display"))]
    return "\n".join(keep) or output
