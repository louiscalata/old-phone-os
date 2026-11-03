# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import http.client
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect blocked", hdrs, fp)
def _open_no_redirect(req, timeout=None):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(req, timeout=timeout)
REASONING_HEADROOM = 1200  # Glimmer spends tokens on reasoning before the visible answer.
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_TOKENS = 600
MAX_RESPONSE_BYTES = 1048576
def _read_capped(resp):
    cap = MAX_RESPONSE_BYTES
    def _try_read(obj):
        return obj.read(cap + 1)
    try:
        raw = _try_read(resp)
    except Exception:
        with resp as r:
            raw = _try_read(r)
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
    """Immutable route definition."""
    key: str
    name: str
    where: str
    base_url: str
    model: str
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
                return Route("glimmer", "Muse Glimmer", "on this device", url, "muse-glimmer-30b")
    return Route("glimmer", "Muse Glimmer", "on this device", default, "muse-glimmer-30b")
SPARK_OPEN = Route("spark-open", "Muse Spark Open", "Baseten partner inference", "https://inference.baseten.co/v1", "meta-models/muse-spark-open-1026")
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
    pass
def _require_http_url(url):
    try:
        scheme = urllib.parse.urlparse(url).scheme
    except ValueError as e:
        raise MuseError("unsupported url scheme") from e
    if scheme in ("http", "https"):
        return url
    raise MuseError("unsupported url scheme")
def ask(route, prompt, env=None, opener=None, timeout=None, max_tokens=DEFAULT_MAX_TOKENS):
    """POST chat completion and return content."""
    if env is None:
        env = os.environ
    if timeout is None:
        timeout = timeout_s(env)
    if opener is None:
        opener = _open_no_redirect
    payload = {"model": route.model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens + REASONING_HEADROOM if route.key in ("glimmer", "lan") else max_tokens, "temperature": 0.2}
    if route.key in ("glimmer", "lan"):
        payload["reasoning_effort"] = "low"
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if route.key == "spark-open":
        try:
            headers["Authorization"] = "Api-Key " + env["BASETEN_API_KEY"]
        except Exception as e:
            raise MuseError("auth missing") from e
    _require_http_url(route.base_url)
    req = urllib.request.Request(route.base_url + "/chat/completions", data=data, headers=headers, method="POST")  # noqa: S310
    try:
        resp = opener(req, timeout=timeout)
        raw = _read_capped(resp)
        obj = json.loads(raw.decode("utf-8"))
        text = obj["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise MuseError("empty response")
        return text.strip()
    except MuseError:
        raise
    except (OSError, ValueError, KeyError, IndexError, TypeError, RecursionError) as e:
        raise MuseError("request failed") from e
FACT_KEYS = ("mode", "allowed", "paused", "hours_left", "reserve", "goal_hours", "battery_percent", "battery_health_percent", "usable_percent", "temp_c", "charging", "why_paused", "goal", "goal_met", "time_left")
HELPER_DEADLINE_S = 115.0  # all route attempts of one Why/Plan/Ask share this; the page waits 125 s.
def facts(d):
    """Filter dict to allowed fact keys."""
    return {k: d[k] for k in FACT_KEYS if k in d}
def _routes(route):
    """Accept one Route or an ordered sequence of Routes."""
    if isinstance(route, Route):
        return [route]
    return [r for r in route if isinstance(r, Route)]
EARLY_ROUTE_S = 60  # each route is capped and shares the remaining budget with later routes
def _attempts(routes, env, deadline):
    """Yield (route, timeout) pairs while the shared deadline has time left."""
    routes = list(routes)
    for i, r in enumerate(routes):
        left = deadline - time.monotonic()
        if left < 1.0:
            return
        yield r, min(timeout_s(env), EARLY_ROUTE_S, left / (len(routes) - i))
def explain(fact_dict, route, env=None, opener=None):
    """Explain pause using facts via the first route that answers, with a rules fallback."""
    prompt = "In at most two plain sentences for a non-technical phone owner explain why the phone is pausing the listed tasks using only these facts: " + json.dumps(fact_dict) + ". If a why_paused fact is present, it is the reason: restate it in your own plain words. Say calls, messages and alarms are always kept."
    deadline = time.monotonic() + HELPER_DEADLINE_S
    for r, timeout in _attempts(_routes(route), env, deadline):
        try:
            text = ask(r, prompt, env=env, opener=opener, timeout=timeout)
            return {"text": text, "engine": r.name, "where": r.where, "ok": True}
        except MuseError:
            continue
    return {"text": fact_dict.get("why_paused", "Calls, messages and alarms stay on; other tasks are paused to save battery."), "engine": "rules", "where": "on this device", "ok": False}
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
    prompt = "Order these steps from most to least useful for the goal: " + json.dumps(list(steps)) + " using facts " + json.dumps(fact_dict) + ". Reply with a JSON list of step names only."
    deadline = time.monotonic() + HELPER_DEADLINE_S
    for r, timeout in _attempts(_routes(route), env, deadline):
        try:
            names = _parse_names(ask(r, prompt, env=env, opener=opener, timeout=timeout), steps)
        except (MuseError, ValueError, IndexError, TypeError):
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
        ordered.route = r
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
        req = urllib.request.Request(endpoint + "/models", method="GET")  # noqa: S310
        resp = opener(req, timeout=5)
        raw = _read_capped(resp)
        obj = json.loads(raw.decode("utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("not an object")
        data = obj.get("data", [])
        served = []
        for item in data:
            if isinstance(item, dict) and "id" in item:
                served.append(item["id"])
        return {"route": lbl, "model": route.model, "endpoint": endpoint, "reachable": True, "served": served, "model_served": route.model in served}
    except (MuseError, OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError, RecursionError, http.client.HTTPException):
        return {"route": lbl, "model": route.model, "endpoint": endpoint, "reachable": False, "served": [], "model_served": False}
def probe(env=None, opener=None):
    """POST one short chat request and report whether the model answered."""
    if env is None:
        env = os.environ
    if opener is None:
        opener = _open_no_redirect
    route = glimmer_route(env)
    payload = {"model": route.model, "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": DEFAULT_MAX_TOKENS + REASONING_HEADROOM, "reasoning_effort": "low", "temperature": 0}
    data = json.dumps(payload).encode("utf-8")
    try:
        _require_http_url(route.base_url)
        req = urllib.request.Request(route.base_url + "/chat/completions", data=data, headers={"Content-Type": "application/json"}, method="POST")  # noqa: S310
        raw = _read_capped(opener(req, timeout=30))
        choices = json.loads(raw.decode("utf-8"))["choices"]
        if not isinstance(choices, list) or not choices:
            raise ValueError("no choices")
        content = choices[0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty response")
    except urllib.error.HTTPError as e:
        e.close()
        return {"answered": False, "error": f"HTTP {e.code}"}
    except (MuseError, OSError):
        return {"answered": False, "error": "unreachable"}
    except (ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError, http.client.HTTPException):
        return {"answered": False, "error": "bad response"}
    return {"answered": True}
CHECK_HINT = "Serve the model id muse-glimmer-30b on a loopback chat-completions server, or set OPOS_GLIMMER_URL to that server's /v1 URL."
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
        result["hint"] = CHECK_HINT
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0 if ok else 1
if __name__ == "__main__":
    raise SystemExit(main())
