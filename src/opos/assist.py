# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import http.client
import ipaddress
import json
import os
import re
import sys
import sysconfig
import time
import urllib.parse
import urllib.request
from pathlib import Path
from . import muse
PHONE_LAUNCHER = "/usr/bin/opos-kiosk"  # installed by the opos postmarketOS package only
def _phone_package(module_file=__file__):
    """True when opos runs from the postmarketOS package: it copies src/opos into the system interpreter's
    purelib and installs the kiosk launcher.

    A source checkout, the desktop installer and tests import opos from <checkout>/src instead. A virtual
    environment (sys.prefix differs from sys.base_prefix) or a pip install without the launcher is not the package.
    """
    try:
        if sys.prefix != sys.base_prefix:
            return False
        here = Path(module_file).resolve().parent.parent
        return here in {Path(sysconfig.get_path(k)).resolve() for k in ("purelib", "platlib")} and Path(PHONE_LAUNCHER).is_file()
    except (OSError, ValueError, TypeError, KeyError):
        return False
PHONE_BUILD = _phone_package()
DEFAULT_LOCAL_URL = "http://127.0.0.1:8080/v1"
MIN_MB_3B = 4096  # free RAM in MB needed for 3B Q4_K_M build with context
REST_BELOW_USABLE = 15  # governor's critical line: below 15% usable charge the helper rests
def is_resting(context):
    """Rest the helper only when the battery itself is critical.

    The router also reports CRITICAL when a daily goal forces every optional task to pause,
    even at a healthy charge. Asking then costs the phone one request, so the helper answers.
    """
    if not isinstance(context, dict) or context.get("mode") != "CRITICAL" or context.get("charging"):
        return False
    usable = context.get("usable_percent")
    return not isinstance(usable, (int, float)) or isinstance(usable, bool) or usable < REST_BELOW_USABLE
MIN_MB_1B = 1536  # free RAM in MB needed for 1B Q4_K_M build with context
LOCAL_MODELS = {"llama-3.2-1b-instruct": "Meta Llama 3.2 1B", "llama-3.2-3b-instruct": "Meta Llama 3.2 3B"}
def free_mb(path="/proc/meminfo"):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None
def recommend(free):
    if free is not None and free < MIN_MB_1B:
        return None
    if free is not None and free >= MIN_MB_3B:
        return "llama-3.2-3b-instruct"
    return "llama-3.2-1b-instruct"
def local_route(env=None, free=None):
    """The optional Llama server on this machine.

    OPOS_LOCAL_URL (http on loopback) is the owner's choice and is used as set. The default port 8080 is
    common for other programs, so that route is marked verify_model: muse.ask sends nothing to it until
    its /models list contains one of the LOCAL_MODELS ids exactly, and the answer names the size listed.
    """
    if env is None:
        env = os.environ
    if free is None:
        return None
    model = recommend(free)
    if model is None:
        return None
    url = None
    env_url = env.get("OPOS_LOCAL_URL")
    if env_url:
        with contextlib.suppress(Exception):
            p = urllib.parse.urlparse(env_url)
            if p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost"):
                url = env_url
    # OPOS_ON_PHONE=1: set by deploy/termux-setup.sh, where the packaged-install check cannot see the phone.
    where = "on this phone" if PHONE_BUILD or env.get("OPOS_ON_PHONE") == "1" else "on this computer"
    # The server already holds whichever size it loaded, so either exact id verifies it; the free-memory pick goes first.
    other = tuple((m, n) for m, n in LOCAL_MODELS.items() if m != model)
    return muse.Route("local", LOCAL_MODELS[model], where, url or DEFAULT_LOCAL_URL, model, verify_model=url is None, alternates=other if url is None else ())
