# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin published simulation figures; OPOS_SLOW_TESTS=1 also runs full reports."""
import contextlib
import io
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from opos import eol, evaleol, profiles
from opos.evaluate import router_policy, rules_policy, static_low_power

ROOT = Path(__file__).resolve().parents[1]
PHONES = 40
HEADER = "policy  mode    calls_h sms_h alarms_h app_h alive_h brownouts% died_at%"
EOL_ORDER = tuple((policy, mode) for policy in ("basic", "static", "rules", "router")
                  for mode in ("before", "after"))
EOL_COLUMNS = ("calls", "sms", "alarms", "app", "alive", "brownout_pct", "shutdown_level")
FACTORIES = {"basic": lambda: evaleol.basic_policy, "static": lambda: static_low_power,
             "rules": lambda: rules_policy, "router": lambda: router_policy()}
CHEAP_BEFORE = ("basic", "static", "rules")
# These two profile runs take most of the time; the opt-in slow class checks them.
SLOW_PROFILE_PAIRS = frozenset({("worn", "router"), ("eol", "router")})
TWO_DECIMALS = r"(?<![\w.])\d+\.\d{2}(?!\w|\.\d)"
NUMBER = r"(?<![\w.])\d+(?:\.\d+)?(?!\w|\.\d)"
# The README profile table: one row per profile, in this order, with these columns.
README_PROFILES = (("Fresh battery", "fresh"), ("Worn battery", "worn"), ("Cold", "cold"),
                   ("Hot", "hot"), ("Low start", "lowstart"), ("End of life", "eol"))
README_COLUMNS = (("router", "important_min"), ("rules", "important_min"),
                  ("router", "useful_min"), ("rules", "useful_min"))
README_HEADER = ("| Profile | Important minutes, router | Important minutes, rules "
                 "| Useful minutes, router | Useful minutes, rules |")
README_ROW = r"\| ([^|\n]+?) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \|"
README_CELL = r"(?<=\| )\d+(?= \|)"
README_TRADE_OFF = ("The router buys important minutes by giving up useful minutes in every profile: "
                    "it allows email and maps for fewer minutes than the fixed rules everywhere, "
                    "far fewer with a worn battery, and none with a low start or an end-of-life battery.")
# Each expression captures one particular occurrence, including repeated values.
META_FIELDS = (
    ("fresh", "rules", "important_min", r"fresh battery: rules (\d+)(?!\w|\.\d) important"),
    ("fresh", "router", "important_min", r"fresh battery: rules \d+ important, router (\d+)(?!\w|\.\d)"),
    ("fresh", "static", "important_min", r"fresh battery: rules \d+ important, router \d+, static low-power (\d+)(?!\w|\.\d)"),
    ("worn", "rules", "important_min", r"Worn battery: rules (\d+)(?!\w|\.\d)"),
    ("worn", "router", "important_min", r"Worn battery: rules \d+, router (\d+)(?!\w|\.\d)"),
    ("worn", "static", "important_min", r"Worn battery: rules \d+, router \d+, static (\d+)(?!\w|\.\d)"),
    ("cold", "rules", "important_min", r"Cold: rules (\d+)(?!\w|\.\d)"),
    ("cold", "router", "important_min", r"Cold: rules \d+, router (\d+)(?!\w|\.\d)"),
    ("hot", "rules", "important_min", r"Hot: rules (\d+)(?!\w|\.\d)"),
    ("hot", "router", "important_min", r"Hot: rules \d+, router (\d+)(?!\w|\.\d)"),
    ("lowstart", "rules", "important_min", r"Low start: rules (\d+)(?!\w|\.\d)"),
    ("lowstart", "router", "important_min", r"Low start: rules \d+, router (\d+)(?!\w|\.\d)"),
    ("eol", "rules", "important_min", r"End-of-life battery: rules (\d+)(?!\w|\.\d)"),
    ("eol", "router", "important_min", r"End-of-life battery: rules \d+, router (\d+)(?!\w|\.\d)"),
    ("eol", "static", "important_min", r"End-of-life battery: rules \d+, router \d+, static (\d+)(?!\w|\.\d)"),
    ("eol", "static", "brownout", r"Static browns out in (\d+\.\d) percent"),
    ("fresh", "router", "useful_min", r"router vs rules: fresh (\d+)(?!\w|\.\d) vs"),
    ("fresh", "rules", "useful_min", r"router vs rules: fresh \d+ vs (\d+)(?!\w|\.\d)"),
    ("worn", "router", "useful_min", r", worn (\d+)(?!\w|\.\d) vs"),
    ("worn", "rules", "useful_min", r", worn \d+ vs (\d+)(?!\w|\.\d)"),
    ("cold", "router", "useful_min", r", cold (\d+)(?!\w|\.\d) vs"),
    ("cold", "rules", "useful_min", r", cold \d+ vs (\d+)(?!\w|\.\d)"),
    ("hot", "router", "useful_min", r", hot (\d+)(?!\w|\.\d) vs"),
    ("hot", "rules", "useful_min", r", hot \d+ vs (\d+)(?!\w|\.\d)"),
    ("lowstart", "router", "useful_min", r", low start (\d+)(?!\w|\.\d) vs"),
    ("lowstart", "rules", "useful_min", r", low start \d+ vs (\d+)(?!\w|\.\d)"),
    ("eol", "router", "useful_min", r", end of life (\d+)(?!\w|\.\d) vs"),
    ("eol", "rules", "useful_min", r", end of life \d+ vs (\d+)(?!\w|\.\d)"),
)
# One sentence claims zero counts for router and rules in every simulator profile.
META_ZERO_CLAIM = (r"The recorded runs report (\d+)(?!\w|\.\d) brownouts and (\d+)(?!\w|\.\d) "
                   r"missing_kept counts for router and rules in every profile")
