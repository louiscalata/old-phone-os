# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest


def _load():
    p = pathlib.Path(__file__).resolve().parents[1] / "docs" / "provenance.md"
    t = p.read_text(encoding="utf-8")
    lines = t.splitlines()
    title_idx = None
    for i, line in enumerate(lines):
        if line == "# Provenance":
            title_idx = i
            break
    if title_idx is None:
        raise ValueError("Missing '# Provenance' heading")
    next_idx = None
    for i in range(title_idx + 1, len(lines)):
        if lines[i].startswith("## "):
            next_idx = i
            break
    if next_idx is None:
        raise ValueError("Missing '## ' heading after '# Provenance'")
    summary = "\n".join(lines[title_idx + 1:next_idx])
    return t, summary, summary.lower()


class TestProvenanceSummary(unittest.TestCase):
    def setUp(self):
        self.content, self.summary, self.lower = _load()

    def test_summary_mentions_all_recorded_meta_models(self):
        for mid in ("muse-glimmer-30b", "muse-spark-1.3", "muse-wonder-1.0"):
            self.assertIn(mid, self.lower)

    def test_summary_mentions_paid_routes_and_spend_ledger(self):
        self.assertIn("spend ledger", self.lower)
        import re
        models = ("muse-glimmer-30b", "muse-spark-1.3", "muse-wonder-1.0")
        paid_models = ["muse-spark-1.3"]
        for m in models:
            pattern = re.compile(re.escape(m) + r"\s*\([^)]*\)", re.IGNORECASE)
            for mobj in pattern.finditer(self.content):
                entry = mobj.group(0).lower()
                if re.search(r"\bpaid\b", entry):
                    if m not in paid_models:
                        paid_models.append(m)
                    break
        self.assertTrue(paid_models, "no paid models discovered")
        sentences = re.split(r'(?<=[.!?])\s+|\n+', self.summary)
        for m in paid_models:
            pattern_m = re.compile(re.escape(m), re.IGNORECASE)
            has_positive = False
            clause_split_re = re.compile(r'\s*;\s*|\s*\b(?:while|whereas|but)\b\s*', re.IGNORECASE)
            for sent in sentences:
                clauses = clause_split_re.split(sent)
                for clause in clauses:
                    clause_lower = clause.lower()
                    if pattern_m.search(clause_lower):
                        if re.search(r'\bunpaid\b', clause_lower) or re.search(r'\bnot\s+paid\b', clause_lower):
                            self.fail(f"Model {m} clause contains negative paid status: {clause!r}")
                        clause_free = re.sub(r"\b(?:not|never|no\s+longer|isn't|wasn't|aren't|weren't)\s+(?:(?:entirely|fully|completely|currently|still|also|really|very|quite)\s+)*(?!only\b|just\b)free\b", "", clause_lower)
                        clause_free = re.sub(r"\b(?:not|never|no\s+longer|isn't|wasn't|aren't|weren't)\s+(?:(?:entirely|fully|completely|currently|still|also|really|very|quite)\s+)*(?!only\b|just\b)(?:available\s+)?(?:(?:entirely|fully|completely|currently|still|also|really|very|quite)\s+)*(?:for\s+)?(?:(?:entirely|fully|completely|currently|still|also|really|very|quite)\s+)*free\b", "", clause_free)
                        clause_free = re.sub(r"\b(?:not|never|no\s+longer|isn't|wasn't|aren't|weren't)\s+(?:(?:entirely|fully|completely|currently|still|also|really|very|quite)\s+)*(?!only\b|just\b)(?:offered\s+)?(?:(?:entirely|fully|completely|currently|still|also|really|very|quite)\s+)*free\s+of\s+charge\b", "", clause_free)
                        clause_free = re.sub(r"\b(?:not|never|no\s+longer|isn't|wasn't|aren't|weren't)\s+(?:(?:entirely|fully|completely|currently|still|also|really|very|quite)\s+)*(?!only\b|just\b)(?:on\s+a\s+)?(?:(?:entirely|fully|completely|currently|still|also|really|very|quite)\s+)*free\s+(?:tier|plan|pricing|price|route|access|usage|model)\b", "", clause_free)
                        clause_free = re.sub(r"\b(?:not|never|no\s+longer|isn't|wasn't|aren't|weren't)\s+(?:(?:entirely|fully|completely|currently|still|also|really|very|quite)\s+)*(?!only\b|just\b)(?:available\s+)?(?:(?:entirely|fully|completely|currently|still|also|really|very|quite)\s+)*at\s+(?:no|zero)\s+(?:cost|charge)\b", "", clause_free)
                        clause_free = re.sub(r"\b(?:not|never|no\s+longer|isn't|wasn't|aren't|weren't)\s+(?:(?:entirely|fully|completely|currently|still|also|really|very|quite)\s+)*(?!only\b|just\b)without\s+(?:charge|cost|fees?)\b", "", clause_free)
                        clause_free = re.sub(r"\b(?:not|never|no\s+longer|isn't|wasn't|aren't|weren't)\s+(?:(?:entirely|fully|completely|currently|still|also|really|very|quite)\s+)*(?!only\b|just\b)costs?\s+nothing\b", "", clause_free)
                        if re.search(r'\bfree\s+(?:tier|plan|pricing|price|route|access|usage|model)\b', clause_free):
                            self.fail(f"Model {m} clause claims free status: {clause!r}")
                        if re.search(r'\bfor\s+free\b|\bavailable\s+(?:for\s+)?free\b|\bfree\s+of\s+charge\b|\bat\s+(?:no|zero)\s+(?:cost|charge)\b|\bwithout\s+(?:charge|cost|fees?)\b|\bcosts?\s+nothing\b', clause_free):
                            self.fail(f"Model {m} clause claims free status: {clause!r}")
                        for free_match in re.finditer(r'\b(?:is|was|are|were|be|been|being|remains?|remained|stays?|stayed)\s+(?:entirely|fully|completely|currently|still|also|really|very|quite)?\s*\bfree\b', clause_free):
                            prior_models = list(pattern_m.finditer(clause_free[:free_match.start()]))
                            if not prior_models:
                                fwd = None
                                for fwd_m in pattern_m.finditer(clause_free[free_match.end():]):
                                    fwd = fwd_m
                                    break
                                if fwd is None:
                                    continue
                                between_fwd = clause_free[free_match.end():free_match.end() + fwd.start()]
                                if not re.fullmatch(r'[\s,;:()"\'\-]*(?:(?:is|was|are|were|be|been|being)[\s,;:()"\'\-]+)?(?:(?:the|a|an)[\s,;:()"\'\-]+)?[\s,;:()"\'\-]*', between_fwd):
                                    continue
                                self.fail(f"Model {m} clause claims free status: {clause!r}")
                            between = clause_free[prior_models[-1].end():free_match.start()]
                            if re.search(r'\b(?:its|their|the|this|that|these|those)?\s*(?:output|outputs|software|documentation|docs?|results?|responses?|artifacts?|artefacts?)\s*$', between):
                                continue
                            pre = clause_free[:free_match.end()]
                            if re.search(r'\b(?:its|their|the|this|that|these|those)\s+(?:output|outputs|software|documentation|docs?|results?|responses?|artifacts?|artefacts?)\b(?:\s+(?:generated|produced)\s+by\s+[a-z0-9_.-]+|\s+(?:of|from)\s+[a-z0-9_.-]+)?\s*,?\s*\b(?:is|was|are|were|be|been|being|remains?|remained|stays?|stayed)\s+(?:entirely|fully|completely|currently|still|also|really|very|quite)?\s*free$', pre):
                                continue
                            self.fail(f"Model {m} clause claims free status: {clause!r}")
                        for paid_match in re.finditer(r'\bpaid\b', clause_lower):
                            before = clause_lower[:paid_match.start()]
                            last_model_end = None
                            for mm in pattern_m.finditer(before):
                                last_model_end = mm.end()
                            if last_model_end is None:
                                next_model_start = None
                                for mm in pattern_m.finditer(clause_lower[paid_match.end():]):
                                    next_model_start = paid_match.end() + mm.start()
                                    break
                                if next_model_start is None:
                                    continue
                                between = clause_lower[paid_match.end():next_model_start]
                                if re.fullmatch(r'[\s,;:()"\']*', between):
                                    continue
                                else:
                                    route_match = re.search(r'\broutes?\b|\bpool\b|\bmodels?\b', between)
                                    if route_match is None:
                                        continue
                                    before_route = between[:route_match.start()]
                                    if not re.fullmatch(r'[\s,;:()"\']*(?:[a-z0-9][a-z0-9_.\-]*[\s,;:()"\']+){0,2}', before_route):
                                        continue
                                    if re.search(r'\bhosting\b|\bstorage\b', before_route):
                                        continue
                                    after = between[route_match.end():]
                                    after_tokens = [t for t in re.split(r'[^a-z0-9]+', after.lower()) if t]
                                    allowed = set(("for", "of", "and", "or", "the", "a", "an", "route", "routes", "pool", "model", "models", "meta", "partner", "inference"))
                                    for _mid in ("muse-glimmer-30b", "muse-spark-1.3", "muse-wonder-1.0"):
                                        for _p in re.split(r'[^a-z0-9]+', _mid.lower()):
                                            if _p:
                                                allowed.add(_p)
                                    if any(t not in allowed for t in after_tokens):
                                        continue
                                    parts = [p for p in re.split(r'[^a-z0-9]+', m.lower()) if p]
                                    display_pat = r'[\s\-_./]*'.join(re.escape(p) for p in parts)
                                    if after.count("(") < after.count(")") or after.count("[") < after.count("]"):
                                        self.fail(f"Model {m} clause has excess closing delimiters: {clause!r}")
                                    if not re.search(r'\b(?:for|of)\b', after):
                                        if not re.search(display_pat, after):
                                            if ")" in after or "]" in after:
                                                stripped_models = after
                                                for lm in ("muse-glimmer-30b", "muse-spark-1.3", "muse-wonder-1.0"):
                                                    lparts = [q for q in re.split(r'[^a-z0-9]+', lm.lower()) if q]
                                                    lpat = r'[\s\-_./]*'.join(re.escape(q) for q in lparts)
                                                    stripped_models = re.sub(lpat, '', stripped_models)
                                                stripped_models = re.sub(r'\b(?:and|or|the|a|an|meta|partner|inference|route|routes|pool|model|models)\b', '', stripped_models)
                                                if re.fullmatch(r'[\s,;:()"\'.-]*', stripped_models):
                                                    continue
                                            if not re.fullmatch(r'[\s,;:()"\ ise\-]*'.replace(' ise', ''), after):
                                                rest = after
                                                for lm in ("muse-glimmer-30b", "muse-spark-1.3", "muse-wonder-1.0"):
                                                    lparts = [q for q in re.split(r'[^a-z0-9]+', lm.lower()) if q]
                                                    lpat = r'[\s\-_./]*'.join(re.escape(q) for q in lparts)
                                                    rest = re.sub(lpat, '', rest)
                                                rest = re.sub(r'\b(?:and|or|the|a|an|meta|partner|inference|route|routes|pool|model|models)\b', '', rest)
                                                if not re.fullmatch(r'[\s,;:()"\ ise\-]*'.replace(' ise', ''), rest):
                                                    continue
                                bound_other = False
                                for _lm in ("muse-glimmer-30b", "muse-spark-1.3", "muse-wonder-1.0"):
                                    if _lm.lower() == m.lower():
                                        continue
                                    _lparts = [q for q in re.split(r'[^a-z0-9]+', _lm.lower()) if q]
                                    _lpat = r'[\s\-_./]*'.join(re.escape(q) for q in _lparts)
                                    if re.search(r'\b(?:for|of)\b[^.!?]*?' + _lpat, between, re.IGNORECASE):
                                        bound_other = True
                                        break
                                if bound_other:
                                    continue
                                hijacked_fwd = False
                                for sm in re.finditer(r'\b([a-z0-9][a-z0-9_.-]*)\s+(?:is|was|are|were|be|been|being)\b', between):
                                    subj = sm.group(1)
                                    if subj in ("and", "or", "not", "it", "they", "them", "this", "that", "these", "those", "route", "routes", "model", "models"):
                                        continue
                                    if pattern_m.search(subj):
                                        continue
                                    hijacked_fwd = True
                                    break
                                if hijacked_fwd:
                                    continue
                                has_positive = True
                                continue
                            between = before[last_model_end:]
                            hijacked = False
                            for sm in re.finditer(r'\b([a-z0-9][a-z0-9_.-]*)\s+(?:is|was|are|were|be|been|being)\b', between):
                                subj = sm.group(1)
                                if subj in ("and", "or", "not", "it", "they", "them", "this", "that", "these", "those", "route", "routes", "model", "models"):
                                    continue
                                if pattern_m.search(subj):
                                    continue
                                hijacked = True
                                break
                            if hijacked:
                                continue
                            copulas = list(re.finditer(r'\b(?:is|was|are|were|be|been|being)\b', between))
                            if not copulas:
                                continue
                            tail = between[copulas[-1].end():]
                            if not re.fullmatch(r'[\s,;:]*((?:entirely|fully|completely|currently|still|also|really|very|quite)[\s,;:]*)*(?:(?:a|an|the)[\s,;:]+)?', tail):
                                continue
                            has_positive = True
            self.assertTrue(has_positive, f"Model {m} sentence lacks paid")
        self.assertRegex(self.lower, r'\bpaid\b')

    def test_summary_labels_historical_prompts_and_preserves_evidence(self):
        self.assertIn("historical", self.lower)
        self.assertTrue("prompt" in self.lower or "output" in self.lower)
        self.assertIn("preserved", self.lower)
        self.assertIn("evidence ledger", self.lower)
        self.assertIn("have not been rewritten", self.lower)

    def assert_summary_has_no_contradiction(self, summary):
        import re
        joined = re.sub(r'(?<!\n)\n(?!\n)', ' ', summary)
        sentences = re.split(r'(?<=[.!?])\s+|\n+', joined)
        pat_a = r'\b(?:every\s+line|all\s+code|entirely)\b'
        pat_b = r'\b(?:local(?:\s*-\s*|\s+)only|entirely\s+local|lm\s+studio)\b'
        paid_route_re = r'\bpaid\s+routes?\b'
        for sent in sentences:
            if re.search(pat_a, sent, re.IGNORECASE) and re.search(pat_b, sent, re.IGNORECASE):
                paid_route = re.search(paid_route_re, sent, re.IGNORECASE)
                local_route = re.search(pat_b, sent, re.IGNORECASE)
                if paid_route is not None and local_route is not None:
                    lo = min(paid_route.start(), local_route.start())
                    hi = max(paid_route.end(), local_route.end())
                    between = sent[lo:hi]
                    after_paid = sent[paid_route.end():]
                    if re.search(r'\bunpaid\b', sent, re.IGNORECASE):
                        pass
                    elif re.search(r'\b(?:rather\s+than|instead\s+of|avoid\w*|except|excluding|other\s+than)\b[^.!?]*paid\s+routes?\b', sent, re.IGNORECASE):
                        pass
                    elif re.search(r'\bnot\s+(?:only|merely|exclusively)\b[^.!?]*\bbut\s+also\b[^.!?]*\bpaid\s+routes?\b', sent, re.IGNORECASE):
                        self.fail(f"contradictory whole-codebase claim in sentence: {sent!r}")
                    elif re.search(r'\b(?:no|never|without)\b(?:\s+(?!humans\b)\w+){0,10}\s+paid\s+routes?\b', sent, re.IGNORECASE):
                        pass
                    elif re.search(r'\bnot\b(?:\s+(?!only\b|just\b|merely\b|exclusively\b|humans\b)\w+){0,10}\s+paid\s+routes?\b', sent, re.IGNORECASE):
                        pass
                    elif re.search(r'\bpaid\s+routes?\b[^.!?]*\b(?:not|never|without)\b[^.!?]*(?:\binvolv\w*\b|\bgenerat\w*\b|\bproduc\w*\b|\bcontribut\w*\b|\bused\b)', sent, re.IGNORECASE):
                        pass
                    elif re.search(r'\bpaid\s+routes?\b[^.!?]*(?:were|was)?\s*(?:not\s+used|never\s+used|unused|avoided)\b', sent, re.IGNORECASE):
                        pass
                    elif re.search(r'\band\b', between, re.IGNORECASE):
                        if re.search(r'\brather\s+than\b|\binstead\s+of\b', between, re.IGNORECASE):
                            pass
                        elif re.search(r'\bpaid\s+routes?\b[^.!?]*\b(?:not|never|without)\b[^.!?]*(?:\binvolv\w*\b|\bgenerat\w*\b|\bproduc\w*\b|\bcontribut\w*\b|\bused\b)', sent, re.IGNORECASE):
                            pass
                        elif re.search(r'\b(?:generat\w*|produc\w*|built|build\w*|wrote|written|creat\w*|ran|run\w*|using|used|use\w*|through|via|with|including|both)\b[^.!?]*\bpaid\s+routes?\b', sent, re.IGNORECASE):
                            if re.search(r'\bpaid\s+routes?\b[^.!?]*\b(?:not|never|without)\b', sent, re.IGNORECASE):
                                pass
                            else:
                                continue
                        elif re.search(r'\bpaid\s+routes?\b[^.!?]*\b(?:generat\w*|produc\w*|built|build\w*|wrote|written|creat\w*|ran|run\w*|used|use\w*|contribut\w*)\b', sent, re.IGNORECASE):
                            if re.search(r'\bpaid\s+routes?\b[^.!?]*\b(?:not|never|without)\b', sent, re.IGNORECASE):
                                pass
                            else:
                                continue
                        else:
                            pass
                self.fail(f"contradictory whole-codebase claim in sentence: {sent!r}")
        if re.search(r'\bunpaid\b', summary, re.IGNORECASE):
            self.fail("contradictory claim: unpaid")
        if re.search(r'\b(?:no|not|never|without)\s+(?:any\s+)?paid\b', summary, re.IGNORECASE):
            self.fail("contradictory claim: negated paid")

    def test_summary_rejects_old_blanket_local_only_claim(self):
        self.assertNotIn("all code in this repository was generated locally", self.lower)
        self.assertNotIn("no paid", self.lower)
        self.assert_summary_has_no_contradiction(self.summary)

    def test_blanket_claim_guard_rejects_original_phrases(self):
        for phrase in ("all code in this repository was generated locally", "no paid"):
            with self.subTest(phrase=phrase):
                obj = TestProvenanceSummary("test_summary_rejects_old_blanket_local_only_claim")
                obj.lower = phrase
                with self.assertRaises(AssertionError):
                    obj.test_summary_rejects_old_blanket_local_only_claim()

    def test_summary_contradiction_guard_rejects_paraphrases(self):
        fixtures = [
            "Every line of code here was generated by Meta muse-glimmer-30b (local LM Studio).",
            "All code was generated local-only by muse-glimmer-30b.",
            "The codebase was generated entirely local.",
            "All code ran on LM Studio.",
            "No paid routes were used.",
            "The routes were not paid.",
            "We never paid.",
            "We worked without any paid routes.",
            "We used no paid routes, though muse-spark-1.3 and muse-wonder-1.0 appear in the spend ledger.",
            "Every line of code was generated\nlocal-only by muse-glimmer-30b.",
            "All code was generated by Meta models using local-only models rather than paid routes.",
            "All code was generated local-only instead of paid routes.",
            "All code was generated local-only, avoiding paid routes.",
            "All code was entirely local, and the paid invoices were filed separately.",
            "All code was generated by Meta models using local-only models without making any use of paid routes.",
            "All code was generated by Meta models using local-only models and without making any use of paid routes.",
            "All code was generated local-only, not only via local models but also via paid routes.",
            "All code was generated local-only and not merely via local models in this repository but also through extensive paid routes.",
            "All code was generated local-only and not exclusively via local models in this repository but also through extensive paid routes.",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                with self.assertRaises(AssertionError):
                    self.assert_summary_has_no_contradiction(fixture)

    def test_summary_contradiction_guard_rejects_unrelated_avoidance_phrase(self):
        fixture = "All code was generated local-only, not merely via local models to avoid delays but also via paid routes."
        with self.assertRaisesRegex(AssertionError, "contradictory whole-codebase claim in sentence"):
            self.assert_summary_has_no_contradiction(fixture)

    def test_summary_contradiction_guard_rejects_denied_involvement(self):
        fixtures = [
            "All code was generated local-only and paid routes were not involved.",
            "All code was generated local-only and paid routes were never involved in generating project code.",
            "All code was generated local-only and paid routes did not generate any project code.",
            "All code was entirely local, and the paid routes were documented separately.",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                with self.assertRaises(AssertionError):
                    self.assert_summary_has_no_contradiction(fixture)

    def test_summary_contradiction_guard_accepts_current_summary(self):
        summary = _load()[1]
        self.assert_summary_has_no_contradiction(summary)

    def test_summary_contradiction_guard_accepts_mixed_provenance(self):
        fixtures = [
            "All code was generated by Meta models using both paid routes and local-only models.",
            "All code was generated by Meta models through paid routes and local-only routes, as recorded in the spend ledger.",
            "All code was generated by Meta models, not humans, using local-only and paid routes.",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self.assert_summary_has_no_contradiction(fixture)

    def test_public_snapshot_keeps_summary_without_private_execution_archive(self):
        self.assertIn("public-source-snapshot-20261102", self.content)
        self.assertIn("private evidence ledger", self.content.lower())
        self.assertNotIn("muse-spark-1.3 (meta pool, paid, logged to spend ledger)", self.content)
        self.assertNotRegex(self.content, r"(?m)^###? (?:Prompt|Output|Job|F[0-9]{3})")
        root = pathlib.Path(__file__).resolve().parents[1]
        self.assertFalse((root / "docs/activity-log.md").exists())
        self.assertFalse((root / "docs/provenance-docs.md").exists())
        self.assertFalse((root / "docs/activity").exists())

    def test_summary_excludes_paid_ledger_entries(self):
        self.assertNotIn("muse-spark-1.3 (meta pool, paid, logged to spend ledger)", self.summary)
        self.assertNotIn("muse-wonder-1.0 (meta pool, paid, logged to spend ledger)", self.summary)

    def test_paid_route_validation_cannot_skip_models(self):
        import re
        fixture = "Paid routes use the spend ledger. muse-spark-1.3 is recorded."
        obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
        obj.content = fixture
        obj.summary = fixture
        obj.lower = fixture.lower()
        with self.assertRaisesRegex(AssertionError, re.escape("Model muse-spark-1.3 sentence lacks paid")):
            obj.test_summary_mentions_paid_routes_and_spend_ledger()
        summary = "muse-spark-1.3 is paid and logged to the spend ledger. muse-wonder-1.0 is recorded."
        content = summary + "\nmuse-wonder-1.0 (historical prompt)\nmuse-wonder-1.0 (meta pool, paid, logged to spend ledger)"
        obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
        obj.content = content
        obj.summary = summary
        obj.lower = summary.lower()
        with self.assertRaisesRegex(AssertionError, re.escape("Model muse-wonder-1.0 sentence lacks paid")):
            obj.test_summary_mentions_paid_routes_and_spend_ledger()
        summary = "muse-spark-1.3 is paid and logged to the spend ledger. muse-wonder-1.0 is paid."
        content = summary + "\nmuse-wonder-1.0 (historical prompt)\nmuse-wonder-1.0 (meta pool, paid, logged to spend ledger)"
        obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
        obj.content = content
        obj.summary = summary
        obj.lower = summary.lower()
        obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_attribution_rejects_other_models_paid_clause(self):
        import re
        obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
        obj.setUp()
        invalid_summary = "muse-spark-1.3 was free, while muse-wonder-1.0 was paid and logged to the spend ledger."
        obj.summary = invalid_summary
        obj.lower = invalid_summary.lower()
        with self.assertRaisesRegex(AssertionError, re.escape("muse-spark-1.3")):
            obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_attribution_rejects_and_joined_cross_model_paid(self):
        content = (
            "muse-spark-1.3 (meta pool, paid, logged to spend ledger)\n"
            "muse-wonder-1.0 (meta pool, paid, logged to spend ledger)"
        )
        fixtures = [
            "Paid routes for muse-spark-1.3 and muse-wonder-1.0 generated documentation.",
            "Paid routes for muse-wonder-1.0 and muse-spark-1.3 generated documentation.",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                with self.assertRaises(AssertionError):
                    obj.test_summary_mentions_paid_routes_and_spend_ledger()


class TestPaidRouteValidation(unittest.TestCase):
    def test_paid_route_validation_scopes_status_to_model_clause(self):
        content = "muse-spark-1.3 (meta pool, paid, logged to spend ledger)"
        # normal cases
        for fixture in [
            "muse-glimmer-30b is unpaid; muse-spark-1.3 is paid through the spend ledger. muse-spark-1.3 also generated documentation.",
            "muse-glimmer-30b is not paid; muse-spark-1.3 is paid through the spend ledger. muse-spark-1.3 also generated documentation.",
        ]:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                obj.test_summary_mentions_paid_routes_and_spend_ledger()

        # failing cases
        failing_fixtures = [
            "muse-glimmer-30b is paid through the spend ledger; muse-spark-1.3 also generated documentation.",
            "muse-spark-1.3 is unpaid through the spend ledger.",
            "muse-spark-1.3 is not paid through the spend ledger.",
            "muse-spark-1.3 is unpaid through the spend ledger. muse-spark-1.3 is paid through the spend ledger.",
            "muse-spark-1.3 is not paid through the spend ledger. muse-spark-1.3 is paid through the spend ledger.",
        ]
        for fixture in failing_fixtures:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                with self.assertRaises(AssertionError):
                    obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_route_validation_rejects_conflicting_free_and_paid(self):
        content = "muse-spark-1.3 (meta pool, paid, logged to spend ledger)"
        must_raise = [
            "muse-spark-1.3 is free. muse-spark-1.3 is paid through the spend ledger.",
            "muse-spark-1.3 is paid through the spend ledger. muse-spark-1.3 is free.",
            "muse-spark-1.3 is free and paid through the spend ledger.",
            "muse-spark-1.3 is available for free and paid through the spend ledger.",
            "muse-spark-1.3 is free of charge and paid through the spend ledger.",
            "muse-spark-1.3 is available at no cost and paid through the spend ledger.",
            "muse-spark-1.3 costs nothing and is paid through the spend ledger.",
            "muse-spark-1.3 is not only available for free and paid through the spend ledger.",
            "muse-spark-1.3 is not cheap but available for free and paid through the spend ledger.",
            "muse-spark-1.3 is not cheap for free users and paid through the spend ledger.",
            "muse-spark-1.3 is not just available for free and paid through the spend ledger.",
        ]
        for fixture in must_raise:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                with self.assertRaises(AssertionError):
                    obj.test_summary_mentions_paid_routes_and_spend_ledger()

        must_pass = [
            "muse-glimmer-30b is free; muse-spark-1.3 is paid through the spend ledger.",
            "muse-spark-1.3 is not free and is paid through the spend ledger.",
            "muse-spark-1.3 is paid through the spend ledger and is not on a free tier.",
            "muse-spark-1.3 is paid through the spend ledger and is not offered free of charge.",
            "muse-spark-1.3 is paid through the spend ledger and is not available at no cost.",
            "muse-spark-1.3 is paid through the spend ledger and is never without charge.",
            "muse-spark-1.3 is paid through the spend ledger and never costs nothing.",
            "muse-spark-1.3 is paid through the spend ledger and is not currently available for free.",
            "muse-spark-1.3 is paid through the spend ledger and is not fully offered free of charge.",
            "muse-spark-1.3 is paid through the spend ledger and is not currently on a free tier.",
            "muse-spark-1.3 is paid through the spend ledger and is not currently available at no cost.",
        ]
        for fixture in must_pass:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_route_validation_rejects_output_word_preceding_model_free_claim(self):
        content = "muse-spark-1.3 (meta pool, paid, logged to spend ledger)"
        must_raise = [
            "muse-spark-1.3 is paid through the spend ledger and its output notes confirm it is free.",
            "muse-spark-1.3 is paid through the spend ledger with output data claiming it is free.",
        ]
        for fixture in must_raise:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                with self.assertRaises(AssertionError):
                    obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_route_validation_rejects_free_claim_preceding_model(self):
        content = "muse-spark-1.3 (meta pool, paid, logged to spend ledger)"
        must_raise = [
            "Being free, muse-spark-1.3 is paid through the spend ledger.",
            "It is free: muse-spark-1.3 is paid through the spend ledger.",
        ]
        for fixture in must_raise:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                with self.assertRaises(AssertionError):
                    obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_route_validation_accepts_unrelated_free_use(self):
        content = "muse-spark-1.3 (meta pool, paid, logged to spend ledger)"
        fixtures = [
            "muse-spark-1.3 is paid through the spend ledger and produces free software.",
            "muse-spark-1.3 is paid through the spend ledger and its output is free software.",
            "muse-spark-1.3 is paid through the spend ledger and its documentation is free.",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_route_validation_accepts_output_predicate_free(self):
        content = "muse-spark-1.3 (meta pool, paid, logged to spend ledger)"
        fixtures = [
            "its output generated by muse-spark-1.3 is free, and it is paid through the spend ledger.",
            "the output of muse-spark-1.3 is free, and it is paid through the spend ledger.",
            "their outputs produced by muse-spark-1.3 are free, and it is paid through the spend ledger.",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_route_validation_rejects_model_free_with_output_mention(self):
        content = "muse-spark-1.3 (meta pool, paid, logged to spend ledger)"
        must_raise = [
            "muse-spark-1.3 is free and its output is documented, and it is paid through the spend ledger.",
            "muse-spark-1.3 is free with output notes attached, and it is paid through the spend ledger.",
        ]
        for fixture in must_raise:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                with self.assertRaises(AssertionError):
                    obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_route_validation_rejects_unrelated_hosting_paid(self):
        content = "muse-spark-1.3 (meta pool, paid, logged to spend ledger)"
        fixture = "the output of muse-spark-1.3 is free, and unrelated hosting is paid through the spend ledger."
        obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
        obj.content = content
        obj.summary = fixture
        obj.lower = fixture.lower()
        with self.assertRaises(AssertionError):
            obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_route_validation_rejects_unrelated_paid_before_model(self):
        content = "muse-spark-1.3 (meta pool, paid, logged to spend ledger)"
        fixture = "The spend ledger records all routes. Paid hosting was logged and muse-spark-1.3 generated documentation."
        obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
        obj.content = content
        obj.summary = fixture
        obj.lower = fixture.lower()
        with self.assertRaises(AssertionError):
            obj.test_summary_mentions_paid_routes_and_spend_ledger()
        linked = "The spend ledger records all routes. The paid route for muse-spark-1.3 is logged to the spend ledger."
        obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
        obj.content = content
        obj.summary = linked
        obj.lower = linked.lower()
        obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_route_validation_rejects_unrelated_payment_without_copula(self):
        content = "muse-spark-1.3 (meta pool, paid, logged to spend ledger)"
        fixtures = [
            "the output of muse-spark-1.3 is free, with paid hosting logged to the spend ledger.",
            "muse-spark-1.3 generated documentation, with paid hosting logged to the spend ledger.",
            "the output of muse-spark-1.3 is free, with paid storage recorded in the spend ledger.",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                with self.assertRaises(AssertionError):
                    obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_route_validation_rejects_unrelated_payment_with_route_keywords(self):
        content = "muse-spark-1.3 (meta pool, paid, logged to spend ledger)"
        fixtures = [
            "Paid hosting routes logged for billing, and muse-spark-1.3 generated documentation.",
            "Paid hosting models logged for billing, and muse-spark-1.3 generated documentation.",
            "Paid hosting pool fees logged for billing, and muse-spark-1.3 generated documentation.",
            "Paid hosting routes for muse-spark-1.3 were logged to the spend ledger.",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                with self.assertRaises(AssertionError):
                    obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_route_validation_accepts_explicit_lists(self):
        content = (
            "muse-spark-1.3 (meta pool, paid, logged to spend ledger)\n"
            "muse-wonder-1.0 (meta pool, paid, logged to spend ledger)"
        )
        fixtures = [
            "Paid routes: muse-spark-1.3 are logged to the spend ledger. Paid routes: muse-wonder-1.0 are logged to the spend ledger.",
            "Paid routes: muse-spark-1.3, muse-wonder-1.0 are logged to the spend ledger.",
            "Paid routes (muse-spark-1.3, muse-wonder-1.0) are logged to the spend ledger.",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_route_validation_accepts_direct_article_attribution(self):
        content = "muse-spark-1.3 (meta pool, paid, logged to spend ledger)"
        fixtures = [
            "muse-spark-1.3 is a paid model, logged to the spend ledger.",
            "muse-spark-1.3 is the paid model logged to the spend ledger.",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_route_validation_rejects_lists_omitting_current_route(self):
        content = (
            "muse-spark-1.3 (meta pool, paid, logged to spend ledger)\n"
            "muse-wonder-1.0 (meta pool, paid, logged to spend ledger)"
        )
        fixtures = [
            "Paid routes (muse-spark-1.3) and muse-wonder-1.0 generated documentation.",
            "Paid routes (muse-wonder-1.0) and muse-spark-1.3 generated documentation.",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                with self.assertRaises(AssertionError):
                    obj.test_summary_mentions_paid_routes_and_spend_ledger()

    def test_paid_route_validation_rejects_misplaced_delimiter(self):
        content = "muse-spark-1.3 (meta pool, paid, logged to spend ledger)"
        fixtures = [
            "Paid routes for) muse-spark-1.3 are logged to the spend ledger.",
            "Paid routes for] muse-spark-1.3 are logged to the spend ledger.",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                with self.assertRaises(AssertionError):
                    obj.test_summary_mentions_paid_routes_and_spend_ledger()


    def test_paid_route_validation_rejects_excess_closers_after_valid_attribution(self):
        content = "muse-spark-1.3 (meta pool, paid, logged to spend ledger)"
        fixtures = [
            "muse-spark-1.3 is paid through the spend ledger. Paid routes ) muse-spark-1.3 generated documentation.",
            "muse-spark-1.3 is paid through the spend ledger. Paid routes ] muse-spark-1.3 generated documentation.",
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                obj = TestProvenanceSummary("test_summary_mentions_paid_routes_and_spend_ledger")
                obj.content = content
                obj.summary = fixture
                obj.lower = fixture.lower()
                with self.assertRaises(AssertionError):
                    obj.test_summary_mentions_paid_routes_and_spend_ledger()


if __name__ == "__main__":
    unittest.main()