# RFC 1918 (10/8, 172.16/12, 192.168/16) built from octets, plus IPv6 unique-local fc00::/7.
_LAN_NETS = (
    ipaddress.IPv4Network((bytes((10, 0, 0, 0)), 8)),
    ipaddress.IPv4Network((bytes((172, 16, 0, 0)), 12)),
    ipaddress.IPv4Network((bytes((192, 168, 0, 0)), 16)),
    ipaddress.IPv6Network("fc00::/7"),
)
def _lan_address(host):
    """A private LAN address, or loopback for a reverse tunnel to a computer you own (docs/duo-usb-install.md).

    Link-local, documentation, benchmark, reserved and unspecified addresses are refused.
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or any(ip in net for net in _LAN_NETS)
def lan_route(env=None):
    if env is None:
        env = os.environ
    url = env.get("OPOS_LAN_URL")
    if not url:
        return None
    try:
        p = urllib.parse.urlparse(url)
        host = p.hostname
    except ValueError:
        return None
    if p.scheme != "http":
        return None
    if host is None or not _lan_address(host):
        return None
    return muse.Route("lan", "Muse Glimmer", "on a computer you own", url, "muse-glimmer-30b")
def explain_route(env=None, free=None):
    return explain_routes(env, free)[0]
def explain_routes(env=None, free=None):
    """Why and Plan keep their LAN-first precedence, then fall back through the other Ask routes."""
    lan = lan_route(env)
    rest = [r for r in ask_routes("", env, free) if r != lan]
    return ([lan] if lan is not None else []) + rest
def ask_routes(question, env=None, free=None):
    lan = lan_route(env)
    local = local_route(env, free)
    order = [lan, local] if is_hard(question) else [local, lan]
    order = [r for r in order if r is not None]
    order.append(muse.glimmer_route(env))
    return order
def ask_route(env=None, free=None, question=""):
    return ask_routes(question, env, free)[0]
def search(question, opener=None, timeout=8):
    if opener is None:
        opener = muse._open_no_redirect  # ignores environment proxies and does not follow redirects
    params = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": question, "srlimit": 3, "format": "json", "srprop": "snippet"})
    url = "https://en.wikipedia.org/w/api.php?" + params
    req = urllib.request.Request(url, headers={"User-Agent": "OPOS-helper/1.0"})
    try:
        with muse._opened(opener, req, timeout) as resp:
            raw = resp.read(65536)
        obj = json.loads(raw.decode("utf-8"))
        items = obj["query"]["search"]
        out = []
        for item in items[:3]:
            title = item["title"]
            snippet = re.sub(r"<[^>]+>", "", item["snippet"])[:300]
            wiki_url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
            out.append({"title": title, "url": wiki_url, "snippet": snippet})
        return out
    except (OSError, ValueError, KeyError, TypeError, RecursionError, http.client.HTTPException):
        return []
def is_hard(question):
    return len(question.split()) >= 20
_CITE_RE = re.compile(r"\s*\[\d+\]")
def _strip_stray_citations(text, sources):
    """Drop [1]-style markers the model invents when no sources were listed."""
    if sources:
        return text
    return _CITE_RE.sub("", text).strip()
def _with_idk_hint(text, web):
    norm = text.lower().replace("’", "'").rstrip(". ")
    if norm in ("i don't know", "i do not know", "i dont know"):
        hint = " Try asking about this phone, for example what is paused"
        if not web:
            hint += ", or tick Search Wikipedia for general questions"
        return text + hint + "."
    return text
_PAUSE_Q = re.compile(r"\bpaus\w*|\bstopped\b|\bturned off\b|\bwhy is \w+(?: \w+)? off\b", re.I)
_TIME_Q = re.compile(r"\bhow long\b|\btime left\b|\brun out\b|\blast\b|\bdie\b|\bbattery life\b", re.I)
RULES_ENGINE = "battery rules"
def _rules_reply(text):
    return {"ok": True, "text": text, "engine": RULES_ENGINE, "where": "on this phone", "sources": []}
def rules_answer(question, facts):
    """The rules' own answer to a question about pauses or time left, or None.

    These facts are exact in the final decision; a small on-phone model restates a question's
    premise or misreads a goal time as a duration, so Ask gives this instead of waking it.
    """
    if not isinstance(facts, dict):
        return None
    why = facts.get("why_paused")
    if _PAUSE_Q.search(question) and isinstance(why, str) and why.strip():
        return _rules_reply(why.strip()[:500] + " Calls and messages are never paused.")
    left, goal = facts.get("time_left"), facts.get("goal")
    if _TIME_Q.search(question) and isinstance(left, str) and left.strip():
        if facts.get("charging") is True:
            text = "The phone is charging, so the battery is filling, not running down."
        else:
            text = left.strip().rstrip(".") + " at the current rate."
            if isinstance(goal, str) and goal:
                text += f" That reaches your {goal} goal." if facts.get("goal_met") is True else f" That falls short of your {goal} goal, so the phone pauses less important tasks."
        return _rules_reply(text + " Calls and messages are never paused.")
    return None
def answer(question, web=False, env=None, opener=None, free=None, context=None):
    if env is None:
        env = os.environ
    q = question.strip()[:500]
    if not q:
        return {"ok": False, "text": "Ask a question.", "engine": "none", "where": "", "sources": []}
    if is_resting(context):
        return {"ok": False, "text": "Battery is critical, so the helper is resting to save power. Calls and messages are never paused. Ask again once the battery recovers or the phone is charging.", "engine": "rules", "where": "on this phone", "sources": []}
    use_facts = isinstance(context, dict) and len(context) > 0
    ruled = rules_answer(q, context) if use_facts and not web else None
    sources = search(q, opener) if web else []
    if use_facts:
        prompt = "Answer in at most four short sentences. Use the phone facts for questions about the phone and any listed sources for other information; cite sources as [1], [2]. When asked why something is paused, off or not working, the why_paused fact is the reason: restate it in plain words. If neither the facts nor the sources covers the question, say you do not know.\n"
    else:
        prompt = "Answer in at most four short sentences. If sources are listed, use only them and cite them as [1], [2]. If you do not know, say so.\n"
    for i, s in enumerate(sources, 1):
        prompt += f"[{i}] {s['title']}: {s['snippet']}\n"
    if use_facts:
        try:
            facts = json.dumps(context, sort_keys=True)
        except (TypeError, ValueError, RecursionError):
            return {"ok": False, "text": "Invalid phone facts.", "engine": "none", "where": "", "sources": []}
        prompt += "Facts about this phone (use them for questions about the phone): " + facts + "\n"
        if muse.glimmer_paused(context):
            prompt += "If asked about a task listed in paused_by_glimmer, say that Muse Glimmer chose to pause it.\n"
    prompt += "\nQuestion: " + q
    deadline = time.monotonic() + muse.HELPER_DEADLINE_S
    contradicted = False
    for route, timeout in muse._attempts(ask_routes(q, env, free), env, deadline):
        if route.key == "local" and ruled is not None:
            # The small on-phone model gets these facts wrong; Glimmer on a computer may still restate them.
            return ruled
        try:
            text = muse.ask(route, prompt, env=env, opener=opener, timeout=timeout, max_tokens=200 if route.key == "local" else 400)
            if use_facts and muse.contradicts(text, context):
                contradicted = True
                continue
            used = muse.answered_by(text, route)
            text = _with_idk_hint(_strip_stray_citations(str(text), sources), web)
            return {"ok": True, "text": text, "engine": used.name, "where": used.where, "sources": [{"title": s["title"], "url": s["url"]} for s in sources]}
        except muse.MuseError:
            continue
    why = context.get("why_paused") if use_facts else None
    if contradicted and isinstance(why, str) and why.strip():
        # The model claimed a pause the phone does not have; the rules' own sentence is the answer.
        return _rules_reply(why.strip()[:500] + " Calls and messages are never paused.")
    if sources:
        return {"ok": True, "text": "The helper did not answer, so here are the sources I found.", "engine": "none", "where": "", "sources": [{"title": s["title"], "url": s["url"]} for s in sources]}
    detail = "The helper did not answer. "
    if free is not None and free < MIN_MB_1B:
        return {"ok": False, "text": detail + f"The phone has only about {free} MB of free memory and the helper needs {MIN_MB_1B} MB.", "engine": "none", "where": "", "sources": []}
    return {"ok": False, "text": detail + "The battery rules still work.", "engine": "none", "where": "", "sources": []}
