# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import http.client
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from threading import Event
from typing import Any


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect blocked", hdrs, fp)


def _open_no_redirect(req, timeout=None):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(req, timeout=timeout)


REASONING_HEADROOM = 1200  # Glimmer spends tokens on reasoning before the visible answer.
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_TOKENS = 600
MAX_RESPONSE_BYTES = 1048576
MODEL_LIST_TIMEOUT = 5.0  # seconds for the /models check before an unconfigured local route gets a prompt


@contextlib.contextmanager
def _opened(opener, req, timeout):
    """Open req and close the response afterwards, also when reading or parsing it fails."""
    try:
        resp = opener(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        e.close()  # an HTTP error carries the open response body
        raise
    if hasattr(resp, "__enter__") and hasattr(resp, "__exit__"):
        with resp as r:
            yield r
        return
    try:
        yield resp
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()


def _read_capped(resp):
    cap = MAX_RESPONSE_BYTES
    raw = resp.read(cap + 1)
    if len(raw) > cap:
        raise ValueError("response too large")
    return raw


def timeout_s(env=None):
    if env is None:
        env = os.environ
    v_raw = env.get("OPOS_MUSE_TIMEOUT")
    if v_raw is None:
        return DEFAULT_TIMEOUT
    try:
        v = float(v_raw)
    except (ValueError, TypeError):
        return DEFAULT_TIMEOUT
    if not math.isfinite(v) or v <= 0:
        return DEFAULT_TIMEOUT
    if v < 5.0:
        return 5.0
    if v > 600.0:
        return 600.0
    return float(v)


@dataclass(frozen=True)
class Route:
    """Immutable route definition.

    verify_model: send a prompt only after the server's /models list contains model, or one of the
    alternates, exactly. alternates: (model id, name) pairs accepted after model, in order.
    """

    key: str
    name: str
    where: str
    base_url: str
    model: str
    verify_model: bool = False
    alternates: tuple = ()


class Reply(str):
    """Visible answer text; route is the route that answered, with the model id and name it served."""

    route = None


def answered_by(reply, route):
    """The route that produced reply, or route when reply carries none (for example a stub's plain text)."""
    used = getattr(reply, "route", None)
    return used if isinstance(used, Route) else route


def glimmer_route(env=None):
    """Return local Glimmer route."""
    if env is None:
        env = os.environ
    default = "http://127.0.0.1:1234/v1"
    url = env.get("OPOS_GLIMMER_URL")
    if url:
        with contextlib.suppress(Exception):
            p = urllib.parse.urlparse(url)
            if p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost"):
                return Route("glimmer", "Muse Glimmer", "on a local server", url, "muse-glimmer-30b")
    return Route("glimmer", "Muse Glimmer", "on a local server", default, "muse-glimmer-30b")


SPARK_OPEN = Route(
    "spark-open",
    "Muse Spark Open",
    "Baseten partner inference",
    "https://inference.baseten.co/v1",
    "meta-models/muse-spark-open-1026",
)


def choose(hard, env=None):
    """Choose Spark Open when hard and env allows."""
    if env is None:
        env = os.environ
    if hard and env.get("OPOS_SPARK_OPEN") == "1" and env.get("BASETEN_API_KEY"):
        return SPARK_OPEN
    return glimmer_route(env)


def is_hard(goal_hours, hours_left):
    """Check if hours_left is below goal_hours."""
    if goal_hours is None or hours_left is None:
        return False
    return hours_left < goal_hours


def label(route):
    """Format route label."""
    return f"{route.name}, {route.where}"


class MuseError(Exception):
    """Raised when Muse request fails."""


def _require_http_url(url):
    try:
        scheme = urllib.parse.urlparse(url).scheme
    except ValueError as e:
        raise MuseError("unsupported url scheme") from e
    if scheme in ("http", "https"):
        return url
    raise MuseError("unsupported url scheme")


def _visible(text):
    """Keep only visible text: after the last </think> and before any later <think."""
    text = re.split(r"</think\s*>", text, flags=re.IGNORECASE)[-1]
    return re.split(r"<think", text, maxsplit=1, flags=re.IGNORECASE)[0].strip()


def _payload(route, prompt, max_tokens):
    """Build the helper's chat-completions request body."""
    payload = {
        "model": route.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens + REASONING_HEADROOM if route.key in ("glimmer", "lan") else max_tokens,
        "temperature": 0.2,
    }
    if route.key in ("glimmer", "lan"):
        payload["reasoning_effort"] = "low"
    return payload


def ask(route, prompt, env=None, opener=None, timeout=None, max_tokens=DEFAULT_MAX_TOKENS):
    """POST chat completion and return content."""
    if env is None:
        env = os.environ
    if timeout is None:
        timeout = timeout_s(env)
    if opener is None:
        opener = _open_no_redirect
    headers = {"Content-Type": "application/json"}
    if route.key == "spark-open":
        try:
            headers["Authorization"] = "Api-Key " + env["BASETEN_API_KEY"]
        except Exception as e:
            raise MuseError("auth missing") from e
    _require_http_url(route.base_url)
    try:
        if route.verify_model:
            route = _verified(
                route, opener, min(timeout, MODEL_LIST_TIMEOUT)
            )  # an unverified server never receives the prompt
        data = json.dumps(_payload(route, prompt, max_tokens)).encode("utf-8")
        req = urllib.request.Request(route.base_url + "/chat/completions", data=data, headers=headers, method="POST")  # noqa: S310
        with _opened(opener, req, timeout) as resp:
            raw = _read_capped(resp)
        obj = json.loads(raw.decode("utf-8"))
        text = obj["choices"][0]["message"]["content"]
        if not isinstance(text, str):
            raise MuseError("empty response")
        if obj["choices"][0].get("finish_reason") == "length":
            raise MuseError("cut off at the token budget")
        text = _visible(text)
        if not text:
            raise MuseError("empty response")
        reply = Reply(text)
        reply.route = route
        return reply
    except MuseError:
        raise
    except (OSError, ValueError, KeyError, IndexError, TypeError, RecursionError, http.client.HTTPException) as e:
        raise MuseError("request failed") from e


STREAM_IDLE_S = 30.0
STREAM_LINE_BYTES = 65536


def stream(
    route: Route,
    prompt: str,
    env: Mapping[str, str] | None = None,
    opener: Callable[..., Any] | None = None,
    timeout: float | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    cancel: Event | None = None,
) -> "Stream":
    """Open a streamed completion after applying the blocking helper's route checks.

    The opener timeout is min(STREAM_IDLE_S, time left), also for model checks.
    The overall deadline is checked before every body read and before text is
    yielded. Ending can take one waiting read beyond timeout: with the defaults,
    up to 150 seconds total (120 + 30), plus scheduling and cleanup. DNS and
    trickled header or chunk-framing bytes are outside this bound, as for ask.
    Custom openers must honor the timeout and provide bounded reads themselves.
    Use with muse.stream(...) as s: to own the response. It closes at the end
    of the stream, on an error, on cancel, on close(), on leaving the with
    block, and when an iterator that has started is dropped. A Stream or an
    iterator that is never started is closed by Stream.close() or the with block.
    """
    _stream_check_cancel(cancel)
    if env is None:
        env = os.environ
    if opener is None:
        opener = _open_no_redirect
    if timeout is None:
        timeout = timeout_s(env)
    if not math.isfinite(timeout) or timeout <= 0:
        raise MuseError("invalid timeout")
    deadline = time.monotonic() + timeout
    headers = {"Content-Type": "application/json"}
    if route.key == "spark-open":
        try:
            headers["Authorization"] = "Api-Key " + env["BASETEN_API_KEY"]
        except KeyError as err:
            raise MuseError("auth missing") from err
    _require_http_url(route.base_url)
    try:
        if route.verify_model:

            def model_opener(req: urllib.request.Request, timeout: float | None = None) -> Any:
                return _stream_model_opened(opener, req, timeout, deadline)

            route = _verified(route, model_opener, min(_stream_wait(deadline), MODEL_LIST_TIMEOUT))
        _stream_check_cancel(cancel)
        payload = _payload(route, prompt, max_tokens)
        payload.update(stream=True, stream_options={"include_usage": True})
        req = urllib.request.Request(
            route.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )  # noqa: S310 - the scheme was checked above; the default opener blocks redirects and proxies.
        _stream_check_cancel(cancel)
        resp = _stream_open(opener, req, _stream_wait(deadline))
        try:
            result = Stream(route, resp, deadline, cancel)
            _stream_wait(deadline)
            return result
        except Exception:
            resp.close()
            raise
    except MuseError:
        raise
    except TimeoutError as err:
        raise MuseError("stream timed out") from err
    except Exception as err:
        raise MuseError("request failed") from err


def _stream_check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise MuseError("cancelled")


def _stream_wait(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise MuseError("stream timed out")
    return min(STREAM_IDLE_S, left)


def _stream_open(opener: Callable[..., Any], req: urllib.request.Request, timeout: float) -> Any:
    try:
        return opener(req, timeout=timeout)
    except urllib.error.HTTPError as err:
        err.close()
        raise


class _StreamModelReader:
    """Bound model-list body reads by the same overall deadline as the POST."""

    def __init__(self, response: Any, deadline: float) -> None:
        self._read = getattr(response, "read1", None) or response.read
        self._deadline = deadline

    def read(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            _stream_wait(self._deadline)
            raw = self._read(min(4096, size - len(chunks)))
            _stream_wait(self._deadline)
            if not raw:
                break
            chunks.extend(raw)
        return bytes(chunks)


@contextlib.contextmanager
def _stream_model_opened(
    opener: Callable[..., Any], req: urllib.request.Request, timeout: float | None, deadline: float
) -> Iterator[Any]:
    with contextlib.closing(_stream_open(opener, req, min(timeout, _stream_wait(deadline)))) as response:
        yield _StreamModelReader(response, deadline)
        _stream_wait(deadline)


class Stream:
    """Visible text from one streamed chat completion, piece by piece.

    Reads use HTTPResponse.read1 to avoid waiting to fill a large buffer. The
    opener sets a socket timeout of min(STREAM_IDLE_S, time left). The overall
    deadline is checked before every read and before yielding text. Ending can
    take one waiting read beyond timeout: up to 150 seconds (120 + 30) with the
    defaults, plus scheduling and cleanup. DNS and trickled header or chunk
    framing bytes are outside this limit, as they are for ask.

    cancel is checked before and after every read and before buffered text.
    Cancellation ends iteration and closes the response within one waiting read
    (up to 30 seconds by default, plus scheduling and cleanup). Call close()
    from the thread that iterates. Use with muse.stream(...) as s: to own the
    response. It closes at the end of the stream, on an error, on cancel, on
    close(), on leaving the with block, and when an iterator that has started
    is dropped. A Stream or an iterator that is never started is closed by
    Stream.close() or the with block. Custom openers and responses must honor
    the opener timeout and provide bounded reads.
    """

    def __init__(self, route: Route, response: Any, deadline: float, cancel: Event | None) -> None:
        self.route = route
        self.finish: str | None = None
        self.tokens: int | None = None
        self.tps: float | None = None
        self._response = response
        self._deadline = deadline
        self._cancel = cancel
        self._buffer = bytearray()
        self._total = 0
        self._closed = False
        self._read_response = getattr(response, "read1", None) or response.read

    def __iter__(self) -> Iterator[str]:
        """Yield nonempty pieces; closing or collecting a started generator closes IO."""
        try:
            while True:
                try:
                    content = self._next()
                except StopIteration:
                    return
                yield content
        finally:
            self.close()

    def close(self) -> None:
        """Close once, from the thread that iterates; repeated calls are safe."""
        if not self._closed:
            self._closed = True
            self._response.close()

    def __enter__(self) -> "Stream":
        """Return this stream for a bounded request lifetime."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Close an unfinished response when leaving the context."""
        self.close()

    def _stopped(self) -> bool:
        return self._closed or (self._cancel is not None and self._cancel.is_set())

    def _read(self) -> bytes:
        if self._stopped():
            return b""
        _stream_wait(self._deadline)
        try:
            raw = self._read_response(min(4096, MAX_RESPONSE_BYTES - self._total + 1))
        except OSError:
            if self._stopped():
                return b""
            raise
        if self._stopped():
            return b""
        _stream_wait(self._deadline)
        self._total += len(raw)
        if self._total > MAX_RESPONSE_BYTES:
            raise MuseError("response too large")
        return raw

    def _line(self) -> bytes | None:
        while True:
            end = self._buffer.find(b"\n")
            size = end if end >= 0 else len(self._buffer)
            if size > STREAM_LINE_BYTES:
                raise MuseError("stream line too large")
            if end >= 0:
                line = bytes(self._buffer[:end]).removesuffix(b"\r")
                del self._buffer[: end + 1]
                return line
            raw = self._read()
            if not raw:
                if self._buffer:
                    line = bytes(self._buffer)
                    self._buffer.clear()
                    return line
                return None
            self._buffer.extend(raw)

    def _event(self, line: bytes) -> str | None:
        if not line.startswith(b"data:"):
            return None
        data = line[5:].strip()
        if data == b"[DONE]":
            self.close()
            return None
        obj = json.loads(data.decode("utf-8"))
        if not isinstance(obj, dict) or not isinstance(obj.get("choices"), list):
            raise MuseError("bad stream object")
        self._metrics(obj)
        if not obj["choices"]:
            return None
        choice = obj["choices"][0]
        if not isinstance(choice, dict) or not isinstance(choice.get("delta"), dict):
            raise MuseError("bad stream delta")
        finish = choice.get("finish_reason")
        if finish is not None:
            if finish not in ("stop", "length"):
                raise MuseError("bad stream finish")
            self.finish = finish
        content = choice["delta"].get("content")
        if content is not None and not isinstance(content, str):
            raise MuseError("bad stream content")
        if content:
            content.encode("utf-8")  # Refuse lone surrogates even when escaped in JSON.
        return content or None

    def _metrics(self, obj: dict[str, Any]) -> None:
        usage = obj.get("usage")
        timings = obj.get("timings")
        if usage is not None:
            if not isinstance(usage, dict):
                raise MuseError("bad stream usage")
            tokens = usage.get("completion_tokens")
            if tokens is not None:
                if type(tokens) is not int or tokens < 0:
                    raise MuseError("bad stream tokens")
                self.tokens = tokens
        if timings is not None:
            if not isinstance(timings, dict):
                raise MuseError("bad stream timings")
            tps = timings.get("predicted_per_second")
            if tps is not None:
                if type(tps) not in (int, float) or not math.isfinite(tps) or tps < 0:
                    raise MuseError("bad stream speed")
                self.tps = tps

    def _next(self) -> str:
        try:
            while not self._stopped():
                _stream_wait(self._deadline)
                line = self._line()
                if self._stopped():
                    break
                if line is None:
                    if self.finish is None:
                        raise MuseError("stream ended early")
                    break
                content = self._event(line)
                if content:
                    if self._stopped():
                        break
                    _stream_wait(self._deadline)
                    return content
        except TimeoutError as err:
            if not self._stopped():
                raise MuseError("stream timed out") from err
        except (
            OSError,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            OverflowError,
            RecursionError,
            http.client.HTTPException,
        ) as err:
            stopped = self._stopped()
            self.close()
            if not stopped:
                raise MuseError("stream failed") from err
        except MuseError:
            stopped = self._stopped()
            self.close()
            if not stopped:
                raise
        self.close()
        raise StopIteration


def _verified(route, opener, timeout):
    """The route with the first of model and alternates that /models lists exactly; MuseError when none is."""
    served = _served_models(route.base_url, opener, timeout)
    for model, name in ((route.model, route.name),) + tuple(route.alternates):
        if any(type(s) is str and s == model for s in served):
            return replace(route, model=model, name=name)
    raise MuseError("model not listed")


def _served_models(base_url, opener, timeout):
    """GET base_url/models and return the listed model ids; raises on transport or format errors."""
    req = urllib.request.Request(base_url + "/models", method="GET")  # noqa: S310
    with _opened(opener, req, timeout) as resp:
        raw = _read_capped(resp)
    obj = json.loads(raw.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("not an object")
    return [item["id"] for item in obj.get("data", []) if isinstance(item, dict) and "id" in item]


FACT_KEYS = (
    "mode",
    "allowed",
    "paused",
    "hours_left",
    "reserve",
    "goal_hours",
    "battery_percent",
    "battery_health_percent",
    "usable_percent",
    "temp_c",
    "charging",
    "why_paused",
    "goal",
    "goal_met",
    "time_left",
    "paused_by_glimmer",
)
HELPER_DEADLINE_S = 115.0  # all route attempts of one Why/Plan/Ask share this; the page waits 125 s.


def facts(d):
    """Filter dict to allowed fact keys."""
    return {k: d[k] for k in FACT_KEYS if k in d}


def _routes(route):
    """Accept one Route or an ordered sequence of Routes."""
    if isinstance(route, Route):
        return [route]
    return [r for r in route if isinstance(r, Route)]


EARLY_ROUTE_S = 60  # each route but the last is capped and shares the remaining budget with later routes


def _attempts(routes, env, deadline):
    """Yield (route, timeout) pairs while the shared deadline has time left; the last route gets all of it."""
    routes = list(routes)
    for i, r in enumerate(routes):
        left = deadline - time.monotonic()
        if left < 1.0:
            return
        if i == len(routes) - 1:
            yield r, min(timeout_s(env), left)
        else:
            yield r, min(timeout_s(env), EARLY_ROUTE_S, left / (len(routes) - i))


ALWAYS_ON = frozenset(("call", "calls", "message", "messages", "alarm", "alarms"))
GLIMMER_NAMES_CAP = 200


def glimmer_paused(fact_dict):
    """Names in paused_by_glimmer that are non-empty, also listed in paused, and never calls, messages or alarms."""
    names = fact_dict.get("paused_by_glimmer")
    paused = fact_dict.get("paused")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return []
    return [
        n.strip()
        for n in names
        if n.strip() and n.strip().lower() not in ALWAYS_ON and (not isinstance(paused, list) or n in paused)
    ]


def _join_names(names):
    """Join whole names up to GLIMMER_NAMES_CAP characters."""
    shown = []
    for n in names:
        if len(", ".join(shown + [n])) > GLIMMER_NAMES_CAP:
            return ", ".join(shown) + " and other tasks" if shown else "some tasks"
        shown.append(n)
    return ", ".join(shown)


_PAUSE_RE = re.compile(r"\bpaus", re.I)
_NO_PAUSE_RE = re.compile(
    r"\b(?:not|never|no|nothing|none)\b[^.!?;]{0,40}\bpaus|n't\b[^.!?;]{0,20}\bpaus|\bpaus\w*\b[^.!?;]{0,20}\b(?:nothing|none)\b",
    re.I,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+")


def contradicts(text, fact_dict):
    """True when model text claims a pause the final decision does not have.

    A sentence that says something is paused contradicts the facts when nothing is paused, or when it
    names a task the decision still allows (calls, messages and alarms excepted: they always stay on).
    Small on-phone models restate a question's premise; the rules answer replaces such text.
    """
    paused = fact_dict.get("paused") if isinstance(fact_dict, dict) else None
    if not isinstance(paused, list):
        return False
    allowed = fact_dict.get("allowed")
    running = (
        [a.lower() for a in allowed if isinstance(a, str) and a not in paused and a.lower() not in ALWAYS_ON]
        if isinstance(allowed, list)
        else []
    )
    for sentence in _SENTENCE_RE.split(str(text)):
        if not _PAUSE_RE.search(sentence) or _NO_PAUSE_RE.search(sentence):
            continue
        if not paused:
            return True
        low = sentence.lower()
        if any(re.search(r"\b" + re.escape(name) + r"\b", low) for name in running):
            return True
    return False


def explain(fact_dict, route, env=None, opener=None):
    """Explain pause using facts via the first route that answers, with a rules fallback.

    With nothing paused there is nothing to explain, so the rules answer without waking a model, and
    an answer that contradicts the paused list falls through to the next route or the rules.
    """
    paused_by_glimmer = glimmer_paused(fact_dict)
    if fact_dict.get("paused") == [] and not paused_by_glimmer:
        route = ()
    # why_paused already reconciles the battery, goal and final task decision. Sending
    # the raw estimates too invites Glimmer to reconcile them again before answering.
    why = fact_dict.get("why_paused")
    context = {"why_paused": why.strip()} if isinstance(why, str) and why.strip() else facts(fact_dict)
    context.pop("paused_by_glimmer", None)
    if paused_by_glimmer:
        context["paused_by_glimmer"] = paused_by_glimmer
    prompt = (
        "Explain why tasks are paused in at most four short sentences. Restate the supplied reason in plain words; do not recalculate or propose a new plan. Say calls and messages are never paused. Facts: "
        + json.dumps(context)
    )
    if paused_by_glimmer:
        prompt += " If a paused_by_glimmer fact is present, also say that Muse Glimmer chose to pause those tasks."
    deadline = time.monotonic() + HELPER_DEADLINE_S
    for r, timeout in _attempts(_routes(route), env, deadline):
        try:
            # Match Ask's answer budget; Glimmer still receives reasoning headroom
            # and low effort via _payload. A token cap is not a wall-clock timeout.
            text = ask(r, prompt, env=env, opener=opener, timeout=timeout, max_tokens=200 if r.key == "local" else 400)
            if contradicts(text, fact_dict):
                continue
            used = answered_by(text, r)
            return {"text": str(text), "engine": used.name, "where": used.where, "ok": True}
        except MuseError:
            continue
    why_paused = fact_dict.get("why_paused")
    paused = fact_dict.get("paused")
    if isinstance(why_paused, str) and why_paused.strip():
        text = why_paused.strip()[:500]
        if paused_by_glimmer:
            text += " Muse Glimmer chose to pause " + _join_names(paused_by_glimmer) + "."
        text += " Calls and messages are never paused."
    elif isinstance(paused, list) and not paused:
        text = "Calls and messages are never paused. Nothing is paused right now."
    else:
        text = "Calls and messages are never paused. Other tasks are paused to save battery."
    return {"text": text, "engine": "rules", "where": "on this device", "ok": False}


class Order(list):
    """Step order plus the answering route (None when no usable answer arrived)."""

    route = None


def _parse_names(answer, steps):
    """Return the last nonempty JSON list containing only known step names."""
    decoder = json.JSONDecoder()
    steps = set(steps)
    names = None
    pos = answer.find("[")
    while pos != -1:
        with contextlib.suppress(ValueError):
            obj, _ = decoder.raw_decode(answer, pos)
            if isinstance(obj, list) and obj and all(isinstance(x, str) and x in steps for x in obj):
                names = obj
        pos = answer.find("[", pos + 1)
    if names is not None:
        return names
    raise ValueError("no list")


def plan(fact_dict, steps, route, env=None, opener=None):
    """Order steps via the first route that answers, or return the original order."""
    if not steps:
        return Order(steps)
    prompt = (
        "Order these steps from most to least useful for the goal: "
        + json.dumps(list(steps))
        + " using facts "
        + json.dumps(fact_dict)
        + ". Reply with a JSON list of step names only."
    )
    deadline = time.monotonic() + HELPER_DEADLINE_S
    for r, timeout in _attempts(_routes(route), env, deadline):
        try:
            reply = ask(r, prompt, env=env, opener=opener, timeout=timeout)
            names = _parse_names(reply, steps)
        except (MuseError, ValueError, IndexError, TypeError, RecursionError):
            continue
        seen = set()
        ordered = Order()
        steps_set = set(steps)
        for name in names:
            if name in steps_set and name not in seen:
                ordered.append(name)
                seen.add(name)
        if not seen:
            continue
        for name in steps:
            if name not in seen:
                ordered.append(name)
        ordered.route = answered_by(reply, r)
        return ordered
    return Order(steps)


def check(env=None, opener=None):
    """GET models and report reachability and whether route.model is served."""
    if env is None:
        env = os.environ
    if opener is None:
        opener = _open_no_redirect
    route = glimmer_route(env)
    endpoint = route.base_url
    lbl = label(route)
    try:
        _require_http_url(endpoint)
        served = _served_models(endpoint, opener, 5)
        return {
            "route": lbl,
            "model": route.model,
            "endpoint": endpoint,
            "reachable": True,
            "served": served,
            "model_served": route.model in served,
        }
    except (
        MuseError,
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        AttributeError,
        json.JSONDecodeError,
        RecursionError,
        http.client.HTTPException,
    ):
        return {
            "route": lbl,
            "model": route.model,
            "endpoint": endpoint,
            "reachable": False,
            "served": [],
            "model_served": False,
        }


def probe(env=None, opener=None):
    """POST one chat request and report whether visible text was returned.

    reply_model is the server's self-reported name, not proof of the weights.
    """
    if env is None:
        env = os.environ
    if opener is None:
        opener = _open_no_redirect
    route = glimmer_route(env)
    payload = {
        "model": route.model,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": DEFAULT_MAX_TOKENS + REASONING_HEADROOM,
        "reasoning_effort": "low",
        "temperature": 0,
    }
    data = json.dumps(payload).encode("utf-8")
    try:
        _require_http_url(route.base_url)
        req = urllib.request.Request(
            route.base_url + "/chat/completions", data=data, headers={"Content-Type": "application/json"}, method="POST"
        )  # noqa: S310
        probe_timeout = min(timeout_s(env), 90.0)
        started = time.monotonic()
        with _opened(opener, req, probe_timeout) as resp:
            raw = _read_capped(resp)
        latency = time.monotonic() - started
        obj = json.loads(raw.decode("utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("not an object")
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("no choices")
        content = choices[0].get("message", {}).get("content")
        text = _visible(content) if isinstance(content, str) else ""
        finish_reason = choices[0].get("finish_reason")
        finish_reason = finish_reason if isinstance(finish_reason, str) else None
        reply_model = obj.get("model")
        reply_model = reply_model[:128] if isinstance(reply_model, str) else None
    except urllib.error.HTTPError as e:
        e.close()
        return {"answered": False, "error": f"HTTP {e.code}"}
    except (MuseError, OSError):
        return {"answered": False, "error": "unreachable"}
    except (ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError, http.client.HTTPException):
        return {"answered": False, "error": "bad response"}
    out = {
        "answered": False,
        "latency_s": round(latency, 1),
        "finish_reason": finish_reason,
        "reply_model": reply_model,
    }
    if finish_reason == "length":
        # "Reply with OK." never needs the whole budget, so a cut-off reply is reasoning, not an answer.
        if not isinstance(content, str):
            return dict(out, error="bad response")
        if text:
            out["reply"] = text[:60]
        return dict(out, error="reasoning used the whole token budget")
    if text:
        return {
            "answered": True,
            "latency_s": round(latency, 1),
            "reply": text[:60],
            "finish_reason": finish_reason,
            "reply_model": reply_model,
        }
    return dict(out, error="empty reply")


CHECK_HINT = "Serve the model id muse-glimmer-30b on a loopback chat-completions server, or set OPOS_GLIMMER_URL to that server's /v1 URL."
NO_TEXT_HINT = "The server answered but gave no complete visible reply. Lower its reasoning effort, or check that it puts the answer in message.content."


def main(argv=None):
    """Handle --check receipt, plus one probe request with --probe."""
    if argv is None:
        argv = sys.argv
    if "--check" not in argv:
        return 0
    result = check()
    probing = "--probe" in argv
    if probing:
        result.update(probe())
    ok = bool(result.get("reachable") and result.get("model_served") and (result.get("answered") or not probing))
    if not result.get("model_served") or (probing and not result.get("answered")):
        no_text = result.get("model_served") and result.get("error") in (
            "empty reply",
            "reasoning used the whole token budget",
        )
        result["hint"] = NO_TEXT_HINT if no_text else CHECK_HINT
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