META_ZERO_METRICS = ((1, "brownout"), (2, "missing_kept"))


def one_match(pattern, text, label):
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    if len(matches) != 1:
        raise ValueError(f"{label}: expected one match, found {len(matches)}")
    return matches[0]


def readme_section(text):
    return one_match(r"^## Simulation results[^\S\n]*\n([\s\S]*?)(?=^## |\Z)",
                     text, "README Simulation results").group(1)


def meta_line(text, prefix):
    return one_match(r"^" + re.escape(prefix) + r"[^\n]*$", text, prefix).group()


def table_lines(text):
    block = one_match(r"^~~~\n([\s\S]*?)^~~~[^\S\n]*$", text, "worn table").group(1)
    lines = block.splitlines()
    if len(lines) != 9 or lines[0] != HEADER:
        raise ValueError("worn table: expected report header and eight rows")
    for line, key in zip(lines[1:], EOL_ORDER):
        fields = line.split()
        if (len(fields) != 9 or tuple(fields[:2]) != key
                or not all(re.fullmatch(r"\d+(?:\.\d+)?", value) for value in fields[2:])):
            raise ValueError(f"worn table: malformed or out-of-order {key} row: {line!r}")
    return lines


def table_results(text):
    return {key: dict(zip(EOL_COLUMNS, map(float, line.split()[2:])))
            for key, line in zip(EOL_ORDER, table_lines(text)[1:])}


def format_eol_row(name, mode, r):
    # Keep the display contract of evaleol.report(), including rounding and padding.
    return (f"{name:<7} {mode:<7} {r['calls']:7.2f} {r['sms']:5.2f} "
            f"{r['alarms']:8.2f} {r['app']:5.2f} {r['alive']:7.2f} "
            f"{r['brownout_pct']:10.0f} {r['shutdown_level']:8.1f}")


def table_mismatches(text, report_lines, exact=False):
    """Compare complete fenced lines, optionally including all report ordering."""
    try:
        actual = table_lines(text)
    except ValueError as error:
        return [str(error)]
    if exact:
        return [] if actual == report_lines else [f"worn table: {actual!r} != {report_lines!r}"]
    return [f"worn table missing whole line: {line!r}" for line in report_lines if line not in actual]


def hours_mismatches(text, results, document):
    """Check displayed two-decimal call hours in their specified document order."""
    try:
        if document == "README":
            scope = readme_section(text)
            order = (("router", "before"), ("router", "after"),
                     ("rules", "before"), ("rules", "after"))
        elif document == "META":
            scope = meta_line(text, "Worn-battery hours")
            order = EOL_ORDER
        elif document == "worn summary":
            scope = meta_line(text, "Average hours of working calls")
            order = EOL_ORDER + (("router", "after"), ("static", "after"))
        else:
            raise ValueError(f"unknown hours document: {document}")
    except ValueError as error:
        return [str(error)]
    actual = re.findall(TWO_DECIMALS, scope)
    expected = [f"{results[key]['calls']:.2f}" for key in order]
    return [] if actual == expected else [f"{document} call hours: {actual!r} != {expected!r}"]


