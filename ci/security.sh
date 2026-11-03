#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
failures=0
run_step() {
	local name="$1"
	shift
	echo "== ${name}"
	if "$@"; then
		echo "PASS: ${name}"
	else
		echo "FAIL: ${name}"
		failures=$((failures + 1))
	fi
}
shellcheck_all() {
	git ls-files -z '*.sh' | xargs -0 shellcheck -x -e "SC1090,SC1091"
}
semgrep_auto() {
	if command -v timeout >/dev/null 2>&1; then
		timeout 120 semgrep --config "auto" --error "src"
	else
		semgrep --config "auto" --error "src"
	fi
}
run_step "tests" env PYTHONPATH="src" python3 -m unittest discover -s "tests" -t "."
mods=()
for m in test_secure test_web_security test_essentials_security test_manifest test_deploy_hardening test_harden_device test_historyscan test_no_unsafe_code; do
	if [ -f "tests/${m}.py" ]; then
		mods+=("tests.${m}")
	fi
done
if [ "${#mods[@]}" -eq 0 ]; then
	echo "== security tests"
	echo "skip: no security test modules"
else
	for i in 1 2 3; do
		run_step "security tests run ${i}" env PYTHONPATH="src" python3 -m unittest "${mods[@]}"
	done
fi
run_step "guard" python3 "guard/check.py"
run_step "history scan" python3 "guard/historyscan.py"
echo "== shellcheck"
if command -v shellcheck >/dev/null 2>&1; then
	run_step "shellcheck" shellcheck_all
else
	echo "skip: shellcheck not installed (optional)"
fi
echo "== ruff"
if command -v ruff >/dev/null 2>&1; then
	run_step "ruff" ruff check --no-cache --select "S" --ignore "S101,S603,S607,S311,S112" "src"
else
	echo "skip: ruff not installed (optional)"
fi
echo "== bandit"
if command -v bandit >/dev/null 2>&1; then
	run_step "bandit" bandit -q -r "src"
else
	echo "skip: bandit not installed (optional)"
fi
echo "== semgrep"
if command -v semgrep >/dev/null 2>&1; then
	run_step "semgrep" semgrep_auto
else
	echo "skip: semgrep not installed (optional)"
fi
if [ "${failures}" -gt 0 ]; then
	echo "security CI: FAIL (${failures} steps)"
	exit 1
fi
echo "security CI: PASS"
