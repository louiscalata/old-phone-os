// SPDX-License-Identifier: GPL-3.0-or-later
// Decimal arithmetic with scaled integers, so 0.1 + 0.2 is 0.3. No eval.
(function (root) {
  var SCALE = 100000000n;
  var PREC = { "+": 1, "-": 1, "*": 2, "/": 2 };

  function parseNumber(token) {
    var neg = token[0] === "-";
    var body = neg ? token.slice(1) : token;
    var bits = body.split(".");
    if (bits.length > 2 || bits[0] === "" && bits.length === 1) throw new Error("bad number");
    var whole = bits[0] || "0";
    var frac = (bits[1] || "").slice(0, 8);
    if (!/^\d+$/.test(whole) || (frac && !/^\d+$/.test(frac))) throw new Error("bad number");
    while (frac.length < 8) frac += "0";
    var value = BigInt(whole) * SCALE + BigInt(frac);
    return neg ? -value : value;
  }

  function format(value) {
    var neg = value < 0n;
    var abs = neg ? -value : value;
    var whole = abs / SCALE;
    var frac = (abs % SCALE).toString().padStart(8, "0").replace(/0+$/, "");
    return (neg ? "-" : "") + whole.toString() + (frac ? "." + frac : "");
  }

  function tokenize(input) {
    var out = [], i = 0, expect = true;
    var text = String(input).replace(/\s+/g, "");
    while (i < text.length) {
      var ch = text[i];
      if (ch >= "0" && ch <= "9" || ch === ".") {
        var start = i;
        while (i < text.length && (text[i] >= "0" && text[i] <= "9" || text[i] === ".")) i++;
        out.push(text.slice(start, i));
        expect = false;
        continue;
      }
      if (ch === "-" && expect) {
        i++;
        var start2 = i;
        while (i < text.length && (text[i] >= "0" && text[i] <= "9" || text[i] === ".")) i++;
        if (start2 === i) throw new Error("bad expression");
        out.push("-" + text.slice(start2, i));
        expect = false;
        continue;
      }
      if ("+-*/()".indexOf(ch) < 0) throw new Error("bad expression");
      out.push(ch);
      expect = ch !== ")";
      i++;
    }
    return out;
  }

  function apply(op, b, a) {
    if (op === "+") return a + b;
    if (op === "-") return a - b;
    if (op === "*") return (a * b) / SCALE;
    if (b === 0n) throw new Error("Cannot divide by zero");
    return (a * SCALE) / b;
  }

  function evaluate(input) {
    var tokens = tokenize(input);
    var values = [], ops = [];
    function run() {
      var op = ops.pop();
      values.push(apply(op, values.pop(), values.pop()));
    }
    tokens.forEach(function (token) {
      if (token !== "+" && token !== "-" && token !== "*" && token !== "/" && token !== "(" && token !== ")") {
        values.push(parseNumber(token));
        return;
      }
      if (token === "(") { ops.push(token); return; }
      if (token === ")") {
        while (ops.length && ops[ops.length - 1] !== "(") run();
        if (!ops.length) throw new Error("bad expression");
        ops.pop();
        return;
      }
      while (ops.length && ops[ops.length - 1] !== "(" && PREC[ops[ops.length - 1]] >= PREC[token]) run();
      ops.push(token);
    });
    while (ops.length) {
      if (ops[ops.length - 1] === "(") throw new Error("bad expression");
      run();
    }
    if (values.length !== 1) throw new Error("bad expression");
    return format(values[0]);
  }

  root.oposCalc = { evaluate: evaluate };
})(typeof globalThis !== "undefined" ? globalThis : this);