def meta_profile_values(text):
    line = meta_line(text, "Measured in simulation")
    values = {(profile, policy, metric): one_match(pattern, line, f"META {profile}/{policy}/{metric}").group(1)
              for profile, policy, metric, pattern in META_FIELDS}
    claim = one_match(META_ZERO_CLAIM, line, "META zero brownouts and missing_kept")
    for group, metric in META_ZERO_METRICS:
        for profile in profiles.PROFILES:
            for policy in ("rules", "router"):
                values[profile, policy, "zero_" + metric] = claim.group(group)
    return values


def meta_profile_results(text):
    """Document values for cross-document checks, never live simulator evidence."""
    results = {}
    for (profile, policy, metric), value in meta_profile_values(text).items():
        scale = 100 if metric == "brownout" else 1
        results.setdefault((profile, policy), {})[metric.removeprefix("zero_")] = float(value) / scale
    return results


def published_profile_minutes(value):
    # profiles.main prints one decimal; published whole minutes round that output.
    # For example, the EOL router's 337.475 prints as 337.5, then rounds to 338.
    return f"{float(f'{value:.1f}'):.0f}"


def readme_table_values(text):
    """Read every figure of the README profile table, in its fixed row and column order.

    Whole-minute figures (100 or more) outside the table are rejected, so a stale
    figure left in the prose cannot pass unchecked.
    """
    lines = readme_section(text).splitlines()
    if lines.count(README_HEADER) != 1:
        raise ValueError("README minutes: expected one profile table header")
    rows = [line for line in lines if line.startswith("|") and line != README_HEADER
            and not re.fullmatch(r"\|(?:-+\|)+", line)]
    if len(rows) != len(README_PROFILES):
        raise ValueError(f"README minutes: expected {len(README_PROFILES)} profile rows, found {rows!r}")
    values = {}
    for line, (label, profile) in zip(rows, README_PROFILES):
        match = re.fullmatch(README_ROW, line)
        if not match or match.group(1) != label:
            raise ValueError(f"README minutes: malformed or out-of-order {profile} row: {line!r}")
        for (policy, metric), value in zip(README_COLUMNS, match.groups()[1:]):
            values[profile, policy, metric] = value
    prose = "\n".join(line for line in lines if not line.startswith("|"))
    stray = [value for value in re.findall(r"(?<![\w.])\d+(?!\w|\.\d)", prose) if int(value) >= 100]
    if stray:
        raise ValueError(f"README minutes: whole-minute figures outside the table: {stray!r}")
    return values


def profile_values(text, document):
    if document == "README":
        return readme_table_values(text)
    if document == "META":
        return meta_profile_values(text)
    raise ValueError(f"unknown profile document: {document}")


def profile_mismatches(text, results, document, exclude=frozenset()):
    """Check every published figure except (profile, policy) pairs in exclude.

    A published figure without a simulator result is a mismatch, never a pass.
    """
    try:
        values = profile_values(text, document)
    except ValueError as error:
        return [str(error)]
    mismatches = []
    for (profile, policy, metric), actual in values.items():
        if (profile, policy) in exclude:
            continue
        measured = metric.removeprefix("zero_")
        value = results.get((profile, policy), {}).get(measured)
        if value is None:
            mismatches.append(f"{document} {profile}/{policy}/{metric}: no simulator result")
            continue
        if metric != measured:
            expected = f"{value:g}"  # a published zero count needs an average of exactly 0
        elif metric == "brownout":
            expected = f"{100 * value:.1f}"
        else:
            expected = published_profile_minutes(value)
        if actual != expected:
            mismatches.append(f"{document} {profile}/{policy}/{metric}: {actual} != {expected}")
    return mismatches


