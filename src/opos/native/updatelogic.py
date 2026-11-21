# SPDX-License-Identifier: GPL-3.0-or-later
"""The native Updates screen's view of the existing updater response."""

import urllib.parse

from opos.native.logic import size_words


def mapping(value):
    return value if isinstance(value, dict) else {}


def source_host(source):
    url = source.get("url")
    if not isinstance(url, str):
        return None
    try:
        return urllib.parse.urlsplit(url).hostname
    except ValueError:
        return None


def busy(snapshot):
    phase = mapping(snapshot.get("status")).get("phase")
    return (
        mapping(snapshot.get("job")).get("phase") == "downloading"
        or bool(snapshot.get("pending"))
        or mapping(snapshot.get("rollback")).get("requested") is True
        or phase in ("verifying", "applying", "checking")
    )


def can_check(snapshot):
    source = mapping(snapshot.get("source"))
    return (
        snapshot.get("available") is True
        and source.get("configured") is True
        and not source.get("error")
        and not busy(snapshot)
    )


def download_offer(snapshot):
    check = mapping(snapshot.get("check"))
    review = check.get("review")
    if (
        not can_check(snapshot)
        or snapshot.get("stalled")
        or mapping(snapshot.get("rollback")).get("staged") is not None
        or check.get("ok") is not True
        or check.get("newer") is not True
        or not isinstance(review, str)
        or not review
    ):
        return None
    size = _size(check.get("size")) or "size unknown"
    host = source_host(mapping(snapshot.get("source"))) or "the configured source"
    words = (
        f"Download {check.get('version')} (update {check.get('serial')}, {size}) from {host}? "
        "The updater may restart OPOS after applying it or stage it for the next start."
    )
    return "update_download", review, "Yes, download", words


def rollback_offer(snapshot):
    if snapshot.get("available") is not True or snapshot.get("stalled") or busy(snapshot):
        return None
    data = mapping(snapshot.get("rollback"))
    review = mapping(data.get("review"))
    key, kind, serial = review.get("key"), review.get("kind"), review.get("serial")
    if data.get("possible") is not True or not isinstance(key, str) or type(serial) is not int:
        return None
    if kind == "cancel":
        return "update_rollback", key, "Yes, drop it", f"Drop update {serial} from the next start?"
    if kind == "rollback":
        return "update_rollback", key, "Yes, go back", f"Go back to update {serial}? OPOS restarts."
    return None


def _size(value):
    return size_words(value) if type(value) is int and value >= 0 else None


def state_lines(snapshot):
    if snapshot is None:
        return ["Waiting for update status."]
    if snapshot.get("missing") or snapshot.get("available") is False:
        return ["Updates are not available on this install."]
    if snapshot.get("available") is not True:
        return ["Could not read update status."]

    lines = []
    running = mapping(snapshot.get("running"))
    if running.get("version"):
        lines.append(f"Installed: {running['version']}.")
    else:
        lines.append("Installed version not reported.")
    if type(running.get("serial")) is int:
        lines.append(f"Installed update: {running['serial']}.")

    source = mapping(snapshot.get("source"))
    host = source_host(source)
    if source.get("error"):
        lines.append("The update source could not be read.")
    elif source.get("configured"):
        lines.append(f"Update source: {host}." if host else "An update source is configured.")
    else:
        lines.append("No update source is set.")

    check = mapping(snapshot.get("check"))
    if check.get("ok") is True and check.get("newer") is True:
        description = f"Available: {check.get('version', 'unknown')} (update {check.get('serial', 'unknown')})"
        size = _size(check.get("size"))
        lines.append(f"{description}, {size}." if size else description + ".")
    elif check.get("message"):
        lines.append(check["message"])
    else:
        lines.append("A check has not been reported.")

    return lines + progress_lines(snapshot)


def progress_lines(snapshot):
    lines = []
    job = mapping(snapshot.get("job"))
    phase = job.get("phase")
    if phase == "downloading":
        got, total = _size(job.get("bytes")), _size(job.get("total"))
        if got and total:
            lines.append(f"Downloading: {got} of {total}.")
        elif got:
            lines.append(f"Downloading: {got} received. Total size not reported.")
        else:
            lines.append("Downloading. Progress not reported.")
    elif phase == "done":
        lines.append("Download delivered to the updater.")
    elif phase == "failed":
        lines.append(job.get("message") or "The download failed.")

    root = mapping(snapshot.get("status"))
    if root.get("phase") in ("verifying", "applying", "checking", "done", "rolled-back", "failed"):
        phase_words = root.get("phase").replace("-", " ")
        message = root.get("message")
        lines.append(f"Updater {phase_words}: {message}" if message else f"Updater: {phase_words}.")
    if snapshot.get("pending"):
        lines.append("An update is waiting for the updater.")
    if snapshot.get("stalled"):
        lines.append("The updater has not taken the queued request yet.")
    rollback = mapping(snapshot.get("rollback"))
    if type(rollback.get("staged")) is int:
        lines.append(f"Update {rollback['staged']} is staged for the next start.")
    if rollback.get("requested"):
        lines.append("An updater request is queued.")
    return lines
