# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only checks for an existing GPU Link forward and loopback chat server.

No forward creation, enrolment, signed phone requests or engine startup. Chat
probes send fixed questions to the existing server. Windows hardware and physical
phone readiness require a run on that PC. --quick skips chat and GPU checks.
"""

from __future__ import annotations

import argparse
import csv
import http.client
import io
import ipaddress
import json
import math
import os
import re
import stat
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from opos import gpu_link_proto as proto

GPU_COMMAND = [
    "nvidia-smi",
    "--query-gpu=name,memory.total,driver_version,utilization.gpu,memory.used",
    "--format=csv,noheader,nounits",
]
FIRST = "Say OK."
CONTINUE = "Count from 1 to 12, separated by commas, nothing else."
PREFIX = "1, 2, 3, 4,"
GPU_PROMPT = "Write a numbered list of the first 30 prime numbers with one short fact each."
SLOW_SECONDS = 5
LINK_FIX = "The forward is missing or the phone page server was started without OPOS_GPU_LINK=1; check both."
COLD_FIX = "Send one full question through the chat server before the demo."
REASON_FIX = "Start the server with reasoning off (llama.cpp: --reasoning off), or use a non-thinking model."
GPU_FIX = "The engine may be running on the CPU; check the server's start-up log for the CUDA device."
SERIAL = re.compile(r"[A-Za-z0-9._:-]{1,64}")
ENGINE_LOG_LIMIT = 2 * 1024 * 1024


def clean(text):
    """Clean probe text locally; do not change what the stand-in sends on."""
    return "".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cf", "Cs") or c in "\n\t")


def display(text):
    """Untrusted details must not control the terminal or JSON presentation."""
    return "".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cf", "Cs"))


def usable(text):
    return any(unicodedata.category(c)[0] in "LNPS" for c in text)


COUNTING = r"5\s*,\s*6\s*,\s*7(?:\s*,\s*[1-9][0-9]*)*"


def counting(text, period=True):
    """The integers of a comma list that reads 5, 6, 7, ... and goes on by one; None for anything else."""
    if re.fullmatch(COUNTING + (r"\s*\.?" if period else r"\s*"), text):
        numbers = [int(n) for n in text.rstrip(".").split(",")]
        if numbers == list(range(5, 5 + len(numbers))):
            return numbers
    return None


def continues(text, finish):
    """True when the text is the list 5, 6, 7, ... that follows PREFIX.

    A server that stops at the token limit can end the list right after a comma or inside the next number. Only then
    the list before the last comma must be right (without a final period) and what follows that comma must be empty or
    the beginning of the number that comes next. Any other final item is wrong, even after a stop at the limit.
    """
    if counting(text) is not None:
        return True
    if finish != "length":
        return False
    head, _, tail = text.rpartition(",")
    numbers = counting(head, period=False)
    return numbers is not None and str(numbers[-1] + 1).startswith(tail.strip())


def unexpected(result):
    """Why a stream is not the single, normally finished completion that the probes ask for ('' when it is)."""
    if result.get("unexpected"):
        return "unexpected finish reason"
    return "unexpected extra choice in the stream" if result.get("extra_choice") else ""


class LogAliasError(RuntimeError):
    pass


def check_log_alias(path, info, protected):
    for other in protected:
        if other is None:
            continue
        try:
            other_info = os.stat(other)
        except FileNotFoundError:
            continue
        if os.path.samefile(path, other) or (info.st_dev, info.st_ino) == (other_info.st_dev, other_info.st_ino):
            raise LogAliasError("Engine log refused: same file as --key-file or --json.")


def engine_offload(path, port, max_age=12, protected=()):
    """Inspect a regular start-up log, never more than its first 2 MiB."""
    if os.path.islink(path):
        raise OSError("engine log must be a regular, non-symlink file")
    info = os.stat(path)
    check_log_alias(path, info, protected)
    if not stat.S_ISREG(info.st_mode):
        raise OSError("engine log must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        opened = os.fstat(handle.fileno())
        check_log_alias(path, opened, protected)
        if not stat.S_ISREG(opened.st_mode) or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            raise OSError("engine log changed while opening")
        text = handle.read(ENGINE_LOG_LIMIT).decode("utf-8", errors="replace")
    lines = text.split("\n")
    offloads = [
        i for i, line in enumerate(lines) if re.search(r"offloaded\s+[1-9]\d*(?:/\d+)?\s+layers to GPU", line, re.I)
    ]
    first = next((i for i, line in enumerate(lines) if re.search(r"offloaded\s+\d+", line, re.I)), None)
    failures = (
        r"failed to|out of memory|cudaMalloc failed|no CUDA-capable device|CUDA error|falling back|offloaded 0/|error:"
    )
    if first is not None and any(re.search(failures, line, re.I) for line in lines[first:]):
        return "FAIL", "engine log reports a failure after offload"
    # Also retain the older zero-offload form without a denominator.
    if any(re.search(r"offloaded\s+0(?:/\d+)?\s+layers to GPU", line, re.I) for line in lines):
        return "FAIL", "engine log shows no GPU offload"
    devices = [
        i
        for i, line in enumerate(lines)
        if re.search(r"ggml_cuda_init:\s*found\s+[1-9]\d*\s+CUDA devices|\bCUDA\d+\b[^\n]*buffer", line)
    ]
    age = max(0, (time.time() - opened.st_mtime) / 3600)
    if age > max_age:
        return "WARN", f"engine log is stale: {age:.2f} hours old (maximum {max_age:g})"
    if opened.st_size > ENGINE_LOG_LIMIT:
        return "WARN", "engine log exceeds the 2 MiB scan limit"
    listeners = []
    for i, line in enumerate(lines):
        match = re.search(r"\blistening on\s+(https?://[^\s]+)", line, re.I)
        if match:
            try:
                url = urlsplit(match[1])
                if url.hostname and url.port == port:
                    listeners.append(i)
            except ValueError:
                pass
    if not listeners:
        return "WARN", f"engine log is not tied to server port {port}: no matching listening line"
    listening = listeners[0]
    if not any(i < listening for i in offloads) or not any(i < listening for i in devices):
        return "WARN", "engine log needs CUDA device and positive offload lines before the listening line"
    return "PASS", "engine log and utilization agree: evidence, not proof"


class ProbeError(ValueError):
    """A diagnostic with fixed guidance and, for server errors, a cleaned excerpt."""

    def __init__(self, detail, fix, status=None):
        super().__init__(detail)
        self.fix, self.status = fix, status


def endpoint(value, phone=False):
    """Validate loopback literals without DNS, proxies or redirect handlers."""
    url = urlsplit("//" + value if phone else value)
    if (
        url.username is not None
        or url.password is not None
        or url.fragment
        or (phone and (url.path or url.query or url.port is None))
        or (not phone and (url.scheme not in ("http", "https") or not url.path.startswith("/")))
    ):
        raise ValueError
    host = url.hostname
    if host == "localhost":
        host = "127.0.0.1"
    elif not ipaddress.ip_address(host).is_loopback:
        raise ValueError
    port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
    if not 1 <= port <= 65535 or "%" in host or any(ord(c) < 33 for c in value):
        raise ValueError
    return host, port, url


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phone", default="127.0.0.1:8767", help="forwarded loopback HOST:PORT")
    parser.add_argument("--server", help="full loopback HTTP(S) chat-completions endpoint")
    parser.add_argument("--model", help="model name on that server")
    parser.add_argument("--key-file", help="existing raw 32-byte key; never created or read for its contents")
    parser.add_argument(
        "--adb", default="adb", help="selects trusted executable code: one path or command name (default: PATH adb)"
    )
    parser.add_argument("--engine-log", type=Path, help="existing llama.cpp start-up log; read only its first 2 MiB")
    parser.add_argument("--engine-log-max-age", type=float, default=12, help="maximum log age in hours (default: 12)")
    parser.add_argument("--serial", help="select one attached device")
    parser.add_argument("--transport", choices=("adb", "ssh"), default="adb")
    parser.add_argument("--no-gpu-probe", action="store_true", help="skip GPU use and speed; retain GPU inventory")
    parser.add_argument("--quick", action="store_true", help="skip chat probes and both GPU checks")
    parser.add_argument("--timeout", type=float, default=30, help="seconds per network step (default: 30)")
    parser.add_argument("--json", type=Path, help="create a new JSON results file exclusively; existing paths refused")
    parser.add_argument("--color", action="store_true", help="colour status words only when stdout is a terminal")
    args = parser.parse_args(argv)
    try:
        endpoint(args.phone, phone=True)
        if args.server:
            endpoint(args.server)
    except (ValueError, TypeError):
        parser.error(
            "endpoints must name a loopback literal or localhost and a valid port; no credentials or fragments"
        )
    if bool(args.server) != bool(args.model):
        parser.error("--server and --model must be supplied together")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and greater than zero")
    if not math.isfinite(args.engine_log_max_age) or args.engine_log_max_age <= 0:
        parser.error("--engine-log-max-age must be finite and greater than zero")
    if args.serial is not None and SERIAL.fullmatch(args.serial) is None:
        parser.error("invalid serial")
    if not args.adb or (any(c.isspace() for c in args.adb) and not Path(args.adb).is_file()):
        parser.error("--adb must be a single path or command name; paths with spaces must exist as given")
    return args


class DeadlineReader(io.RawIOBase):
    """Apply an overall deadline even when a server keeps trickling bytes."""

    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline
        self.stream = sock.makefile("rb", buffering=0)

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        self.sock.settimeout(remaining)
        count = self.stream.readinto(buffer)
        if time.monotonic() >= self.deadline:
            raise TimeoutError
        return count

    def close(self):
        self.stream.close()
        super().close()


class DeadlineResponse(http.client.HTTPResponse):
    def __init__(self, sock, deadline, **kwargs):
        class ReaderSocket:
            def makefile(self, *args):
                return io.BufferedReader(DeadlineReader(sock, deadline))

        super().__init__(ReaderSocket(), **kwargs)


def connection(address, timeout):
    host, port, url = address
    cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    conn = cls(host, port, timeout=timeout)
    deadline = time.monotonic() + timeout
    conn.response_class = lambda sock, **kw: DeadlineResponse(sock, deadline, **kw)
    return conn


def command(args, timeout):
    """All callers supply one of the four read-only argument lists."""
    result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise OSError
    return result.stdout.decode("utf-8", errors="replace")


def gpu_sample(timeout):
    rows = list(csv.reader(command(GPU_COMMAND, timeout).splitlines(), skipinitialspace=True))
    if not rows:
        raise ValueError
    samples = []
    for row in rows:
        if len(row) != 5:
            raise ValueError
        name, memory, driver, utilization, used = (part.strip() for part in row)
        memory, utilization, used = float(memory), float(utilization), float(used)
        if not all(math.isfinite(n) for n in (memory, utilization, used)):
            raise ValueError
        if memory <= 0 or not 0 <= utilization <= 100 or not 0 <= used <= memory:
            raise ValueError
        name = "".join(c for c in name[:100] if 32 <= ord(c) < 127)
        samples.append((name, memory, utilization, used))
    return samples


def stream(args, prompt, max_tokens, partial=None):
    """Read bounded SSE; keep reply text only for the continuation excerpt."""
    messages = [{"role": "user", "content": prompt}]
    if partial is not None:
        messages.append({"role": "assistant", "content": partial})
    payload = {
        "model": args.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens,
    }
    if partial is not None:
        payload["temperature"] = 0
    try:
        return chat_request(args, payload, partial is not None)
    except ProbeError as err:
        if partial is None or err.status not in (400, 422):
            raise
        # Some endpoints reject temperature; try their defaults once.
        payload.pop("temperature")
        return chat_request(args, payload, True)


def chat_request(args, payload, keep_text):
    address = endpoint(args.server)
    url = address[2]
    target = url.path + ("?" + url.query if url.query else "")
    started = time.monotonic()
    conn = connection(address, args.timeout)
    try:
        conn.request(
            "POST",
            target,
            json.dumps(payload).encode(),
            {"Content-Type": "application/json", "Accept-Encoding": "identity"},
        )
        with conn.getresponse() as response:
            if response.status != 200:
                raise ProbeError(
                    f"HTTP {response.status}",
                    "Check the chat server endpoint, model and start-up log.",
                    response.status,
                )
            if response.getheader("Content-Type", "").split(";")[0].strip() != "text/event-stream":
                raise ProbeError(
                    "server reported an error: body is not an event stream (not text/event-stream)",
                    "Enable streaming chat completions on the server.",
                )
            if response.getheader("Content-Encoding") not in (None, "identity"):
                raise ProbeError("compressed stream refused", "Disable compressed replies on the chat server.")
            return read_stream(response, started, keep_text)
    finally:
        conn.close()


def has_error(value):
    if isinstance(value, dict):
        return "error" in value or any(has_error(v) for v in value.values())
    return isinstance(value, list) and any(has_error(v) for v in value)


def scrub_error(value):
    if isinstance(value, str):
        return display(value)
    if isinstance(value, dict):
        return {display(k): scrub_error(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_error(v) for v in value]
    return value


def stream_error(text):
    if isinstance(text, dict) and "message" in text:
        text = text["message"]
    if not isinstance(text, str):
        text = json.dumps(scrub_error(text), ensure_ascii=False)
    raise ProbeError(
        "server reported an error: " + display(text)[:60],
        "Check the chat server log and retry.",
    )


def read_stream(response, started, keep_text):
    result = dict(
        text="",
        text_truncated=False,
        chunks=0,
        tokens=None,
        first=None,
        reasoning=False,
        unexpected=False,
        extra_choice=False,
        finish=None,
    )
    total = 0
    while True:
        line = response.readline(65537)
        total += len(line)
        if len(line) > 65536 or total > 2 * 1024 * 1024:
            raise ProbeError("server stream too large", "Check the server's streaming chat configuration.")
        if not line:
            raise ProbeError("server stream ended early", "Check the server's streaming chat configuration.")
        if line.startswith(b"event:") and line[6:].strip().lower() == b"error":
            stream_error("event: error")
        if not line.startswith(b"data:"):
            if line.strip() and not line.startswith((b":", b"event:", b"id:", b"retry:")):
                stream_error(line.decode("utf-8", errors="replace"))
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            if result["finish"] is None:
                raise ProbeError(
                    "server stream missing finish reason", "Check the server's streaming chat configuration."
                )
            # Only stop/length can PASS; callers retain WARN for other non-error finish reasons.
            break
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            stream_error(data.decode("utf-8", errors="replace"))
        if not isinstance(value, dict):
            stream_error(data.decode("utf-8", errors="replace"))
        if "error" in value:
            stream_error(value["error"])
        consume_event(value, result, started, keep_text)
    result["elapsed"] = time.monotonic() - started
    return result


def consume_event(value, result, started, keep_text):
    choices = value.get("choices", [])
    # The probes ask for one completion: text, reasoning and finish reason must all come from it.
    result["extra_choice"] |= len(choices) > 1
    for choice in choices:
        if has_error(choice):
            stream_error(choice)
        result["extra_choice"] |= choice.get("index") not in (None, 0)
        finish = choice.get("finish_reason")
        if finish == "error":
            stream_error("finish_reason: error")
        if finish not in (None, "stop", "length"):
            result["unexpected"] = True
        # Null means generation is still in progress; it must not erase a prior finish reason.
        if finish is not None:
            result["finish"] = finish
    if choices:
        choice = choices[0]
        delta = choice.get("delta", {})
        piece = delta.get("content") or ""
        if not isinstance(piece, str):
            raise ValueError
        result["reasoning"] |= bool(delta.get("reasoning_content") or delta.get("reasoning"))
        piece = clean(piece)
        if piece:
            if result["first"] is None and usable(piece):
                result["first"] = time.monotonic() - started
            result["chunks"] += 1
            if keep_text:
                room = 4096 - len(result["text"])
                result["text_truncated"] |= len(piece) > room
                result["text"] += piece[:room]
    tokens = (value.get("usage") or {}).get("completion_tokens")
    if type(tokens) is int and 0 <= tokens <= 1_000_000:
        result["tokens"] = tokens


class Preflight:
    def __init__(self, args):
        self.args, self.results, self.selected = args, [], args.serial

    def add(self, name, check):
        started = time.monotonic()
        try:
            status, detail, fix = check()
        except ProbeError as err:
            status, detail, fix = "FAIL", str(err), err.fix
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            AttributeError,
            ArithmeticError,
            RecursionError,
            http.client.HTTPException,
        ):
            status, detail, fix = (
                "FAIL",
                "invalid or unavailable reply",
                "Check this service's configuration and retry.",
            )
        self.results.append(
            dict(
                name=name,
                status=status,
                detail=display(detail),
                fix=display(fix),
                seconds=round(time.monotonic() - started, 6),
            )
        )

    def adb_device(self):
        try:
            output = command([self.args.adb, "devices"], self.args.timeout)
        except (OSError, subprocess.TimeoutExpired):
            return "FAIL", "adb devices unavailable", "Install or locate adb, then check the cable and USB debugging."
        devices = []
        malformed = False
        for line in output.replace("\r\n", "\n").split("\n"):
            if any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in line):
                malformed = True
                continue
            fields = line.split()
            if not fields or line.startswith(("List of devices", "*", "adb server")):
                continue
            if len(fields) < 2 or SERIAL.fullmatch(fields[0]) is None:
                malformed = True
                continue
            serial, state = fields[:2]
            if fields[1:3] == ["no", "permissions"]:
                state = "no permissions"
            devices.append((serial, state))
        if malformed:
            return "WARN", "ignored malformed adb line", "Check adb devices output and select a valid --serial."
        if self.args.serial:
            devices = [(serial, state) for serial, state in devices if serial == self.args.serial]
        if not devices:
            return "FAIL", "no selected device attached", "Connect the phone and enable USB debugging."
        states = {state for _, state in devices}
        attention = sorted(states - {"device"})
        if not self.args.serial and len(devices) > 1 and attention:
            return (
                "FAIL",
                f"a device needs attention: {attention[0]}; select the right one with --serial",
                "Check attached devices, then pass --serial for the demo phone.",
            )
        if "unauthorized" in states:
            return "FAIL", "device unauthorized", 'Tap "Always allow from this computer" on the phone.'
        if "offline" in states:
            return "FAIL", "device offline", "Reconnect the cable and check USB debugging on the phone."
        if attention:
            return "FAIL", "a device needs attention: " + attention[0], "Check USB permissions and device state."
        if len(devices) > 1:
            return "WARN", "several devices attached", "Pass --serial for the demo phone."
        self.selected = devices[0][0]
        return "PASS", "one selected device in state device", ""

    def adb_forward(self):
        if self.args.serial is not None and SERIAL.fullmatch(self.args.serial) is None:
            return "FAIL", "invalid serial", "Pass a valid --serial."
        port = endpoint(self.args.phone, phone=True)[1]
        fix = f"Run adb forward tcp:{port} tcp:8767"
        if self.args.serial:
            fix = f"Run adb -s {self.args.serial} forward tcp:{port} tcp:8767"
        fix += "; keep a loop running because a forward does not survive a cable pull."
        try:
            output = command([self.args.adb, "forward", "--list"], self.args.timeout)
        except (OSError, subprocess.TimeoutExpired):
            return "FAIL", "adb forward list unavailable", fix
        rules = []
        malformed = False
        for line in output.replace("\r\n", "\n").split("\n"):
            if not line.strip():
                continue
            row = line.split()
            if (
                any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in line)
                or len(row) != 3
                or SERIAL.fullmatch(row[0]) is None
            ):
                malformed = True
                continue
            rules.append(row)
        if malformed:
            return "WARN", "ignored malformed adb line", fix
        matches = [
            row
            for row in rules
            if len(row) == 3
            and row[1:] == [f"tcp:{port}", "tcp:8767"]
            and (self.selected is None or row[0] == self.selected)
        ]
        if not matches:
            return "FAIL", f"no matching forward on local port {port}", fix
        if self.selected is None:
            return "WARN", "forward found; device selection is ambiguous", "Pass --serial for the demo phone."
        return "PASS", f"local tcp:{port} maps to phone tcp:8767", ""

    def hello(self):
        conn = connection(endpoint(self.args.phone, phone=True), min(3, self.args.timeout))
        try:
            conn.request(
                "GET",
                "/v1/hello",
                body=b"",
                headers={
                    "Host": "localhost",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            with conn.getresponse() as response:
                if response.status != 200:
                    return "FAIL", f"hello HTTP {response.status}", LINK_FIX
                if response.getheader("Content-Encoding") not in (None, "identity"):
                    raise ValueError
                raw = response.read(65537)
                if len(raw) > 65536 or response.length:
                    raise ValueError
                try:
                    value = proto.loads(raw, proto.WORK_REPLY_LIMIT)
                except ValueError:
                    return "FAIL", "phone reply refused by the stand-in's rules", LINK_FIX
                if (
                    set(value) != {"proto", "label", "epoch"}
                    or type(value.get("proto")) is not int
                    or value["proto"] != proto.PROTO
                    or proto.unhex(value.get("epoch"), proto.EPOCH_BYTES) is None
                    or not proto.valid_label(value.get("label"))
                ):
                    return "FAIL", "phone reply refused by the stand-in's rules", LINK_FIX
                return "PASS", "protocol 1; phone model readiness not reported", ""
        except (OSError, ValueError, TypeError, AttributeError, http.client.HTTPException):
            return "FAIL", "hello unavailable or invalid", LINK_FIX
        finally:
            conn.close()

    def key(self):
        suffix = "; enrolment is checked by the stand-in's first status line"
        fix = "Use an existing raw 32-byte regular key file owned by you with mode 0600 on POSIX."
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            with os.fdopen(os.open(self.args.key_file, flags), "rb") as handle:
                info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != 32:
                return "FAIL", "key file is not a regular 32-byte file" + suffix, fix
            if os.name == "posix" and (stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid()):
                return "FAIL", "key file requires current owner and mode 0600" + suffix, fix
        except OSError:
            return "FAIL", "key file unavailable" + suffix, fix
        detail = "regular 32-byte key file"
        detail += "; owner and mode 0600 checked" if os.name == "posix" else "; Windows access rules not checked"
        return "PASS", detail + suffix, ""

    def gpu(self):
        try:
            rows = gpu_sample(self.args.timeout)
            return "PASS", "; ".join(f"{row[0]}, {row[1]:g} MiB" for row in rows), ""
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return "WARN", "nvidia-smi unavailable; this PC may have no NVIDIA driver", "Check the NVIDIA driver."

    def chat(self, continuation=False):
        try:
            result = stream(
                self.args,
                CONTINUE if continuation else FIRST,
                24 if continuation else 16,
                partial=PREFIX if continuation else None,
            )
        except TimeoutError:
            return "FAIL", "chat server timed out (it may be cold)", COLD_FIX
        except OSError:
            return "FAIL", "chat server unreachable", "Check that the existing loopback chat server is listening."
        if continuation:
            text = clean(result["text"]).strip()
            if not usable(text):
                return "FAIL", "empty continuation", REASON_FIX if result["reasoning"] else COLD_FIX
            if text.startswith("1"):
                return (
                    "FAIL",
                    "restarts the answer",
                    "Use a server and chat template that continue a trailing assistant message.",
                )
            if odd := unexpected(result):
                return "WARN", odd, "Check the server's streaming configuration."
            if not result["text_truncated"] and continues(text, result.get("finish")):
                detail = (
                    "continues: the reply continued the stored partial on this fixed probe; a server with a prompt "
                    "cache may behave differently on a long real answer"
                )
                if result["reasoning"]:
                    return "WARN", detail + "; reasoning also arrived", REASON_FIX
                return "PASS", detail, ""
            return (
                "WARN",
                "unclear: " + json.dumps(text[:40], ensure_ascii=True),
                "Check assistant-prefix continuation before the demo.",
            )
        if result["first"] is None:
            if result["reasoning"]:
                return "FAIL", "only reasoning arrived; no usable content text", REASON_FIX
            return "FAIL", "no content text arrived", "Check the server's model and streaming configuration."
        if odd := unexpected(result):
            return "WARN", odd, "Check the server's streaming configuration."
        seconds = result["first"]
        detail = f"first usable content in {seconds:.3f} seconds"
        if result["reasoning"]:
            return "WARN", detail + "; reasoning also arrived", REASON_FIX
        return ("WARN", detail, COLD_FIX) if seconds > SLOW_SECONDS else ("PASS", detail, "")

    def gpu_probe(self):
        samples, unavailable = [], threading.Event()
        stop = threading.Event()
        deadline = time.monotonic() + self.args.timeout

        def sample():
            started = time.monotonic()
            try:
                rows = gpu_sample(max(0.001, deadline - started))
                samples.append((started, time.monotonic(), max(row[2] for row in rows), sum(row[3] for row in rows)))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                unavailable.set()

        def poll():
            while not stop.wait(0.25) and time.monotonic() < deadline:
                sample()

        sample()  # Baseline is separate from the generation window.
        baseline = samples[0] if samples else None
        worker = threading.Thread(target=poll, name="preflight-gpu")
        worker.start()
        generation_start = time.monotonic()
        try:
            try:
                result = stream(self.args, GPU_PROMPT, 150)
            finally:
                generation_end = time.monotonic()
            if result["first"] is None:
                raise ProbeError("GPU probe received no content text", REASON_FIX if result["reasoning"] else COLD_FIX)
        except TimeoutError:
            return "FAIL", "GPU chat probe timed out (server may be cold)", COLD_FIX
        except OSError:
            return "FAIL", "GPU chat probe server unreachable", "Check the existing loopback chat server."
        finally:
            try:
                # This diagnostic sample is deliberately outside the generation window.
                if time.monotonic() < deadline:
                    sample()
            finally:
                stop.set()
                worker.join()
        return self.gpu_result(result, samples, baseline, generation_start, generation_end, unavailable)

    def gpu_result(self, result, samples, baseline, generation_start, generation_end, unavailable):
        """Classify only completed samples wholly within the generation request."""
        elapsed = max(0.001, result["elapsed"])
        tokens = result["tokens"]
        speed = (
            f"{tokens / elapsed:.2f} tokens/s (server usage)"
            if tokens is not None
            else f"{result['chunks'] / elapsed:.2f} content chunks/s (token count unavailable)"
        )
        inside = [row for row in samples if generation_start <= row[0] and row[1] <= generation_end]
        peak = max((row[2] for row in inside), default=None)
        base = baseline[2] if baseline else None
        growth = max(0, max((row[3] for row in inside), default=baseline[3]) - baseline[3]) if baseline else 0
        detail = (
            f"{len(inside)} samples inside generation window; baseline {base if base is not None else 'unavailable'}%; "
            f"peak {peak if peak is not None else 'unavailable'}%; memory growth {growth:g} MiB; {speed}; "
            "measured by this check, not a benchmark"
        )
        reasons = []
        if self.args.engine_log is None:
            reasons.append("--engine-log not supplied")
        else:
            try:
                log_status, log_detail = engine_offload(
                    self.args.engine_log,
                    endpoint(self.args.server)[1],
                    self.args.engine_log_max_age,
                    (self.args.key_file, self.args.json),
                )
            except OSError:
                reasons.append("engine log unavailable or not a regular, non-symlink file")
            else:
                if log_status == "FAIL":
                    return "FAIL", log_detail + "; " + detail, GPU_FIX
                if log_status == "WARN":
                    reasons.append(log_detail)
        if unavailable.is_set() or baseline is None:
            reasons.append("nvidia-smi unavailable")
        if len(inside) < 3:
            reasons.append("need at least three samples inside generation window")
        if peak is not None and base is not None and peak - base < 15:
            reasons.append("GPU utilization did not rise by at least 15 points")
        if odd := unexpected(result):
            reasons.append(odd)
        if result["reasoning"]:
            reasons.append("reasoning also arrived")
        if reasons:
            return "WARN", "; ".join(reasons) + "; " + detail, REASON_FIX if result["reasoning"] else GPU_FIX
        return "PASS", "engine log and utilization agree: evidence, not proof; " + detail, ""

    def standin(self):
        tool = Path(__file__).with_name("gpu-link-standin-pc.py")
        if tool.is_file():
            try:
                command([sys.executable, "-B", str(tool), "--help"], 5)
                return "PASS", "--help exits 0", ""
            except (OSError, subprocess.TimeoutExpired):
                pass
        return (
            "FAIL",
            "stand-in missing or --help failed",
            "Restore the adjacent gpu-link-standin-pc.py and check its Python imports.",
        )

    def run(self):
        def skip(detail):
            return lambda: ("SKIP", detail, "")

        self.add(
            "python",
            lambda: (
                ("PASS", "interpreter is 3.11 or newer", "")
                if sys.version_info >= (3, 11)
                else ("FAIL", "interpreter is older than 3.11", "Run this tool with Python 3.11 or newer.")
            ),
        )
        ssh = self.args.transport == "ssh"
        self.add("adb device", skip("SSH transport") if ssh else self.adb_device)
        self.add("adb forward", skip("SSH transport") if ssh else self.adb_forward)
        self.add("link hello", self.hello)
        self.add("key file", self.key if self.args.key_file else skip("--key-file not supplied"))
        self.add("gpu", skip("--quick") if self.args.quick else self.gpu)
        chat_skip = "--quick" if self.args.quick else "--server and --model not supplied"
        chat_enabled = self.args.server and not self.args.quick
        self.add("chat server first answer", self.chat if chat_enabled else skip(chat_skip))
        self.add("chat server continuation", (lambda: self.chat(True)) if chat_enabled else skip(chat_skip))
        gpu_skip = "--no-gpu-probe" if self.args.no_gpu_probe else chat_skip
        self.add(
            "gpu use and speed",
            self.gpu_probe if chat_enabled and not self.args.no_gpu_probe else skip(gpu_skip),
        )
        self.add("stand-in tool", self.standin)
        return self.results


def main(argv=None):
    try:
        return run_main(argv)
    except KeyboardInterrupt:
        print("Preflight interrupted; readiness checks incomplete", file=sys.stderr)
        return 130


def run_main(argv):
    args = arguments(argv)
    try:
        if args.engine_log is not None:
            try:
                info = os.stat(args.engine_log)
            except OSError:
                pass  # GPU check reports missing/unavailable logs as WARN.
            else:
                check_log_alias(args.engine_log, info, (args.key_file, args.json))
        results = Preflight(args).run()
    except LogAliasError as err:
        print(str(err), file=sys.stderr)
        return 2
    if args.json:
        try:
            with args.json.open("x", encoding="utf-8") as output:
                output.write(json.dumps(results, indent=2, ensure_ascii=True) + "\n")
        except FileExistsError:
            print("JSON output refused: path already exists; choose a new --json path.", file=sys.stderr)
            return 2
        except OSError:
            results.append(
                dict(
                    name="JSON output",
                    status="FAIL",
                    detail="could not write the requested results file",
                    fix="Choose a writable --json output file.",
                    seconds=0,
                )
            )
    colors = {"PASS": "32", "WARN": "33", "FAIL": "31", "SKIP": "36"}
    counts = dict.fromkeys(colors, 0)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    for result in results:
        status = result["status"]
        counts[status] += 1
        label = f"\033[{colors[status]}m{status}\033[0m" if args.color and sys.stdout.isatty() else status
        print(f"{label} {result['name']}: {result['detail']}")
        if status in ("WARN", "FAIL"):
            print("Fix: " + result["fix"])
    print("Summary: " + ", ".join(f"{value} {key}" for key, value in counts.items()))
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