def brownout_sentence_mismatches(text, results, levels, phones=PHONES):
    """Tie the "How to read it" brownout sentence to the table and per-phone cut-out charges."""
    try:
        line = meta_line(text, "How to read it")
        actual = one_match(r"Before, [^\n]*?; after, \d+(?:\.\d+)?%\.", line, "brownout sentence").group()
    except ValueError as error:
        return [str(error)]
    before = [results[name, "before"] for name in ("basic", "static", "rules", "router")]
    after = {results[name, "after"]["brownout_pct"] for name in ("basic", "static", "rules", "router")}
    if any(row["brownout_pct"] != 100 for row in before):
        return ["brownout sentence: not every before row has brownouts% 100"]
    if len(after) != 1 or not levels:
        return ["brownout sentence: after rows disagree or no per-phone cut-out charges"]
    died = [row["shutdown_level"] for row in before]
    expected = (f"Before, all {phones} simulated worn phones (100%) cut out suddenly, at an average of about "
                f"{min(died):.0f}-{max(died):.0f}% charge depending on the policy (individual phones "
                f"anywhere from {min(levels):.0f}% to {max(levels):.0f}%); after, {after.pop():.0f}%.")
    return [] if actual == expected else [f"brownout sentence: {actual!r} != {expected!r}"]


def digit_mutations(text, matches, group=0):
    """Change one digit at each occurrence, even when displayed values repeat."""
    for match in matches:
        start, end = match.span(group)
        digit = next(i for i in range(start, end) if text[i].isdigit())
        replacement = str((int(text[digit]) + 1) % 10)
        yield start, text[:digit] + replacement + text[digit + 1:]


def profile_results(pairs):
    return {(profile, policy): profiles.benchmark(FACTORIES[policy], profile, n=PHONES, seed=100)
            for profile, policy in pairs}


def fast_profile_pairs():
    return [(profile, policy) for profile in profiles.PROFILES
            for policy in ("rules", "static", "router")
            if (profile, policy) not in SLOW_PROFILE_PAIRS]


@contextlib.contextmanager
def recorded_eol_runs():
    """Record (aware, result) for every simulated phone that evaleol runs."""
    runs = []
    real_run_eol = evaleol.run_eol

    def record(policy, scenario, aware=False, *args, **kwargs):
        result = real_run_eol(policy, scenario, aware, *args, **kwargs)
        runs.append((aware, result))
        return result

    with patch.object(evaleol, "run_eol", record):
        yield runs


def cut_out_levels(runs):
    return [result["shutdown_level"] for aware, result in runs if not aware and result["brownout"]]


