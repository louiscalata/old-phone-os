# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
import sys
import unittest
import unittest.mock

from tests.install_review_support import (
    _assert_snippet_confined,
    _group_for_case_headers,
    _header_substitution_code,
    _join_continuations,
    _line_path_violations,
    _mask_heredoc_bodies,
    _read_install_sh,
    _scan_installer_paths,
    _scan_snippet_unquoted_dynamic,
    _shell_delimiter_index,
    _strip_shell_comment,
)


class TestInstallReview(unittest.TestCase):
    def test_f1116_substitution_spans_quote_context(self):
        spans = []
        a = chr(96)
        b = chr(96)
        txt = "x=" + a + "a" + b + " y=$(b) z='$(c)' w=\"$(d)\""
        out = _header_substitution_code(txt, spans)
        self.assertEqual(out, "a b d")
        self.assertEqual(len(spans), 3)
        s0, e0, q0 = spans[0]
        s1, e1, q1 = spans[1]
        s2, e2, q2 = spans[2]
        self.assertEqual(txt[s0:e0], a + "a" + b)
        self.assertEqual(txt[s1:e1], "$(b)")
        self.assertEqual(txt[s2:e2], "$(d)")
        self.assertFalse(q0)
        self.assertFalse(q1)
        self.assertTrue(q2)
        spans2 = []
        nested = "cmd=$(outer$(inner))"
        _header_substitution_code(nested, spans2)
        self.assertEqual(len(spans2), 1)
        self.assertEqual(spans2, [(4, 20, False)])
        self.assertEqual(nested[spans2[0][0] : spans2[0][1]], "$(outer$(inner))")
        self.assertFalse(spans2[0][2])

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_f1116_command_substitution_operands(self):
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        bt = chr(96)
        dq = chr(34)
        sq = chr(39)
        nl = chr(10)
        dollar = ds + lp + "gen_target" + rp
        ticks = bt + "gen_target" + bt
        cases = [
            ("dollar_unquoted", "rm -rf " + dollar + "/rest", True),
            ("backtick_unquoted", "rm -rf " + ticks + "/rest", True),
            ("dollar_quoted", "rm -rf " + dq + dollar + "/rest" + dq, False),
            ("backtick_quoted", "rm -rf " + dq + ticks + "/rest" + dq, False),
        ]
        for name, line, is_unquoted in cases:
            with self.subTest(command=name):
                snippet = line + nl
                if is_unquoted:
                    want = ([line], [])
                    message = "must quote every path operand"
                else:
                    want = ([], [line])
                    message = "must not use dynamically built paths"
                self.assertEqual(_scan_snippet_unquoted_dynamic(snippet), want)
                with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=snippet):
                    with self.assertRaisesRegex(AssertionError, message):
                        self.test_production_paths_reject_unquoted_and_dynamic()
        controls = [
            ("dollar_single", "rm -rf " + sq + dollar + "/rest" + sq),
            ("backtick_single", "rm -rf " + sq + ticks + "/rest" + sq),
        ]
        for name, line in controls:
            with self.subTest(control=name):
                snippet = line + nl
                self.assertEqual(_scan_snippet_unquoted_dynamic(snippet), ([], []))
                with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=snippet):
                    self.test_production_paths_reject_unquoted_and_dynamic()

    def test_unquoted_heredoc_substitution_rejects_unsafe_removal(self):
        star = chr(42)
        dq = chr(34)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        bt = chr(96)
        bs = chr(92)
        nl = chr(10)
        base = dq + "$BASE" + dq + "/"
        inner = "rm -rf " + base + star
        for snippet in (
            "cat <<EOF" + nl + ds + lp + inner + rp + nl + "EOF" + nl,
            "cat <<EOF" + nl + bt + inner + bt + nl + "EOF" + nl,
        ):
            with self.subTest(snippet=snippet[:8]):
                with self.assertRaisesRegex(AssertionError, "unconfined path operation"):
                    _assert_snippet_confined(snippet)
        quoted_snippets = (
            "cat <<'EOF'" + nl + ds + lp + inner + rp + nl + "EOF" + nl,
            'cat <<"EOF"' + nl + ds + lp + inner + rp + nl + "EOF" + nl,
            "cat <<EO" + bs + "F" + nl + ds + lp + inner + rp + nl + "EOF" + nl,
        )
        for snippet in quoted_snippets:
            with self.subTest(snippet=snippet[:8]):
                _assert_snippet_confined(snippet)
        escaped = (
            "cat <<EOF" + nl + bs + ds + lp + inner + rp + nl + "EOF" + nl,
            "cat <<EOF" + nl + bs + bt + inner + bt + nl + "EOF" + nl,
        )
        for snippet in escaped:
            with self.subTest(snippet=snippet[:8]):
                _assert_snippet_confined(snippet)
        _assert_snippet_confined("cat <<EOF" + nl + inner + nl + "EOF" + nl)

    def test_unquoted_heredoc_split_substitution_rejects_unsafe_removal(self):
        star = chr(42)
        dq = chr(34)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        bs = chr(92)
        nl = chr(10)
        base = dq + ds + "BASE" + dq + "/"
        inner = "rm -rf " + base + star
        split = ds + bs + nl + lp + inner + rp
        snippet = "cat <<EOF" + nl + split + nl + "EOF" + nl
        with self.assertRaisesRegex(AssertionError, "unconfined path operation"):
            _assert_snippet_confined(snippet)
        _assert_snippet_confined("cat <<'EOF'" + nl + split + nl + "EOF" + nl)
        _assert_snippet_confined("cat <<EOF" + nl + bs + split + nl + "EOF" + nl)
        _assert_snippet_confined("cat <<EOF" + nl + ds + bs + bs + nl + lp + inner + rp + nl + "EOF" + nl)

    def test_p018_heredoc_substitution_rejects_comment_and_case(self):
        h = chr(35)
        star = chr(42)
        dq = chr(34)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        nl = chr(10)
        sq = chr(39)
        inner = "rm -rf " + dq + ds + "BASE" + dq + "/" + star
        comment_body = ds + lp + "echo ok " + h + " " + rp + nl + inner + nl + rp
        case_kw = "".join(["ca", "se"])
        case_body = ds + lp + case_kw + " x in x" + rp + nl + inner + nl + ";; esac" + rp
        for label, body in (("comment", comment_body), ("case", case_body)):
            with self.subTest(bypass=label):
                snippet = "cat <<EOF" + nl + body + nl + "EOF" + nl
                with self.assertRaisesRegex(AssertionError, "unsupported heredoc substitution syntax"):
                    _assert_snippet_confined(snippet)
        for label, body in (("comment", comment_body), ("case", case_body)):
            with self.subTest(quoted_delim=label):
                _assert_snippet_confined("cat <<'EOF'" + nl + body + nl + "EOF" + nl)
                _assert_snippet_confined('cat <<"EOF"' + nl + body + nl + "EOF" + nl)
        quoted_hash = ds + lp + "printf " + sq + "%s" + sq + " " + sq + h + " " + rp + sq + rp
        quoted_case = ds + lp + "printf " + sq + "%s" + sq + " " + sq + case_kw + " x in x" + sq + rp
        for label, sub in (("hash", quoted_hash), ("case", quoted_case)):
            with self.subTest(quoted_content=label):
                _assert_snippet_confined("cat <<EOF" + nl + sub + nl + "EOF" + nl)

    def test_unterminated_unquoted_heredoc_substitution_rejects_unsafe_removal(self):
        star = chr(42)
        dq = chr(34)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        bt = chr(96)
        nl = chr(10)
        base = dq + "$BASE" + dq + "/"
        inner = "rm -rf " + base + star
        subs = (
            ds + lp + inner + rp,
            bt + inner + bt,
        )
        for sub in subs:
            for ending in ("", nl):
                snippet = "cat <<EOF" + nl + sub + ending
                with self.subTest(sub=sub[:1], newline=ending != ""):
                    with self.assertRaisesRegex(AssertionError, "unconfined path operation"):
                        _assert_snippet_confined(snippet)

    def test_empty_quoted_heredoc_body_quote_does_not_hide_command(self):
        star = chr(42)
        dq = chr(34)
        nl = chr(10)
        base = dq + "$BASE" + dq + "/"
        inner = "rm -rf " + base + star
        for opener in ("cat <<''" + nl, 'cat <<""' + nl):
            snippet = opener + dq + nl + nl + inner + nl
            with self.subTest(opener=opener[:8]):
                with self.assertRaisesRegex(AssertionError, "unconfined path operation"):
                    _assert_snippet_confined(snippet)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_production_paths_reject_unquoted_and_dynamic(self):
        text = _read_install_sh()
        unquoted, dynamic = _scan_installer_paths(text)
        probe_unquoted = 'rm -rf $TARGET/rest\nmv $SRC "$DST"\n'
        probe_dynamic = 'rm -rf "$BASE/$SUFFIX"\n'
        probe_fixed = 'rm -rf "$BASE/static-leaf"\n'
        probe_concat = 'rm -rf "$BASE"/"$SUFFIX"\n'
        probe_split = 'mv "$SRC" "$DST"\n'
        probe_concat_static = 'rm -rf "$BASE"/static-leaf\n'
        probe_positional_unquoted = 'rm -rf $1/rest\nmv $2 "$DST"\n'
        probe_special_unquoted = "rm -rf $@\nrm -rf $*/leaf\n"
        probe_positional_dynamic = 'rm -rf "$BASE/$1"\n'
        probe_arglist_dynamic = 'rm -rf "$BASE/$@"\nrm -rf "$BASE/$*"\n'

        def _scan(snippet):
            return _scan_installer_paths(snippet)

        puq, _ = _scan(probe_unquoted)
        self.assertEqual(len(puq), 2, "scan must catch unquoted operands")
        _, pdyn = _scan(probe_dynamic)
        self.assertEqual(len(pdyn), 1, "scan must catch dynamic quoted paths")
        _, cpdyn = _scan(probe_concat)
        self.assertEqual(len(cpdyn), 1, "scan must catch split dynamic paths")
        fuq, fdyn = _scan(probe_fixed)
        self.assertEqual(fuq, [], "scan must accept static quoted paths")
        self.assertEqual(fdyn, [], "scan must accept static quoted paths")
        suq, spdyn = _scan(probe_split)
        self.assertEqual(suq, [], "scan must accept separate quoted operands")
        self.assertEqual(spdyn, [], "scan must accept separate quoted operands")
        csuq, csdyn = _scan(probe_concat_static)
        self.assertEqual(csuq, [], "scan must accept split static suffix")
        self.assertEqual(csdyn, [], "scan must accept split static suffix")
        puq_pos, _ = _scan(probe_positional_unquoted)
        self.assertEqual(len(puq_pos), 2, "scan must catch unquoted positional operands")
        suq_spec, _ = _scan(probe_special_unquoted)
        self.assertEqual(len(suq_spec), 2, "scan must catch unquoted special operands")
        _, pdyn_pos = _scan(probe_positional_dynamic)
        self.assertEqual(len(pdyn_pos), 1, "scan must catch dynamic paths from positional params")
        _, pdyn_arg = _scan(probe_arglist_dynamic)
        self.assertEqual(len(pdyn_arg), 2, "scan must catch dynamic paths from argument lists")
        probe_arglist_prefix = 'rm -rf "$@/leaf"\nrm -rf "$*/leaf"\n'
        _, pdyn_prefix = _scan(probe_arglist_prefix)
        self.assertEqual(len(pdyn_prefix), 2, "scan must catch argument lists with static suffix")
        proc = subprocess.run(
            ["sh", "-c", 'set -- alpha beta; printf "<%s>\\n" "$@/leaf"'],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith("/leaf>"))
        self.assertNotEqual(lines[0], lines[1])
        self.assertEqual(unquoted, [], "filesystem ops must quote every path operand")
        self.assertEqual(dynamic, [], "filesystem ops must not use dynamically built paths")

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_regression_probes_and_installer_check_share_scanner(self):
        probe = 'rm -rf "$BASE/$SUFFIX"\n'
        uq, dyn = _scan_installer_paths(probe)
        self.assertEqual(len(dyn), 1)
        self.assertEqual(uq, [])
        with unittest.mock.patch.object(
            sys.modules[__name__], "_scan_installer_paths", wraps=_scan_installer_paths
        ) as scan_mock:
            self.test_production_paths_reject_unquoted_and_dynamic()
            self.assertGreater(scan_mock.call_count, 0)
            probed = [c.args[0] for c in scan_mock.call_args_list if "$SUFFIX" in c.args[0]]
            self.assertTrue(probed)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_production_paths_reject_split_positional_and_special_parameters(self):
        from unittest.mock import patch

        cases = (
            ("1", 'rm -rf "$BASE"/"$1"'),
            ("@", 'rm -rf "$BASE"/"$@"'),
            ("*", 'rm -rf "$BASE"/"$*"'),
        )
        for param, snippet in cases:
            with self.subTest(param=param):
                with patch("tests.test_install_review._read_install_sh", return_value=snippet):
                    with self.assertRaisesRegex(AssertionError, "filesystem ops must not use dynamically built paths"):
                        self.test_production_paths_reject_unquoted_and_dynamic()
        with patch("tests.test_install_review._read_install_sh", return_value='rm -rf "$BASE"/static-leaf'):
            self.test_production_paths_reject_unquoted_and_dynamic()

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_production_paths_catch_unsafe_command_after_continuation_separator(self):
        from unittest.mock import patch

        for sep in ("", "# separator"):
            with self.subTest(sep=sep):
                marker = "echo " + chr(34) + "# marker" + chr(34) + " " + chr(92)
                payload = marker + chr(10) + sep + chr(10) + "rm -rf " + chr(36) + "TARGET/rest" + chr(10)
                with patch("tests.test_install_review._read_install_sh", return_value=payload):
                    with self.assertRaisesRegex(AssertionError, "filesystem ops must quote every path operand"):
                        self.test_production_paths_reject_unquoted_and_dynamic()

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_production_paths_quoted_hash_does_not_hide_command(self):
        from unittest.mock import patch

        snippets = [
            'printf "%s" "#"; rm -rf $TARGET/rest',
            "printf '%s' '#'; rm -rf $TARGET/rest",
        ]
        for snippet in snippets:
            with patch("tests.test_install_review._read_install_sh", return_value=snippet):
                with self.assertRaisesRegex(AssertionError, "filesystem ops must quote every path operand"):
                    self.test_production_paths_reject_unquoted_and_dynamic()

        # dynamic operand after quoted hash must still be rejected
        dynamic_snippet = 'printf "%s" "#"; rm -rf "$BASE/$SUFFIX"'
        with patch("tests.test_install_review._read_install_sh", return_value=dynamic_snippet):
            with self.assertRaisesRegex(AssertionError, "filesystem ops must not use dynamically built paths"):
                self.test_production_paths_reject_unquoted_and_dynamic()

        # real trailing comment must be stripped and not hide unsafe command
        comment_snippet = 'rm -rf "$BASE/static-leaf" # rm -rf $TARGET/rest'
        with patch("tests.test_install_review._read_install_sh", return_value=comment_snippet):
            self.test_production_paths_reject_unquoted_and_dynamic()

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_production_paths_catch_escaped_space_operands(self):
        q = chr(34)
        bs = chr(92)
        probe = "rm -rf " + q + "$BASE" + q + "/some" + bs + " " + chr(126) + "/rel/" + q + "$SUFFIX" + q + "\n"
        self.assertEqual(_line_path_violations(probe.strip()), [])
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=probe):
            with self.assertRaisesRegex(AssertionError, "must not use dynamically built paths"):
                self.test_production_paths_reject_unquoted_and_dynamic()

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_production_paths_catch_continued_operands(self):
        text = _read_install_sh()
        self.test_production_paths_reject_unquoted_and_dynamic()
        head = 'rm -rf "$BASE/static-leaf" \\'
        tail = '"$BASE/$SUFFIX"'
        mutant = text + "\n" + head + "\n    " + tail + "\n"
        self.assertNotEqual(mutant, text)
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
            with self.assertRaises(AssertionError):
                self.test_production_paths_reject_unquoted_and_dynamic()
        safe = text + "\n" + head + "\n    " + '"$BASE/static-leaf"\n'
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=safe):
            self.test_production_paths_reject_unquoted_and_dynamic()

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_production_paths_catch_continued_wildcard_operands(self):
        text = _read_install_sh()
        self.test_production_paths_reject_unquoted_and_dynamic()
        star_tail = "*" + ".bak"
        head = 'rm -rf "$BASE/static-leaf" \\'
        mutant = text + "\n" + head + "\n    " + star_tail + "\n"
        self.assertNotEqual(mutant, text)
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
            with self.assertRaises(AssertionError):
                self.test_production_paths_reject_unquoted_and_dynamic()
        safe = text + "\n" + head + "\n    " + '"$BASE/static-leaf"\n'
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=safe):
            self.test_production_paths_reject_unquoted_and_dynamic()

    def test_continuation_preserves_indented_unsafe_operand(self):
        probe = "rm" + chr(92) + chr(10) + "    " + chr(126) + "/opos-x"
        joined = _join_continuations(probe)
        self.assertIn("rm    " + chr(126), joined)
        self.assertTrue(_line_path_violations(joined))
        safe = "rm" + chr(92) + chr(10) + "    " + chr(34) + "$OPOS_HOME/x" + chr(34)
        self.assertEqual(_line_path_violations(_join_continuations(safe)), [])
        glued = "rm" + chr(92) + chr(10) + "bar"
        self.assertEqual(_join_continuations(glued), "rmbar")

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_continuation_blank_and_comment_lines_preserve_boundaries(self):
        bs = chr(92)
        nl = chr(10)
        h = chr(35)
        dq = chr(34)
        ds = chr(36)
        tgt = ds + "TARGET/rest"
        base = dq + ds + "BASE" + dq
        suffix = dq + ds + "SUFFIX" + dq
        for sep in ("", "   ", h + " separator", "   " + h + " indented"):
            with self.subTest(sep=repr(sep)):
                marker = "echo foo" + bs
                payload = marker + nl + sep + nl + "rm -rf " + tgt + nl
                with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=payload):
                    with self.assertRaisesRegex(AssertionError, "filesystem ops must quote every path operand"):
                        self.test_production_paths_reject_unquoted_and_dynamic()
                safe_head = "rm -rf " + base + "/static-leaf " + bs
                safe_tail = base + "/" + suffix
                safe_payload = safe_head + nl + sep + nl + safe_tail + nl
                with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=safe_payload):
                    self.test_production_paths_reject_unquoted_and_dynamic()

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_production_paths_comment_backslash_does_not_swallow_command(self):
        text = _read_install_sh()
        self.test_production_paths_reject_unquoted_and_dynamic()
        mutant = text + "\n# note \\\nrm -rf $TARGET/rest\n"
        self.assertNotEqual(mutant, text)
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
            with self.assertRaises(AssertionError):
                self.test_production_paths_reject_unquoted_and_dynamic()

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_production_paths_subst_comment_backslash_does_not_swallow_command(self):
        text = _read_install_sh()
        self.test_production_paths_reject_unquoted_and_dynamic()
        dq = chr(34)
        ds = chr(36)
        op = chr(40)
        cp = chr(41)
        h = chr(35)
        bs = chr(92)
        nl = chr(10)
        tgt = ds + "TARGET/rest"
        mutant = text + nl + "echo " + dq + ds + op + " " + h + " note " + bs + nl + "rm -rf " + tgt + nl + cp + dq + nl
        self.assertNotEqual(mutant, text)
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
            with self.assertRaises(AssertionError):
                self.test_production_paths_reject_unquoted_and_dynamic()

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_production_paths_inline_comment_and_single_quote_continuations(self):
        text = _read_install_sh()
        self.test_production_paths_reject_unquoted_and_dynamic()
        bs = chr(92)
        nl = chr(10)
        h = chr(35)
        q = chr(39)
        dq = chr(34)
        inline = (
            text
            + nl
            + "rm -rf "
            + dq
            + "$BASE/static-leaf"
            + dq
            + " "
            + h
            + " note "
            + bs
            + nl
            + "rm -rf $TARGET/rest"
            + nl
        )
        self.assertNotEqual(inline, text)
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=inline):
            with self.assertRaises(AssertionError):
                self.test_production_paths_reject_unquoted_and_dynamic()
        quoted = "echo " + q + "a " + bs + nl + "b" + q + nl
        self.assertIn(bs + nl, _join_continuations(quoted))
        safe = text + nl + "echo " + q + "a " + bs + nl + "b" + q + nl
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=safe):
            self.test_production_paths_reject_unquoted_and_dynamic()

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_production_paths_heredoc_quote_does_not_swallow_command(self):
        text = _read_install_sh()
        self.test_production_paths_reject_unquoted_and_dynamic()
        bs = chr(92)
        nl = chr(10)
        h = chr(35)
        dq = chr(34)
        heredoc = "cat <<EOF" + nl + dq + nl + "EOF" + nl
        mutant = (
            text
            + nl
            + heredoc
            + "rm -rf "
            + dq
            + "$BASE/static-leaf"
            + dq
            + " "
            + h
            + " note "
            + bs
            + nl
            + "rm -rf $TARGET/rest"
            + nl
        )
        self.assertNotEqual(mutant, text)
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
            with self.assertRaises(AssertionError):
                self.test_production_paths_reject_unquoted_and_dynamic()
        safe = text + nl + heredoc + "rm -rf " + dq + "$BASE/static-leaf" + dq + nl
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=safe):
            self.test_production_paths_reject_unquoted_and_dynamic()

    def test_production_paths_heredoc_hash_delimiter_keeps_command(self):
        h = chr(35)
        star = chr(42)
        delim = "END" + h + "tag"
        cmd = "rm -rf " + chr(34) + "$BASE" + chr(34) + "/" + star
        snippet = "cat <<" + delim + "\nbody\n" + delim + "\n" + cmd + "\n"
        masked = _mask_heredoc_bodies(snippet)
        self.assertIn(cmd, masked)
        with self.assertRaises(AssertionError):
            _assert_snippet_confined(snippet)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_production_paths_reject_bare_glob_and_tilde(self):
        tilde_x = "~" + "/x"
        tilde_y = "~" + "/y"
        star_bak = "*" + ".bak"
        home_var = "$" + "OPOS_HOME"
        bad = [
            "rm -rf " + home_var + "/" + "*",
            "rm -rf " + '"' + home_var + '"' + "/" + "*",
            "rm -rf " + tilde_x,
            "cp a " + "~",
            "mv a " + star_bak,
            "X=" + tilde_y,
        ]
        for line in bad:
            with self.subTest(line=line):
                self.assertTrue(_line_path_violations(line), "must flag: %r" % line)
        good = [
            "rm -rf " + '"' + home_var + "/" + "*" + '"',
            "rm -rf " + '"' + tilde_x + '"',
            "rm -rf " + "'" + "*" + "'",
            "rm -rf a" + "~" + "b",
            "rm -rf " + "\\" + "*",
        ]
        for line in good:
            with self.subTest(line=line):
                self.assertFalse(_line_path_violations(line), "must pass: %r" % line)
        text = _read_install_sh()
        anchor = text.splitlines()[0]
        self.assertTrue(anchor is not None)
        fixture = text + "\nrm -rf " + tilde_x + "\n"
        self.assertNotEqual(fixture, text)
        with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=fixture):
            with self.assertRaises(AssertionError):
                self.test_production_paths_reject_unquoted_and_dynamic()

    def test_production_paths_hash_inside_word_keeps_glob(self):
        h = chr(35)
        s = chr(42)
        q = chr(34)
        base = q + "$BASE" + q
        inside = "rm -rf " + base + "/foo" + h + s + ".bak"
        self.assertTrue(_line_path_violations(inside), "must flag: %r" % inside)
        quoted = "rm -rf " + q + h + "hash" + q + " " + base + "/" + s
        self.assertTrue(_line_path_violations(quoted), "must flag: %r" % quoted)
        comment = "rm -rf " + base + "/static-leaf " + h + " comment " + s
        self.assertEqual(_line_path_violations(comment), [])

    def test_production_paths_escaped_separator_keeps_glob(self):
        h = chr(35)
        s = chr(42)
        q = chr(34)
        bs = chr(92)
        sp = chr(32)
        semi = chr(59)
        base = q + "$BASE" + q
        escaped_space = "rm -rf " + base + "/foo" + bs + sp + h + s + ".bak"
        escaped_semi = "rm -rf " + base + "/foo" + bs + semi + h + s + ".bak"
        for line in (escaped_space, escaped_semi):
            with self.subTest(line=line):
                self.assertIn(s, _strip_shell_comment(line))
                self.assertTrue(_line_path_violations(line), "must flag: %r" % line)

    def test_production_paths_inspect_for_and_case_bodies(self):
        star = chr(42)
        base = '"$BASE"'
        for_body = "for f in a b; do rm -rf " + base + "/" + star + "; done"
        case_body = 'case "$MODE" in fast) rm -rf ' + base + "/" + star + ";; esac"
        self.assertTrue(_line_path_violations(for_body), "for body must be inspected: %r" % for_body)
        self.assertTrue(_line_path_violations(case_body), "case body must be inspected: %r" % case_body)
        for_safe = "for f in " + star + ".sh; do echo ok; done"
        case_safe = 'case "$MODE" in ' + star + ".bak) echo ok;; esac"
        self.assertEqual(_line_path_violations(for_safe), [])
        self.assertEqual(_line_path_violations(case_safe), [])

    def test_tab_separated_for_case_headers(self):
        tab = chr(9)
        sp = chr(32)
        star = chr(42)
        dq = chr(34)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        nl = chr(10)
        base = dq + ds + "BASE" + dq
        inner = "echo " + base + "/" + star
        sub = ds + lp + inner + rp
        seps = [("space", sp), ("tab", tab)]
        for sep_name, sep in seps:
            for keyword in ("for", "case"):
                with self.subTest(sep=sep_name, keyword=keyword):
                    if keyword == "for":
                        header = "for" + sep + "f in " + sub
                        delimiter = "do"
                        body = "echo ok"
                        terminator = "done"
                    else:
                        header = "case" + sep + sub
                        delimiter = "in"
                        body = "fast) echo ok;;"
                        terminator = "esac"
                    snippet = header + nl + delimiter + nl + body + nl + terminator + nl
                    grouped = _group_for_case_headers(snippet.splitlines())
                    combined = header + nl + delimiter
                    self.assertEqual(grouped[0], combined)
                    unquoted, dynamic = _scan_installer_paths(snippet)
                    self.assertEqual(unquoted, [combined.strip()])
                    self.assertEqual(dynamic, [])
        mode = dq + ds + "MODE" + dq
        for_control = "for" + tab + "f in " + star + ".sh; do echo ok; done"
        case_control = "case" + tab + mode + " in " + star + ".bak) echo ok;; esac"
        self.assertEqual(_line_path_violations(for_control), [])
        self.assertEqual(_line_path_violations(case_control), [])

    def test_production_paths_case_quoted_delimiters_preserve_body(self):
        semi = chr(59)
        rparen = chr(41)
        star = chr(42)
        dq = chr(34)
        sq = chr(39)
        base = '"$BASE"'
        quoted_semi_dq = dq + semi + semi + dq
        quoted_paren_dq = dq + rparen + dq
        quoted_semi_sq = sq + semi + semi + sq
        quoted_paren_sq = sq + rparen + sq
        line_dq = (
            "case "
            + dq
            + "$MODE"
            + dq
            + " in "
            + star
            + rparen
            + " echo "
            + quoted_semi_dq
            + semi
            + " rm -rf "
            + base
            + "/"
            + star
            + semi
            + " echo "
            + quoted_paren_dq
            + semi
            + semi
            + " esac"
        )
        self.assertTrue(_line_path_violations(line_dq), "quoted delimiters must not erase body: %r" % line_dq)
        line_sq = (
            "case "
            + dq
            + "$MODE"
            + dq
            + " in "
            + star
            + rparen
            + " echo "
            + quoted_semi_sq
            + semi
            + " rm -rf "
            + base
            + "/"
            + star
            + semi
            + " echo "
            + quoted_paren_sq
            + semi
            + semi
            + " esac"
        )
        self.assertTrue(_line_path_violations(line_sq), "quoted delimiters must not erase body: %r" % line_sq)

    def test_production_paths_inspect_commands_after_esac(self):
        star = chr(42)
        base = '"$BASE"'
        dq = chr(34)
        semi = chr(59)
        line = (
            "case "
            + dq
            + "$MODE"
            + dq
            + " in fast) echo ok"
            + semi
            + semi
            + " esac"
            + semi
            + " rm -rf "
            + base
            + "/"
            + star
        )
        self.assertTrue(_line_path_violations(line), "commands after esac must be inspected: %r" % line)

    def test_production_paths_inspect_header_substitutions(self):
        star = chr(42)
        base = '"$BASE"'
        dollar = chr(36)
        lparen = chr(40)
        rparen = chr(41)
        btick = chr(96)
        inner = "rm -rf " + base + "/" + star
        dollar_sub = dollar + lparen + inner + rparen
        tick_sub = btick + inner + btick
        unsafe = [
            "for f in " + dollar_sub + "; do echo ok; done",
            "case " + dollar_sub + " in fast) echo ok;; esac",
            "for f in " + tick_sub + "; do echo ok; done",
            "case " + tick_sub + " in fast) echo ok;; esac",
        ]
        for line in unsafe:
            with self.subTest(line=line):
                self.assertTrue(_line_path_violations(line), "header substitution must be inspected: %r" % line)
        safe_inner = dollar + lparen + "echo ok" + rparen
        safe = [
            "for f in " + star + ".sh; do echo ok; done",
            'case "$MODE" in ' + star + ".bak) echo ok;; esac",
            "for f in " + safe_inner + "; do echo ok; done",
            "case " + safe_inner + " in fast) echo ok;; esac",
        ]
        for line in safe:
            with self.subTest(line=line):
                self.assertEqual(_line_path_violations(line), [])

    def test_p039_case_delimiter_skips_complete_subject(self):
        dq = chr(34)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        star = chr(42)
        base = dq + ds + "BASE" + dq
        sub_a = "/in/" + ds + lp + "rm -rf " + base + "/" + star + rp
        sub_b = "in" + ds + lp + "rm -rf " + base + "/" + star + rp
        for sub in (sub_a, sub_b):
            line = "case " + sub + " in x) echo ok;; esac"
            self.assertEqual(_shell_delimiter_index(line, "in"), line.rfind(" in ") + 1)
            self.assertTrue(_line_path_violations(line))
        safe_a = "in"
        safe_b = "/in/" + ds + lp + "echo ok" + rp
        for sub in (safe_a, safe_b):
            line = "case " + sub + " in x) echo ok;; esac"
            self.assertEqual(_shell_delimiter_index(line, "in"), line.rfind(" in ") + 1)
            self.assertEqual(_line_path_violations(line), [])

    def test_production_paths_inspect_multiline_header_substitutions(self):
        star = chr(42)
        dq = chr(34)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        nl = chr(10)
        base = dq + "$BASE" + dq
        inner = "rm -rf " + base + "/" + star
        sub = ds + lp + inner + rp
        snippets = [
            "for f in " + sub + nl + "do" + nl + "  echo ok" + nl + "done" + nl,
            "case " + sub + nl + "in" + nl + "  fast) echo ok;;" + nl + "esac" + nl,
        ]
        for snippet in snippets:
            with self.subTest(snippet=snippet.splitlines()[0]):
                with self.assertRaises(AssertionError):
                    _assert_snippet_confined(snippet)

    def test_production_paths_inspect_nested_subshell_and_arithmetic(self):
        star = chr(42)
        base = '"$BASE"'
        dollar = chr(36)
        lparen = chr(40)
        rparen = chr(41)
        nested_subshell = (
            "for f in "
            + dollar
            + lparen
            + " "
            + lparen
            + "echo inner"
            + rparen
            + "; rm -rf "
            + base
            + "/"
            + star
            + " "
            + rparen
            + "; do echo ok; done"
        )
        nested_arith = (
            "for f in "
            + dollar
            + lparen
            + "echo "
            + dollar
            + lparen
            + lparen
            + "1 + 2"
            + rparen
            + rparen
            + "; rm -rf "
            + base
            + "/"
            + star
            + " "
            + rparen
            + "; do echo ok; done"
        )
        for line in (nested_subshell, nested_arith):
            with self.subTest(line=line):
                self.assertTrue(_line_path_violations(line), "nested construct must not hide deletion: %r" % line)

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_production_paths_arithmetic_and_quote_do_not_mask(self):
        text = _read_install_sh()
        self.test_production_paths_reject_unquoted_and_dynamic()
        lt = chr(60)
        dq = chr(34)
        nl = chr(10)
        arith_cmd = "((" + " x = 1 " + lt + lt + " 2 ))" + nl + "rm -rf $TARGET/rest" + nl
        arith_exp = "$" + "((" + " 1 " + lt + lt + " 2 ))" + nl + "rm -rf $TARGET/rest" + nl
        quoted = (
            "echo "
            + dq
            + "first"
            + nl
            + "second "
            + lt
            + lt
            + "HID"
            + nl
            + "third"
            + dq
            + nl
            + "rm -rf $TARGET/rest"
            + nl
        )
        for name, snippet in (("arith_command", arith_cmd), ("arith_expand", arith_exp), ("multiline_quote", quoted)):
            with self.subTest(case=name):
                mutant = text + nl + snippet
                self.assertNotEqual(mutant, text)
                with unittest.mock.patch("tests.test_install_review._read_install_sh", return_value=mutant):
                    with self.assertRaises(AssertionError):
                        self.test_production_paths_reject_unquoted_and_dynamic()

    def test_arithmetic_command_substitution_heredoc_cannot_hide_deletion(self):
        nl = chr(10)
        lt = chr(60)
        lines = [
            "x=$(( 1 " + lt + lt + " 2 + $(cat " + lt + lt + "HID",
            ")))",
            chr(34) + "quoted" + chr(34),
            lt + lt + "HID",
            "HID",
            ") " + lt + lt + " 1 ))",
            "rm -rf $TARGET/rest",
        ]
        fixture = nl.join(lines) + nl
        masked = _mask_heredoc_bodies(fixture)
        self.assertNotIn(nl + ")))`".replace("`", "") + nl, masked)
        self.assertNotIn(nl + lt + lt + "HID" + nl, masked)
        self.assertIn(") " + lt + lt + " 1 ))", masked)
        self.assertIn("rm -rf $TARGET/rest", masked)
        self.assertEqual(_scan_installer_paths(fixture), (["rm -rf $TARGET/rest"], []))

    @unittest.skipIf(sys.platform == "win32", "runs POSIX shell scripts and host tools")
    def test_completed_arithmetic_heredoc_substitution_preserves_shift_and_deletion(self):
        nl = chr(10)
        lt = chr(60)
        lines = [
            "x=$(( 1 " + lt + lt + " 2 + $(cat " + lt + lt + "HID",
            "2",
            "HID",
            ") " + lt + lt + " 1 ))",
            "rm -rf $TARGET/rest",
        ]
        fixture = nl.join(lines) + nl
        proc = subprocess.run(["/bin/bash", "-n"], input=fixture, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        masked = _mask_heredoc_bodies(fixture)
        self.assertNotIn(nl + "2" + nl, masked)
        self.assertIn(") " + lt + lt + " 1 ))", masked)
        self.assertIn("rm -rf $TARGET/rest", masked)
        self.assertEqual(_scan_installer_paths(fixture), (["rm -rf $TARGET/rest"], []))

    def test_nested_command_substitution_shift_keeps_deletion_visible(self):
        nl = chr(10)
        ds = chr(36)
        lp = chr(40)
        rp = chr(41)
        lt = chr(60)
        tgt = ds + "TARGET/rest"
        first = (
            "v="
            + ds
            + lp
            + lp
            + " "
            + ds
            + lp
            + "echo "
            + ds
            + lp
            + "echo hi"
            + rp
            + rp
            + " "
            + lt
            + lt
            + " 2 "
            + rp
            + rp
        )
        fixture = first + nl + "echo middle" + nl + "rm -rf " + tgt + nl
        self.assertEqual(_mask_heredoc_bodies(fixture), fixture)
        self.assertEqual(_scan_installer_paths(fixture), (["rm -rf " + tgt], []))

    def test_production_paths_misleading_delimiter_inside_substitution(self):
        star = chr(42)
        base = '"$BASE"'
        dollar = chr(36)
        lparen = chr(40)
        rparen = chr(41)
        btick = chr(96)
        dq = chr(34)
        for_line = (
            "for f in " + dollar + lparen + "rm -rf " + base + "/" + star + "; echo do" + rparen + "; do echo ok; done"
        )
        self.assertTrue(
            _line_path_violations(for_line),
            "misleading do inside substitution must not discard deletion: %r" % for_line,
        )
        case_line = (
            "case "
            + dollar
            + lparen
            + "rm -rf "
            + base
            + "/"
            + star
            + "; echo in"
            + rparen
            + " in fast) echo ok;; esac"
        )
        self.assertTrue(
            _line_path_violations(case_line),
            "misleading in inside substitution must not discard deletion: %r" % case_line,
        )
        tick_line = "for f in " + btick + "rm -rf " + base + "/" + star + "; echo do" + btick + "; do echo ok; done"
        self.assertTrue(
            _line_path_violations(tick_line), "misleading do inside backticks must not discard deletion: %r" % tick_line
        )
        quoted_line = (
            "for f in "
            + dollar
            + lparen
            + "rm -rf "
            + base
            + "/"
            + star
            + "; echo "
            + dq
            + "do"
            + dq
            + rparen
            + "; do echo ok; done"
        )
        self.assertTrue(
            _line_path_violations(quoted_line), "quoted misleading do must not discard deletion: %r" % quoted_line
        )

    def test_production_paths_reject_case_in_command_substitution(self):
        star = chr(42)
        base = '"$BASE"'
        dollar = chr(36)
        lparen = chr(40)
        rparen = chr(41)
        case_kw = "".join(["ca", "se"])
        destructive = (
            "for f in "
            + dollar
            + lparen
            + case_kw
            + " x in x"
            + rparen
            + " rm -rf "
            + base
            + "/"
            + star
            + "; echo do;; esac"
            + rparen
            + "; do echo ok; done"
        )
        minimal = (
            "for f in "
            + dollar
            + lparen
            + case_kw
            + " x in x"
            + rparen
            + " echo do;; esac"
            + rparen
            + "; do echo ok; done"
        )
        snippets = (("destructive", destructive), ("minimal", minimal))
        for label, snippet in snippets:
            with self.subTest(snippet=label):
                with self.assertRaisesRegex(AssertionError, "unsupported case in command substitution"):
                    _assert_snippet_confined(snippet)

    def test_strip_shell_comment_keeps_command_after_backtick_comment(self):
        bt = chr(96)
        h = chr(35)
        star = chr(42)
        line = "x=" + bt + "echo a " + h + "b" + bt + "; rm -rf " + "/tmp/opos-t/" + star
        self.assertIn("rm -rf", _strip_shell_comment(line))
        self.assertTrue(_line_path_violations(line))
        with self.assertRaises(AssertionError):
            _assert_snippet_confined(line)
        self.assertEqual(_strip_shell_comment("echo ok # trailing"), "echo ok ")


if __name__ == "__main__":
    unittest.main()
