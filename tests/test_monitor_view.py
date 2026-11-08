# SPDX-License-Identifier: GPL-3.0-or-later
"""The view model: truth about this phone, an untrusted remote document, caching and thread safety."""
import json
import re
import threading
import unittest
import unittest.mock

from opos import monitor


def ip4(*octets):
    return ".".join(str(o) for o in octets)

AGIW_ROW = {"host": "mac", "platform": "mac", "state": "up", "reasonCode": "observed", "trust": "local", "ageSeconds": 1.2,
            "freshForSeconds": 3, "modelsComplete": True, "loadedCount": 2, "busyCount": 1,
            "promptTokensPerSecond": 812.4, "generationTokensPerSecond": 4.33, "handsOff": False}
PC_ROW = {"host": "pc", "platform": "windows", "state": "unknown", "reasonCode": "not_connected", "trust": "unverified",
          "ageSeconds": 41, "freshForSeconds": 30, "modelsComplete": False, "loadedCount": None, "busyCount": None,
          "promptTokensPerSecond": None, "generationTokensPerSecond": None, "handsOff": None}


def snapshot(*rows, models=None):
    return {"host": "mac", "machines": list(rows), "models": models if models is not None else [
        {"name": "gemma-4", "loaded": True}, {"name": "qwen-27b", "loaded": False}]}


def _loopback_ok():
    import socket
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(remote=None, probe=("none", []), env=None, fetch=None, clock=None):
    environment = dict(env or {})
    if remote is not None:
        environment["OPOS_AGIW_URL"] = "http://" + ip4(192, 168, 1, 5) + ":8765"
    calls = {"fetch": 0, "probe": 0}

    def fetcher(address):
        calls["fetch"] += 1
        if callable(fetch):
            return fetch(address)
        if isinstance(remote, monitor.FetchError):
            raise remote
        return remote

    def prober(_env):
        calls["probe"] += 1
        return probe
    return monitor.Monitor(environment, fetch=fetcher, probe=prober, clock=clock or Clock()), calls


def section(view, name):
    return next(s for s in view["sections"] if s["id"] == name)


class ThisDuoTests(unittest.TestCase):
    def test_no_model_is_said_only_when_nothing_listens(self):
        view, _ = make(probe=("none", []))
        duo = section(view.snapshot(), "duo")
        self.assertEqual(duo["status"], "NO MODEL")
        self.assertIn("Nothing is listening on 127.0.0.1:8080", duo["lines"][0])
        for kind in ("unknown", "empty"):
            view, _ = make(probe=(kind, []))
            duo = section(view.snapshot(), "duo")
            self.assertNotIn("Nothing is listening", " ".join(duo["lines"]), kind)
        self.assertEqual(section(make(probe=("unknown", []))[0].snapshot(), "duo")["status"], "UNKNOWN")
        self.assertEqual(section(make(probe=("empty", []))[0].snapshot(), "duo")["status"], "NO MODEL LISTED")

    def test_a_listed_model_is_named(self):
        duo = section(make(probe=("model", ["llama-3.2-1b-instruct"]))[0].snapshot(), "duo")
        self.assertEqual(duo["status"], "MODEL LISTED")
        self.assertIn("llama-3.2-1b-instruct", duo["lines"][0])
        self.assertIn("127.0.0.1:8080", duo["lines"][0], "the address that was checked is named")

    def test_a_probe_that_raises_is_unknown_not_a_crash(self):
        def boom(_env):
            raise RuntimeError("x")
        view = monitor.Monitor({}, probe=boom, clock=Clock()).snapshot()
        self.assertEqual(section(view, "duo")["status"], "UNKNOWN")

    def test_battery_numbers_pass_through_only_when_they_are_valid(self):
        view, _ = make()
        facts = dict(map(tuple, section(view.snapshot({"percent": 54.2, "healthPercent": 87, "source": "sysfs"}), "duo")["facts"]))
        self.assertEqual((facts["Battery"], facts["Battery health"], facts["Battery source"]), ("54%", "87%", "sysfs"))
        for bad in (None, {}, [], "x", {"percent": None}, {"percent": "54"}, {"percent": True}, {"percent": float("nan")},
                    {"percent": -1}, {"percent": 101}, {"percent": float("inf")}, {"source": "<b>x</b>"}):
            facts = dict(map(tuple, section(make()[0].snapshot(bad), "duo")["facts"]))
            self.assertEqual(facts["Battery"], "Not reported", bad)
            self.assertEqual(facts["Battery source"], "Not reported", bad)

    def test_zero_percent_is_a_real_zero_and_missing_is_not(self):
        facts = dict(map(tuple, section(make()[0].snapshot({"percent": 0}), "duo")["facts"]))
        self.assertEqual(facts["Battery"], "0%")