class TestPublishedNumbers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = (ROOT / "docs/worn-battery-results.md").read_text(encoding="utf-8")
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        cls.meta = (ROOT / "docs/META.md").read_text(encoding="utf-8")
        with recorded_eol_runs() as runs:
            cls.before_rows = [format_eol_row(name, "before", evaleol.benchmark_eol(FACTORIES[name], False, PHONES, 0))
                               for name in CHEAP_BEFORE]
        # Router before phones are checked by the opt-in full report.
        cls.before_levels = cut_out_levels(runs)
        # Serial on purpose: a process pool needs POSIX semaphores, which sandboxes may block.
        cls.profile_results = profile_results(fast_profile_pairs())

    def test_fast_before_rows_match_simulator(self):
        self.assertEqual(table_mismatches(self.doc, self.before_rows), [])

    def test_fast_profiles_match_published_minutes(self):
        for name, text in (("README", self.readme), ("META", self.meta)):
            with self.subTest(document=name):
                self.assertEqual(profile_mismatches(text, self.profile_results, name,
                                                    exclude=SLOW_PROFILE_PAIRS), [])

    def test_published_figure_without_result_is_a_mismatch(self):
        complete = meta_profile_results(self.meta)
        for name, text in (("README", self.readme), ("META", self.meta)):
            values = profile_values(text, name)
            self.assertEqual(profile_mismatches(text, complete, name), [], name)
            self.assertEqual(len(profile_mismatches(text, {}, name)), len(values), name)
            for pair in {key[:2] for key in values}:
                partial = {key: value for key, value in complete.items() if key != pair}
                with self.subTest(document=name, pair=pair):
                    mismatches = profile_mismatches(text, partial, name)
                    self.assertTrue(mismatches)
                    for mismatch in mismatches:
                        self.assertIn("/".join(pair) + "/", mismatch)
                        self.assertTrue(mismatch.endswith(": no simulator result"), mismatch)

    def test_brownout_sentence_matches_table(self):
        self.assertEqual(brownout_sentence_mismatches(self.doc, table_results(self.doc), self.before_levels), [])

    def test_cross_document_call_hours(self):
        results = table_results(self.doc)
        for name, text in (("README", self.readme), ("META", self.meta), ("worn summary", self.doc)):
            with self.subTest(document=name):
                self.assertEqual(hours_mismatches(text, results, name), [])

    def test_cross_document_profile_minutes(self):
        self.assertEqual(profile_mismatches(self.readme, meta_profile_results(self.meta), "README"), [])

    def test_no_stale_brownout_range(self):
        self.assertNotIn("98-100%", self.doc)

    def test_readme_table_covers_every_profile(self):
        values = readme_table_values(self.readme)
        self.assertEqual({profile for _, profile in README_PROFILES}, set(profiles.PROFILES))
        self.assertEqual(len(values), len(README_PROFILES) * len(README_COLUMNS))

    def test_readme_trade_off_sentence_matches_table(self):
        # The sentence is checked against the table, and the table against the simulator.
        section = readme_section(self.readme)
        self.assertEqual(section.count(README_TRADE_OFF), 1)
        values = {key: int(value) for key, value in readme_table_values(self.readme).items()}
        for _, profile in README_PROFILES:
            with self.subTest(profile=profile):
                self.assertGreater(values[profile, "router", "important_min"], values[profile, "rules", "important_min"])
                self.assertLess(values[profile, "router", "useful_min"], values[profile, "rules", "useful_min"])
        self.assertEqual((values["lowstart", "router", "useful_min"], values["eol", "router", "useful_min"]), (0, 0))
        self.assertLess(4 * values["worn", "router", "useful_min"], values["worn", "rules", "useful_min"])

    def test_readme_worn_hours_are_averages_not_a_range(self):
        section = readme_section(self.readme)
        self.assertIn("the figures are the averages of those two sets of runs, not a range", section)
        self.assertNotRegex(section, r"\d\.\d{2}\s*(?:-|–|to)\s*\d")

    def test_profile_minutes_follow_cli_precision(self):
        # Exercise the real CLI renderer at the half-minute rounding boundary.
        result = {"important_min": 337.475, "useful_min": 0,
                  "missing_kept": 0, "brownout": 0}
        output = io.StringIO()
        with patch.object(profiles, "benchmark", return_value=result), contextlib.redirect_stdout(output):
            self.assertEqual(profiles.main(["--n", "1"]), 0)
        displayed = re.search(r"important_min=(\S+)", output.getvalue()).group(1)
        self.assertEqual(published_profile_minutes(result["important_min"]), f"{float(displayed):.0f}")

    def test_each_pinned_number_mutation_is_detected(self):
        # Snapshot document values only to test helper sensitivity. Simulator truth
        # is established by the fast benchmarks above and the opt-in full checks.
        rows = table_lines(self.doc)
        hours = table_results(self.doc)
        minutes = meta_profile_results(self.meta)
        levels = self.before_levels
        cases = [
            ("table", "~~~\n" + "\n".join(rows) + "\n~~~", NUMBER,
             lambda text: table_mismatches(text, rows, exact=True)),
            ("README hours", "## Simulation results\n" + readme_section(self.readme), TWO_DECIMALS,
             lambda text: hours_mismatches(text, hours, "README")),
            ("META hours", meta_line(self.meta, "Worn-battery hours"), TWO_DECIMALS,
             lambda text: hours_mismatches(text, hours, "META")),
            ("worn summary", meta_line(self.doc, "Average hours of working calls"), TWO_DECIMALS,
             lambda text: hours_mismatches(text, hours, "worn summary")),
            ("README minutes", "## Simulation results\n" + readme_section(self.readme), README_CELL,
             lambda text: profile_mismatches(text, minutes, "README")),
            ("brownout sentence", meta_line(self.doc, "How to read it"), NUMBER,
             lambda text: brownout_sentence_mismatches(text, hours, levels)),
        ]
        for label, text, pattern, check in cases:
            self.assertEqual(check(text), [], label)
            mutations = list(digit_mutations(text, re.finditer(pattern, text)))
            self.assertTrue(mutations, label)
            for offset, mutated in mutations:
                with self.subTest(document=label, offset=offset):
                    self.assertTrue(check(mutated), mutated)
        line = meta_line(self.meta, "Measured in simulation")
        self.assertEqual(profile_mismatches(line, minutes, "META"), [])
        for profile, policy, metric, pattern in META_FIELDS:
            matches = list(re.finditer(pattern, line))
            self.assertEqual(len(matches), 1)
            for offset, mutated in digit_mutations(line, matches, group=1):
                with self.subTest(document="META minutes", profile=profile, policy=policy, metric=metric):
                    self.assertTrue(profile_mismatches(mutated, minutes, "META"), mutated)
        claim = list(re.finditer(META_ZERO_CLAIM, line))
        self.assertEqual(len(claim), 1)
        for group, metric in META_ZERO_METRICS:
            for offset, mutated in digit_mutations(line, claim, group=group):
                with self.subTest(document="META zero claim", metric=metric):
                    self.assertTrue(profile_mismatches(mutated, minutes, "META"), mutated)

    def test_meta_integer_figures_reject_decimal_suffixes(self):
        line = meta_line(self.meta, "Measured in simulation")
        results = meta_profile_results(self.meta)
        for profile, policy, metric, pattern in META_FIELDS:
            if metric == "brownout":
                continue
            end = one_match(pattern, line, "META number").end(1)
            mutated = line[:end] + ".5" + line[end:]
            with self.subTest(profile=profile, policy=policy, metric=metric):
                self.assertTrue(profile_mismatches(mutated, results, "META"), mutated)
        for group, metric in META_ZERO_METRICS:
            end = one_match(META_ZERO_CLAIM, line, "META zero claim").end(group)
            mutated = line[:end] + ".5" + line[end:]
            with self.subTest(claim=metric):
                self.assertTrue(profile_mismatches(mutated, results, "META"), mutated)

    def test_table_requires_whole_lines_inside_fence(self):
        row = self.before_rows[1]
        for changed in (row + " extra", "prefix " + row, "", row + "\n" + row):
            with self.subTest(changed=changed):
                # An intact row outside the fence must never hide a changed row.
                mutated = self.doc.replace(row, changed) + "\n" + row + "\n"
                self.assertTrue(table_mismatches(mutated, [row]))

    def test_well_formed_stale_row_fails_whole_line_check(self):
        # These rows pass table_lines' validation, so only the whole-line check
        # inside the fence can reject them; the intact row sits after the fence.
        row = self.before_rows[1]
        cases = [(row, row.replace("100     20.0", " 98     20.5")), (row, row + "5")]
        for row in self.before_rows:
            cases += [(row, changed) for _, changed in digit_mutations(row, re.finditer(NUMBER, row))]
        for row, changed in cases:
            with self.subTest(changed=changed):
                self.assertNotEqual(changed, row)
                mutated = self.doc.replace(row, changed) + "\n" + row + "\n"
                self.assertEqual(table_mismatches(mutated, [row]), [f"worn table missing whole line: {row!r}"])