class RemoteTests(unittest.TestCase):
    def test_unset_means_nothing_is_fetched(self):
        view, calls = make()
        remote = section(view.snapshot(), "remote")
        self.assertEqual(remote["status"], "NOT CONFIGURED")
        self.assertEqual(calls["fetch"], 0)

    def test_a_good_document_is_shown_with_its_source_and_age(self):
        view, calls = make(remote=snapshot(AGIW_ROW, PC_ROW))
        remote = section(view.snapshot(), "remote")
        self.assertEqual(remote["status"], "LIVE")
        self.assertIn("AGIW monitor at " + ip4(192, 168, 1, 5) + ":8765, fetched 0 s ago.", remote["lines"][0])
        self.assertIn("gemma-4 · loaded", " ".join(remote["lines"]))
        self.assertIn("went silent", remote["pre"])
        facts = dict(map(tuple, remote["facts"]))
        self.assertEqual((facts["MAC models"], facts["MAC prompts"], facts["PC"], facts["PC models"]),
                         ("2 loaded (1 busy)", "812 tok/s", "UNKNOWN · went silent", "Not measured"))
        self.assertIn("never observed", monitor.peer_map(monitor.machine_rows(snapshot({**PC_ROW, "reasonCode": "not_observed"}, AGIW_ROW)), True))
        self.assertEqual(calls["fetch"], 1)

    def test_an_old_or_unverified_own_sample_is_stale_not_live(self):
        old = {**AGIW_ROW, "ageSeconds": 99}
        self.assertEqual(section(make(remote=snapshot(old))[0].snapshot(), "remote")["status"], "STALE")
        no_age = {k: v for k, v in AGIW_ROW.items() if k not in ("ageSeconds", "freshForSeconds")}
        self.assertEqual(section(make(remote=snapshot(no_age))[0].snapshot(), "remote")["status"], "STALE")
        self.assertEqual(section(make(remote=snapshot())[0].snapshot(), "remote")["status"], "UNKNOWN")
        self.assertEqual(section(make(remote={})[0].snapshot(), "remote")["status"], "UNKNOWN")

    def test_every_fetch_failure_has_fixed_words(self):
        for code, (word, text) in monitor.FETCH_WORDS.items():
            if code == "bad-config":
                continue
            view, _ = make(remote=monitor.FetchError(code))
            remote = section(view.snapshot(), "remote")
            self.assertEqual(remote["status"], word, code)
            self.assertIn(text, remote["lines"][0], code)
            self.assertIn("Nothing about other machines is known.", remote["lines"], code)
            self.assertEqual(remote["facts"], [])

    def test_an_unexpected_exception_is_unreachable_never_a_crash(self):
        def boom(_remote):
            raise ValueError("secret detail")
        view, _ = make(remote={}, fetch=boom)
        remote = section(view.snapshot(), "remote")
        self.assertEqual(remote["status"], "UNREACHABLE")
        self.assertNotIn("secret", json.dumps(remote))

    def test_a_bad_address_in_the_settings_is_not_fetched(self):
        for url in ("http://user@" + ip4(192, 168, 1, 5), "ftp://x", "http://" + ip4(192, 168, 1, 5) + "/api/snapshot", "x"):
            view, calls = make(env={"OPOS_AGIW_URL": url})
            remote = section(view.snapshot(), "remote")
            self.assertEqual(remote["status"], "NOT CONFIGURED")
            self.assertIn("not usable", remote["lines"][0])
            self.assertNotIn(url, json.dumps(remote))
            self.assertEqual(calls["fetch"], 0)

    def test_unknown_is_never_zero(self):
        rows = monitor.machine_rows(snapshot({"host": "x", "state": "weird"}, {"state": "up", "modelsComplete": True}))
        self.assertEqual([r["state"] for r in rows], ["UNKNOWN", "UP"])
        self.assertIsNone(rows[0]["loaded"])
        self.assertIsNone(rows[1]["loaded"], "a complete list without a count is not a zero count")
        text = monitor.peer_map(rows, True)
        self.assertNotRegex(text, r"\b0 loaded\b")
        facts = dict(map(tuple, section(make(remote=snapshot({"host": "x", "state": "up"}))[0].snapshot(), "remote")["facts"]))
        self.assertEqual(facts["X prompts"], "Not measured")
        self.assertEqual(facts["X age"], "Unknown")
        self.assertEqual(facts["X models"], "Model list incomplete")
        self.assertEqual(facts["X"], "UP · observed")

    def test_a_count_is_trusted_only_when_the_list_is_complete(self):
        row = monitor.machine_rows(snapshot({**AGIW_ROW, "modelsComplete": False}))[0]
        self.assertIsNone(row["loaded"])
        self.assertIsNone(monitor.machine_rows(snapshot({**AGIW_ROW, "loadedCount": 1.5}))[0]["loaded"])
        self.assertIsNone(monitor.machine_rows(snapshot({**AGIW_ROW, "loadedCount": True}))[0]["loaded"])
        self.assertEqual(monitor.machine_rows(snapshot({**AGIW_ROW, "loadedCount": 0}))[0]["loaded"], 0)

    def test_hostile_values_never_become_markup_or_raw_json(self):
        evil = "<img src=x onerror=alert(1)>"
        row = {**AGIW_ROW, "host": evil, "platform": evil, "reasonCode": evil, "trust": evil, "state": evil}
        document = snapshot(row, {**PC_ROW, "host": "a" * 500, "reasonCode": "A;B"}, models=[
            {"name": evil, "loaded": True}, {"name": "ok-model", "loaded": "yes"}, "string", None, 5, {"id": "from-id"}])
        document["extra"] = {"password": "hunter2", "token": "t0ken"}
        document["sources"] = [{"id": evil}]
        text = json.dumps(make(remote=document)[0].snapshot(), ensure_ascii=False)
        for forbidden in ("<", ">", "onerror", "hunter2", "t0ken", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"):
            self.assertNotIn(forbidden, text, forbidden)
        self.assertIn("ok-model · unknown", text)
        self.assertIn("from-id", text)

    def test_row_and_model_counts_are_capped(self):
        rows = [{**AGIW_ROW, "host": f"h{i}"} for i in range(50)]
        view = make(remote=snapshot(*rows, models=[{"name": f"m{i}", "loaded": True} for i in range(50)]))[0].snapshot()
        remote = section(view, "remote")
        self.assertLessEqual(len(monitor.machine_rows(snapshot(*rows))), monitor.MAX_MACHINES)
        self.assertLessEqual(len(remote["facts"]), 40)
        self.assertLessEqual(remote["pre"].count("\n"), monitor.MAX_MACHINES + 2)
        self.assertLessEqual(len(json.dumps(view)), 8000)

    def test_numbers_are_clamped_and_formatted(self):
        self.assertEqual(monitor.clean_number(5, 0, 10), 5)
        for bad in (-1, 11, True, False, "5", None, float("nan"), float("inf"), float("-inf"), [], {}):
            self.assertIsNone(monitor.clean_number(bad, 0, 10), repr(bad))
        self.assertEqual(monitor._rate(4.33), "4.3 tok/s")
        self.assertEqual(monitor._rate(812.4), "812 tok/s")
        self.assertEqual(monitor._rate(0), "0.0 tok/s")
        self.assertEqual(monitor._rate(-3), "Not measured")
        self.assertEqual(monitor._rate(None), "Not measured")

    def test_the_peer_map_matches_the_agiw_wording(self):
        rows = monitor.machine_rows(snapshot(AGIW_ROW, PC_ROW))
        self.assertEqual(monitor.peer_map(rows, True).split("\n"), [
            "MAC           UP       2 loaded / 1 busy · local · 1 s",
            " |-?- PC       UNKNOWN  went silent · unverified · 41 s"])
        self.assertTrue(monitor.peer_map(rows, False).endswith("last sample, not live"))
        self.assertEqual(monitor.peer_map([], True), "MACHINES  UNKNOWN\n          no machine rows in this sample")
        self.assertIn("no other machine in this sample", monitor.peer_map(rows[:1], True))

    def test_reason_words_cover_every_agiw_code_and_fall_back_safely(self):
        for code in ("not_observed", "not_connected", "share_unmounted", "clock_skew", "stale", "reported_down"):
            self.assertIn(code, monitor.REASON_WORDS)
        self.assertEqual(monitor.reason_words("not_observed"), "never observed")
        self.assertEqual(monitor.reason_words("some_new_code"), "some new code")
        for bad in (None, 5, "", "Drop TABLE;", "A" * 5, "x" * 41):
            self.assertEqual(monitor.reason_words(bad), "not observed", repr(bad))
        self.assertEqual(monitor.reason_words("constructor"), "constructor")


class CacheTests(unittest.TestCase):
    def test_one_collection_serves_calls_inside_the_window(self):
        clock = Clock()
        view, calls = make(remote=snapshot(AGIW_ROW), clock=clock)
        for _ in range(5):
            view.snapshot()
        self.assertEqual((calls["fetch"], calls["probe"]), (1, 1))
        clock.now += monitor.CACHE_SECONDS + 0.1
        view.snapshot()
        self.assertEqual((calls["fetch"], calls["probe"]), (2, 2))

    def test_ages_are_measured_on_the_phones_monotonic_clock(self):
        clock = Clock()
        view, _ = make(remote=snapshot(AGIW_ROW), clock=clock)
        view.snapshot()
        clock.now += 1.2
        later = view.snapshot()
        self.assertEqual(later["updatedAgoSeconds"], 1)
        self.assertIn("fetched 1 s ago", section(later, "remote")["lines"][0])
        self.assertNotIn("1970", json.dumps(later))

    def test_only_one_caller_collects_while_others_get_the_last_answer(self):
        clock = Clock()
        gate, started = threading.Event(), threading.Event()
        calls = []

        def slow(_remote):
            calls.append(1)
            started.set()
            gate.wait(5)
            return snapshot(AGIW_ROW)
        view, _ = make(remote={}, fetch=slow, clock=clock)
        results = {}
        first = threading.Thread(target=lambda: results.setdefault("first", view.snapshot()))
        first.start()
        self.assertTrue(started.wait(5))
        during = view.snapshot()
        self.assertEqual(section(during, "duo")["status"], "NO MODEL", "the phone's own answer does not wait for the remote fetch")
        self.assertEqual(section(during, "remote")["status"], "CHECKING")
        self.assertEqual(len(calls), 1, "a second caller must not start a second fetch")
        gate.set()
        first.join(5)
        self.assertEqual(section(results["first"], "remote")["status"], "LIVE")
        clock.now += 5
        gate.clear()
        gate.set()
        again = view.snapshot()
        self.assertEqual(len(calls), 2)
        self.assertEqual(section(again, "remote")["status"], "LIVE")

    def test_a_stale_answer_is_served_while_a_refresh_is_running(self):
        clock = Clock()
        gate, started = threading.Event(), threading.Event()
        count = [0]

        def fetch(_remote):
            count[0] += 1
            if count[0] == 2:
                started.set()
                gate.wait(5)
            return snapshot(AGIW_ROW)
        view, _ = make(remote={}, fetch=fetch, clock=clock)
        view.snapshot()
        clock.now += 10
        worker = threading.Thread(target=view.snapshot)
        worker.start()
        self.assertTrue(started.wait(5))
        served = view.snapshot()
        self.assertEqual(served["updatedAgoSeconds"], 0, "the phone's own answer was just taken")
        self.assertIn("fetched 10 s ago", section(served, "remote")["lines"][0], "the remote's answer keeps its own age")
        gate.set()
        worker.join(5)

    def test_many_threads_never_raise_and_fetch_once(self):
        view, calls = make(remote=snapshot(AGIW_ROW))
        errors = []

        def hit():
            try:
                for _ in range(50):
                    json.dumps(view.snapshot({"percent": 50}))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=hit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(calls["fetch"], 1)

    def test_the_view_is_json_safe_and_plain_text(self):
        text = json.dumps(make(remote=snapshot(AGIW_ROW, PC_ROW))[0].snapshot({"percent": 50, "source": "sysfs"}))
        self.assertEqual(json.loads(text)["version"], 1)
        self.assertNotRegex(text, r"NaN|Infinity")
        self.assertTrue(re.search(r'"sections"', text))


class LocalProbeTests(unittest.TestCase):
    def test_the_local_url_is_loopback_http_only(self):
        self.assertEqual(monitor.local_models_url({}), ("127.0.0.1", 8080, "/v1/models"))
        self.assertEqual(monitor.local_models_url({"OPOS_LOCAL_URL": "http://127.0.0.1:1236/v1"}), ("127.0.0.1", 1236, "/v1/models"))
        self.assertEqual(monitor.local_models_url({"OPOS_LOCAL_URL": "http://localhost:9000"}), ("localhost", 9000, "/models"))
        for bad in ("http://" + ip4(192, 168, 1, 2) + ":8080/v1", "https://127.0.0.1:8080/v1", "http://evil.example/v1", "http://[::1]:9000/v1",
                    "http://user@127.0.0.1:8080/v1", "http://127.0.0.1:notaport/v1", "file:///x"):
            self.assertEqual(monitor.local_models_url({"OPOS_LOCAL_URL": bad}), ("127.0.0.1", 8080, "/v1/models"), bad)

    @unittest.skipUnless(_loopback_ok(), "cannot bind 127.0.0.1 here")
    def test_nothing_listening_is_none_and_garbage_is_unknown(self):
        import socket
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.assertEqual(monitor.probe_local({"OPOS_LOCAL_URL": f"http://127.0.0.1:{port}/v1"}), ("none", []))


class StaleTests(unittest.TestCase):
    """A hung or slow collection must never keep showing a green answer."""

    def hung(self, remote_doc):
        clock = Clock()
        gate, started = threading.Event(), threading.Event()
        calls = []

        def fetch(_remote):
            calls.append(1)
            if len(calls) == 2:
                started.set()
                gate.wait(10)
            return remote_doc
        view, _ = make(remote={}, fetch=fetch, probe=("model", ["m1"]), clock=clock)
        first = view.snapshot()
        clock.now += 3
        worker = threading.Thread(target=view.snapshot, daemon=True)
        worker.start()
        self.assertTrue(started.wait(5))
        return view, clock, gate, worker, first

    def test_a_collection_that_hangs_downgrades_what_it_last_said(self):
        view, clock, gate, worker, first = self.hung(snapshot(AGIW_ROW))
        self.assertEqual(section(first, "duo")["status"], "MODEL LISTED")
        self.assertEqual(section(first, "remote")["status"], "LIVE")
        clock.now += monitor.STALE_AFTER + 1
        stale = view.snapshot()
        duo, remote = section(stale, "duo"), section(stale, "remote")
        self.assertEqual((duo["status"], duo["glyph"]), ("UNKNOWN", "[?]"))
        self.assertIn("not current", " ".join(duo["lines"]))
        self.assertNotIn("m1", " ".join(duo["lines"]))
        self.assertEqual(remote["status"], "STALE")
        self.assertIn("not live", " ".join(remote["lines"]))
        self.assertIn("not live", remote["pre"])
        gate.set()
        worker.join(5)

    def test_right_at_the_limit_it_is_still_current_and_each_part_ages_from_its_own_stamp(self):
        # A long freshFor on purpose: this test is about the collection's own age (STALE_AFTER). The row's age against its
        # freshFor, with the cache in the picture, is FreshnessIncludesFetchAgeTests below.
        view, clock, gate, worker, _first = self.hung(snapshot(dict(AGIW_ROW, freshForSeconds=3600)))
        # The remote was last heard at t=1000 and the phone last probed at t=1003 (the second collection started).
        clock.now = 1000 + monitor.STALE_AFTER
        current = view.snapshot()
        self.assertEqual(section(current, "duo")["status"], "MODEL LISTED")
        self.assertEqual(section(current, "remote")["status"], "LIVE")
        clock.now += 1
        later = view.snapshot()
        self.assertEqual(section(later, "duo")["status"], "MODEL LISTED", "the phone's own answer is younger than the remote's")
        self.assertEqual(section(later, "remote")["status"], "STALE")
        clock.now = 1003 + monitor.STALE_AFTER + 1
        self.assertEqual(section(view.snapshot(), "duo")["status"], "UNKNOWN")
        gate.set()
        worker.join(5)

    def test_the_local_answer_is_aged_from_when_the_probe_ran_not_from_the_end_of_the_remote_fetch(self):
        clock = Clock()

        def slow_fetch(_remote):
            clock.now += 5
            return snapshot(AGIW_ROW)
        view, _ = make(remote={}, fetch=slow_fetch, probe=("model", ["m1"]), clock=clock)
        out = view.snapshot()
        facts = dict(map(tuple, section(out, "duo")["facts"]))
        self.assertEqual(facts["Model server checked"], "5 s ago")
        # r5: the remote's age counts from when its fetch started, so a 5 s fetch is "fetched 5 s ago" the moment it lands.
        self.assertIn("fetched 5 s ago", section(out, "remote")["lines"][0])
        self.assertEqual(out["updatedAgoSeconds"], 0)

    def test_the_first_look_publishes_the_phones_answer_before_the_remote_fetch_ends(self):
        gate, started = threading.Event(), threading.Event()

        def fetch(_remote):
            started.set()
            gate.wait(10)
            return snapshot(AGIW_ROW)
        view, _ = make(remote={}, fetch=fetch, probe=("model", ["m1"]))
        worker = threading.Thread(target=view.snapshot, daemon=True)
        worker.start()
        self.assertTrue(started.wait(5))
        early = view.snapshot()
        self.assertEqual(section(early, "duo")["status"], "MODEL LISTED")
        self.assertEqual(section(early, "remote")["status"], "CHECKING")
        gate.set()
        worker.join(5)
        self.assertEqual(section(view.snapshot(), "remote")["status"], "LIVE")


class NeverRaisesTests(unittest.TestCase):
    def test_a_list_or_object_where_a_word_belongs_is_unknown_not_an_exception(self):
        for field in ("state", "platform", "host", "reasonCode", "trust", "ageSeconds", "freshForSeconds", "modelsComplete",
                      "loadedCount", "busyCount", "promptTokensPerSecond", "generationTokensPerSecond", "handsOff"):
            for value in ([], {}, [1], {"a": 1}, [[]], None, 7, 1.5, True, "", "x" * 500, "\n", float("nan")):
                document = {"machines": [{**AGIW_ROW, field: value}, {field: value}], "models": [{"name": value, "loaded": value}]}
                out = make(remote=document)[0].snapshot({"percent": value, "source": value})
                self.assertEqual({s["id"] for s in out["sections"]}, {"duo", "remote"}, (field, value))
                json.dumps(out)
        for document in ({"machines": [{"platform": []}]}, {"machines": [{"state": {}}]}, {"machines": {}}, {"machines": "x"},
                         {"models": {}}, {"models": [[], {}, None, 5]}, {"machines": [[], None, 5, "x"]}):
            out = make(remote=document)[0].snapshot()
            self.assertEqual(len(out["sections"]), 2, document)

    def test_a_small_type_fuzz_never_raises_and_always_keeps_the_local_section(self):
        import itertools
        values = [None, True, 0, -1, 2 ** 70, 1.5, float("inf"), "", "a", "a\n", [], [None], {}, {"x": []}]
        fields = ["state", "platform", "host", "reasonCode", "trust", "ageSeconds", "modelsComplete", "loadedCount"]
        for combo in itertools.product(values, repeat=3):
            row = {fields[i]: combo[i] for i in range(3)}
            row.update({fields[3 + i]: combo[(i + 1) % 3] for i in range(5)})
            out = make(remote={"machines": [row, AGIW_ROW], "models": [row]})[0].snapshot(row)
            self.assertEqual(section(out, "duo")["id"], "duo")
            json.dumps(out)

    def test_one_malformed_row_does_not_cost_the_good_rows(self):
        for bad in ({"platform": []}, {"state": {}}, {"platform": {"a": 1}, "state": [1]}, {"host": [], "trust": {}}):
            remote = section(make(remote={"machines": [AGIW_ROW, bad, PC_ROW], "models": []})[0].snapshot(), "remote")
            self.assertEqual(remote["status"], "LIVE", bad)
            facts = dict(map(tuple, remote["facts"]))
            self.assertEqual(facts["MAC"], "UP · observed", bad)
            self.assertEqual(facts["PC"], "UNKNOWN · went silent", bad)
            self.assertEqual(remote["pre"].count("\n"), 2, "three rows, the bad one shown as unknown")

    def test_a_reader_that_breaks_still_leaves_the_phones_own_section(self):
        with unittest.mock.patch.object(monitor, "remote_section", side_effect=RuntimeError("boom")):
            out = make(remote=snapshot(AGIW_ROW))[0].snapshot()
        self.assertEqual(section(out, "duo")["status"], "NO MODEL")
        self.assertEqual(section(out, "remote")["status"], "UNKNOWN")
        self.assertNotIn("boom", json.dumps(out))

    def test_even_a_failing_view_is_a_plain_unknown(self):
        view = make()[0]
        with unittest.mock.patch.object(monitor.Monitor, "_view", side_effect=RuntimeError("boom")):
            out = view.snapshot()
        self.assertEqual(out["sections"][0]["status"], "UNKNOWN")
        json.dumps(out)


class TextTests(unittest.TestCase):
    def test_one_trailing_newline_is_not_printable_ascii(self):
        for value in ("abc\n", "\n", "abc\r\n", "a\nb", "tab\t", "\x00x", "caf\u00e9"):
            self.assertIsNone(monitor.clean_text(value), repr(value))
        self.assertEqual(monitor.clean_text("studio"), "studio")
        self.assertEqual(monitor.reason_words("not_connected\n"), "not observed")
        self.assertEqual(monitor.reason_words("x\n"), "not observed")
        row = monitor.machine_rows({"machines": [{"host": "evil\n", "state": "up", "trust": "local\n", "reasonCode": "not_connected\n"}]})[0]
        self.assertIsNone(row["trust"])
        self.assertNotIn("\n", row["name"])
        document = {"machines": [{"host": "evil\n", "platform": "mac", "state": "unknown", "trust": "local\n", "reasonCode": "not_connected\n"},
                                 {"host": "ok\n", "state": "unknown", "trust": "x\n", "reasonCode": "stale\n"}],
                    "models": [{"name": "m\n", "loaded": True}, {"name": "fine", "loaded": True}]}
        out = make(remote=document, probe=("model", ["ok"]))[0].snapshot()
        for part in out["sections"]:
            for line in part["lines"]:
                self.assertNotIn("\n", line)
            for label, text in part["facts"]:
                self.assertNotIn("\n", label + text)
        pre = section(out, "remote")["pre"]
        for line in pre.split("\n"):
            self.assertRegex(line, r"^[\x20-\x7e\u00b7]*$", "a remote string split a row of the peer map")
        self.assertEqual(len(pre.split("\n")), 3)


class MarkerTests(unittest.TestCase):
    def test_a_cut_list_says_it_was_cut(self):
        many = [{"name": f"model-{i}", "loaded": True} for i in range(20)]
        out = section(make(remote=snapshot(AGIW_ROW, models=many))[0].snapshot(), "remote")
        self.assertIn("; " + monitor.MORE + ".", out["lines"][-1])
        few = section(make(remote=snapshot(AGIW_ROW, models=many[:3]))[0].snapshot(), "remote")
        self.assertNotIn(monitor.MORE, " ".join(few["lines"]))
        machines = [{**AGIW_ROW, "host": f"h{i}"} for i in range(30)]
        cut = section(make(remote=snapshot(*machines))[0].snapshot(), "remote")
        self.assertIn("More machines are reported than are shown", " ".join(cut["lines"]))
        self.assertNotIn("More machines", " ".join(section(make(remote=snapshot(AGIW_ROW, PC_ROW))[0].snapshot(), "remote")["lines"]))

    def test_the_longest_line_the_module_can_emit_fits_the_pages_cap(self):
        many = [{"name": "m" * 48, "loaded": True} for _ in range(monitor.MAX_MODELS + 4)]
        out = section(make(remote=snapshot(AGIW_ROW, models=many))[0].snapshot(), "remote")
        self.assertLess(max(len(line) for line in out["lines"]), 600)


class HubTests(unittest.TestCase):
    """The first row is the machine that served the document; only its own freshness makes the section LIVE."""

    def remote(self, *rows):
        return section(make(remote=snapshot(*rows))[0].snapshot(), "remote")

    def test_a_degraded_hub_is_live_but_says_so(self):
        out = self.remote({**AGIW_ROW, "reasonCode": "reported_degraded"})
        self.assertEqual(out["status"], "LIVE")
        self.assertIn("reports itself degraded", " ".join(out["lines"]))
        self.assertIn("DEGRADED", out["pre"])
        self.assertNotIn("degraded", " ".join(self.remote(AGIW_ROW)["lines"]))

    def test_a_down_or_unknown_hub_is_never_live_however_fresh(self):
        for state, code in (("down", "reported_down"), ("unknown", "not_observed"), ("weird", "observed"), (None, None), ([], {})):
            out = self.remote({**AGIW_ROW, "state": state, "reasonCode": code})
            self.assertEqual(out["status"], "STALE", state)
            self.assertIn("old or unverified", " ".join(out["lines"]))

    def test_an_unreadable_first_row_does_not_promote_a_peer_to_the_serving_machine(self):
        for first in (None, "x", 5, [], {}, {"state": "weird"}):
            out = self.remote(first, {**AGIW_ROW, "host": "peer"})
            self.assertEqual(out["status"], "STALE", repr(first))
            lines = out["pre"].split("\n")
            self.assertTrue(lines[0].startswith("MACHINE"), lines[0])
            self.assertTrue(lines[1].startswith(" |"), "the readable row stays a peer")

    def test_a_count_needs_modelscomplete_to_be_exactly_true(self):
        for complete in ("no", "true", 1, 0, None, [], {}, "yes"):
            row = {**AGIW_ROW, "modelsComplete": complete}
            facts = dict(map(tuple, self.remote(row)["facts"]))
            self.assertEqual(facts["MAC models"], "Model list incomplete", repr(complete))
        facts = dict(map(tuple, self.remote({**AGIW_ROW, "modelsComplete": True})["facts"]))
        self.assertEqual(facts["MAC models"], "2 loaded (1 busy)")


class ReasonTests(unittest.TestCase):
    """An up row with a reason that contradicts up is shown in its own words and never makes the section LIVE."""

    def remote(self, *rows):
        return section(make(remote=snapshot(*rows))[0].snapshot(), "remote")

    def test_only_a_silent_observed_or_degraded_reason_counts_as_observed(self):
        for ok in (None, "observed"):
            row = {k: v for k, v in AGIW_ROW.items() if k != "reasonCode"} if ok is None else {**AGIW_ROW, "reasonCode": ok}
            out = self.remote(row)
            self.assertEqual(out["status"], "LIVE", ok)
            self.assertEqual(dict(map(tuple, out["facts"]))["MAC"], "UP · observed")
        self.assertEqual(self.remote({**AGIW_ROW, "reasonCode": "reported_degraded"})["status"], "LIVE")
        for code in ("host_key_mismatch", "untrusted", "clock_skew", "not_connected", "stale", "share_unmounted", "asleep",
                     "schema_invalid", "some_new_code", "Bad word!", "", 5, [], {}, True):
            out = self.remote({**AGIW_ROW, "reasonCode": code})
            self.assertEqual(out["status"], "STALE", repr(code))
            self.assertNotEqual(dict(map(tuple, out["facts"]))["MAC"], "UP · observed", repr(code))
            self.assertIn("does not fit an up machine", " ".join(out["lines"]), repr(code))
            self.assertNotIn("[x]", out["pre"])

    def test_the_contradicting_words_are_the_rows_own(self):
        out = self.remote({**AGIW_ROW, "reasonCode": "host_key_mismatch"})
        self.assertEqual(dict(map(tuple, out["facts"]))["MAC"], "UP · host key changed")
        self.assertIn("host key changed", out["pre"].split("\n")[0])
        self.assertNotIn("loaded", out["pre"].split("\n")[0], "no count is claimed for a row that is not confirmed")

    def test_a_down_row_shows_its_true_reason_in_the_map_and_the_facts(self):
        for code, words in (("reported_down", "reported down"), ("share_unmounted", "share not mounted"), ("clock_skew", "clock out of step"),
                            (None, "reported down"), ("some_new_code", "some new code")):
            row = {**PC_ROW, "state": "down", "reasonCode": code}
            if code is None:
                row = {k: v for k, v in row.items() if k != "reasonCode"}
            out = self.remote(AGIW_ROW, row)
            self.assertEqual(dict(map(tuple, out["facts"]))["PC"], f"DOWN · {words}", repr(code))
            self.assertIn(words, out["pre"].split("\n")[1], repr(code))


class PublicationTests(unittest.TestCase):
    def test_a_second_viewer_sees_the_phones_fresh_answer_while_the_remote_drips(self):
        clock = Clock()
        gate, started = threading.Event(), threading.Event()
        probes = [("model", ["m1"]), ("none", [])]
        count = [0]

        def probe(_env):
            return probes[min(count[0], 1)]

        def fetch(_remote):
            count[0] += 1
            if count[0] == 2:
                started.set()
                gate.wait(5)
            return snapshot(AGIW_ROW)
        view = monitor.Monitor({"OPOS_AGIW_URL": "http://" + ip4(10, 0, 0, 9) + ":1"}, fetch=fetch, probe=probe, clock=clock)
        first = view.snapshot()
        self.assertEqual(section(first, "duo")["status"], "MODEL LISTED")
        count[0] = 1
        clock.now += 3
        worker = threading.Thread(target=view.snapshot, daemon=True)
        worker.start()
        self.assertTrue(started.wait(5))
        second = view.snapshot()
        self.assertEqual(section(second, "duo")["status"], "NO MODEL", "the new probe is published before the remote fetch ends")
        self.assertEqual(section(second, "remote")["status"], "LIVE")
        self.assertIn("fetched 3 s ago", section(second, "remote")["lines"][0])
        gate.set()
        worker.join(5)

class FreshnessIncludesFetchAgeTests(unittest.TestCase):
    """R3-13, corrected in r5: a row's own age plus the time since the fetch must fit its freshFor, allowing for the monitor's own cache."""

    def test_a_row_that_is_old_by_the_time_it_is_shown_is_not_live(self):
        row = dict(AGIW_ROW, ageSeconds=1.0, freshForSeconds=3)
        for fetched in range(0, 5):
            self.assertEqual(monitor.remote_section(snapshot(row), "x", fetched)["status"], "LIVE", fetched)
        self.assertEqual(monitor.remote_section(snapshot(row), "x", 5)["status"], "STALE")
        young = dict(AGIW_ROW, ageSeconds=0.5, freshForSeconds=3)
        self.assertEqual(monitor.remote_section(snapshot(young), "x", 5)["status"], "STALE", "a genuinely old sample is still caught")

    def poll_words(self, row, *, step, polls=24, fetch_takes=0.0):
        """Poll a real Monitor through its real cache; returns the remote section's words and how many fetches happened."""
        clock = Clock()
        fetches = [0]

        def fetch(_remote):
            fetches[0] += 1
            clock.now += fetch_takes
            return snapshot(row)
        view = monitor.Monitor({"OPOS_AGIW_URL": "http://" + ip4(10, 0, 0, 9) + ":1"}, fetch=fetch,
                               probe=lambda _e: ("none", []), clock=clock)
        words = []
        for _ in range(polls):
            words.append(section(view.snapshot(), "remote")["status"])
            clock.now += step
        return words, fetches[0]

    def test_a_healthy_monitor_polled_every_two_seconds_stays_live_through_its_own_cache(self):
        """r4 regression. A step just under the cache window makes real cache hits; exactly 2.0 would collect every time."""
        row = dict(AGIW_ROW, ageSeconds=1.2, freshForSeconds=3)
        for step in (1.99, 1.0, 0.5):
            words, fetches = self.poll_words(row, step=step)
            self.assertEqual(set(words), {"LIVE"}, (step, words))
            self.assertLess(fetches, len(words), f"step {step}: the cache must actually be hit, or this proves nothing")
        words, fetches = self.poll_words(row, step=2.0)
        self.assertEqual(set(words), {"LIVE"}, words)

    def test_a_slow_remote_does_not_make_the_word_alternate(self):
        """r5 low: the fetch time is in the age. It may keep a slow remote STALE; it must never flip LIVE and STALE."""
        quick = dict(AGIW_ROW, ageSeconds=0.5, freshForSeconds=3)
        for step in (2.0, 1.99):
            words, _ = self.poll_words(quick, step=step, fetch_takes=2.5)
            self.assertEqual(set(words), {"LIVE"}, (step, words))
        slow = dict(AGIW_ROW, ageSeconds=1.2, freshForSeconds=3)
        for step in (2.0, 1.99):
            words, _ = self.poll_words(slow, step=step, fetch_takes=3.3)
            self.assertEqual(set(words), {"STALE"}, (step, words))

    def test_a_row_older_than_its_own_freshness_window_is_never_live(self):
        for age, fresh_for, expect in ((3.5, 3, "STALE"), (3.0, 3, "LIVE"), (100, 3, "STALE")):
            row = dict(AGIW_ROW, ageSeconds=age, freshForSeconds=fresh_for)
            for fetched in (0, 1, 2):
                got = monitor.remote_section(snapshot(row), "x", fetched)["status"]
                self.assertEqual(got, expect if fetched <= 2 else "STALE", (age, fetched))
        self.assertEqual(monitor.remote_section(snapshot(dict(AGIW_ROW, ageSeconds=100, freshForSeconds=3)), "x", 50)["status"], "STALE")

    def test_the_time_the_fetch_took_counts_toward_the_age(self):
        """The remote's age is stamped before the fetch starts, so a slow fetch cannot read 'fetched 0 s ago'."""
        clock = Clock()

        def slow(_remote):
            clock.now += 6
            return snapshot(AGIW_ROW)
        view = monitor.Monitor({"OPOS_AGIW_URL": "http://" + ip4(10, 0, 0, 9) + ":1"}, fetch=slow,
                               probe=lambda _e: ("none", []), clock=clock)
        remote = section(view.snapshot(), "remote")
        self.assertIn("fetched 6 s ago", remote["lines"][0])
        self.assertEqual(remote["status"], "STALE", "1.2 s old at the fetch, 6 s since: past freshFor 3 plus the cache allowance")


class UnconfirmedRowWordsTests(unittest.TestCase):
    """R3-7, R3-8, R3-15: a row that is not confirmed up never reads as a measurement or an observation."""

    def facts(self, row):
        return dict(map(tuple, monitor.remote_section(snapshot(row), "x", 0)["facts"]))

    def test_counts_and_rates_of_a_row_that_is_not_up_are_not_measured(self):
        for row in (dict(AGIW_ROW, state="down"), dict(AGIW_ROW, state="unknown"), dict(AGIW_ROW, reasonCode="not_connected")):
            facts = self.facts(row)
            self.assertEqual((facts["MAC models"], facts["MAC prompts"], facts["MAC writing"]), ("Not measured",) * 3, row)

    def test_a_confirmed_row_still_shows_its_numbers(self):
        facts = self.facts(AGIW_ROW)
        self.assertEqual((facts["MAC models"], facts["MAC prompts"]), ("2 loaded (1 busy)", "812 tok/s"))

    def test_observed_on_a_row_that_is_not_up_is_not_called_observed(self):
        for state in ("down", "unknown"):
            for code in ("observed", "reported_degraded"):
                rows = monitor.machine_rows(snapshot(dict(AGIW_ROW, state=state, reasonCode=code)))
                self.assertEqual(rows[0]["reason"], "not observed", (state, code))

    def test_a_reason_with_edge_underscores_is_not_read_as_observed(self):
        for code in ("observed_", "_observed", "observed__"):
            rows = monitor.machine_rows(snapshot(dict(AGIW_ROW, reasonCode=code)))
            self.assertNotEqual(rows[0]["reason"], "observed", code)
            self.assertFalse(rows[0]["confirmed"], code)
        self.assertEqual(monitor.machine_rows(snapshot(dict(AGIW_ROW, reasonCode="observed_")))[0]["reason"], "unrecognised reason")


if __name__ == "__main__":
    unittest.main()