@unittest.skipUnless(os.environ.get("OPOS_SLOW_TESTS") == "1", "set OPOS_SLOW_TESTS=1 for full simulator checks")
class TestPublishedNumbersSlow(unittest.TestCase):
    def test_full_eol_report_matches_table_line_for_line(self):
        text = (ROOT / "docs/worn-battery-results.md").read_text(encoding="utf-8")
        with recorded_eol_runs() as runs:
            report = evaleol.report(PHONES, 0)
        self.assertEqual(table_mismatches(text, report, exact=True), [])
        self.assertEqual(brownout_sentence_mismatches(text, table_results(text), cut_out_levels(runs)), [])
        after = [result for aware, result in runs if aware]
        self.assertEqual(len(after), 4 * PHONES)
        # The doc says the planned-shutdown rule never triggered and every "after"
        # phone ran its simulated battery to 0% (dead, no brownout, before 24 h).
        for result in after:
            self.assertEqual((result["graceful"], result["brownout"]), (False, False))
            self.assertLess(result["alive_h"], 24.0)

    def test_every_published_profile_figure(self):
        results = profile_results(fast_profile_pairs() + sorted(SLOW_PROFILE_PAIRS))
        for name, path in (("README", "README.md"), ("META", "docs/META.md")):
            with self.subTest(document=name):
                self.assertEqual(profile_mismatches((ROOT / path).read_text(encoding="utf-8"), results, name), [])


if __name__ == "__main__":
    unittest.main()
