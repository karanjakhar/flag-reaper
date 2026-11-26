"""Rewrite source so a stale flag's check disappears and only the live branch stays.

Handles:
  * Python  `if [not] flags.is_feature_enabled("k"):` ... [elif ...] [else: ...]
  * C-like  `if ([!]flagsmith.hasFeature("k")) { ... } [else if (...) {...}] [else { ... }]`
  * any other call site -> replaced by a literal (`true`/`True`, or the flag value)
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from .analyze import call_pattern, iter_code_files

# --------------------------------------------------------------------------- helpers


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _dedent(lines: list[str], n: int) -> list[str]:
    out = []
    for ln in lines:
        k = min(n, _indent(ln))
        out.append(ln[k:])
    return out


def _literal(value, enabled: bool, kind: str, lang: str) -> str:
    if kind == "bool":
        if lang == "py":
            return "True" if enabled else "False"
        return "true" if enabled else "false"
    if lang == "py":
        return repr(value)
    return json.dumps(value)


# --------------------------------------------------------------------------- python


def _py_block_end(lines: list[str], start: int, base: int) -> int:
    """Index after the indented block that begins at `start`."""
    j = start
    last = start - 1
    while j < len(lines):
        ln = lines[j]
        if ln.strip() == "":
            j += 1
            continue
        if _indent(ln) <= base:
            break
        last = j
        j += 1
    return last + 1


def _rewrite_python(text: str, key: str, enabled: bool) -> str:
    call = call_pattern(key, "bool").pattern
    head = re.compile(r"^(\s*)if\s+(not\s+)?\(?\s*" + call + r"\s*\)?\s*:\s*(#.*)?$")
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = head.match(lines[i])
        if not m:
            i += 1
            continue
        base = _indent(lines[i])
        keep_if = enabled != bool(m.group(2))
        if_end = _py_block_end(lines, i + 1, base)
        branches = [("if", i, if_end)]
        j = if_end
        while j < len(lines):
            ln = lines[j]
            if ln.strip() == "":
                j += 1
                continue
            if _indent(ln) == base and re.match(r"\s*elif\b", ln):
                e = _py_block_end(lines, j + 1, base)
                branches.append(("elif", j, e))
                j = e
                continue
            if _indent(ln) == base and re.match(r"\s*else\s*:\s*(#.*)?$", ln):
                e = _py_block_end(lines, j + 1, base)
                branches.append(("else", j, e))
                j = e
            break
        chain_end = branches[-1][2]

        if keep_if:
            body = lines[i + 1:if_end]
            new = _dedent(body, _indent(next(b for b in body if b.strip())) - base) if body else []
        elif len(branches) > 1 and branches[1][0] == "elif":
            # drop the if-block, promote elif -> if
            _, s, _ = branches[1]
            promoted = re.sub(r"^(\s*)elif\b", r"\1if", lines[s], count=1)
            new = [promoted] + lines[s + 1:chain_end]
        elif len(branches) > 1 and branches[1][0] == "else":
            _, s, e = branches[1]
            body = lines[s + 1:e]
            new = _dedent(body, _indent(next(b for b in body if b.strip())) - base)
        else:
            new = []

        if not new:
            # keep the enclosing block syntactically valid
            prev = next((lines[k] for k in range(i - 1, -1, -1) if lines[k].strip()), "")
            nxt = next((lines[k] for k in range(chain_end, len(lines)) if lines[k].strip()), "")
            if prev.rstrip().endswith(":") and _indent(nxt) <= _indent(prev):
                new = [" " * base + "pass"]
        lines[i:chain_end] = new
        i += max(len(new), 1) if new else 0
    return "\n".join(lines)


# --------------------------------------------------------------------------- c-like


def _match(text: str, pos: int, open_ch: str, close_ch: str) -> int:
    """Given text[pos] == open_ch, return index of the matching close_ch (skips strings/comments)."""
    depth, i, n = 0, pos, len(text)
    while i < n:
        c = text[i]
        if c in "\"'`":
            q, i = c, i + 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
            continue
        elif text.startswith("/*", i):
            i = text.find("*/", i)
            i = n if i < 0 else i + 1
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced braces")


def _reindent_body(body: str, base_ws: str) -> str:
    lines = body.strip("\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if not any(l.strip() for l in lines):
        return ""
    cut = min(_indent(l) for l in lines if l.strip())
    return "\n".join(base_ws + l[cut:] if l.strip() else "" for l in lines)


def _rewrite_clike(text: str, key: str, enabled: bool) -> str:
    call = call_pattern(key, "bool").pattern
    head = re.compile(r"if\s*\(?\s*(!\s*)?" + call + r"\s*\)?\s*\{")
    pos = 0
    while True:
        m = head.search(text, pos)
        if not m:
            return text
        keep_if = enabled != bool(m.group(1))
        line_start = text.rfind("\n", 0, m.start()) + 1
        prefix = text[line_start:m.start()]
        own_line = prefix.strip() == ""
        base_ws = prefix if own_line else ""
        if_open = m.end() - 1
        if_close = _match(text, if_open, "{", "}")
        if_body = text[if_open + 1:if_close]

        # walk else-if / else chain
        cur = if_close + 1
        else_body, rest_start = None, None
        ei = re.compile(r"\s*else\s+if\s*\(")
        el = re.compile(r"\s*else\s*\{")
        first_else_if = None
        while True:
            mm = ei.match(text, cur)
            if mm:
                first_else_if = first_else_if or mm
                p_close = _match(text, mm.end() - 1, "(", ")")
                b_open = text.index("{", p_close)
                cur = _match(text, b_open, "{", "}") + 1
                continue
            mm = el.match(text, cur)
            if mm:
                if first_else_if is None:
                    b_close = _match(text, mm.end() - 1, "{", "}")
                    else_body = text[mm.end():b_close]
                    cur = b_close + 1
                else:
                    cur = _match(text, mm.end() - 1, "{", "}") + 1
            break
        chain_end = cur

        if keep_if:
            repl = _reindent_body(if_body, base_ws)
        elif first_else_if is not None:
            rest = text[first_else_if.end() - 1:chain_end]  # "(cond) {...} else ..."
            repl = (base_ws if own_line else "") + "if " + rest.lstrip()
            # keep original text verbatim for the remaining chain
            start = line_start if own_line else m.start()
            text = text[:start] + repl + text[chain_end:]
            pos = start + len(repl)
            continue
        elif else_body is not None:
            repl = _reindent_body(else_body, base_ws)
        else:
            repl = ""

        start = line_start if own_line else m.start()
        end = chain_end
        if not repl and own_line:
            # drop the whole statement including its trailing newline
            if end < len(text) and text[end] == "\n":
                end += 1
            text = text[:start] + text[end:]
            pos = start
        else:
            text = text[:start] + repl + text[end:]
            pos = start + len(repl)


# --------------------------------------------------------------------------- public


def rewrite_text(text: str, lang: str, key: str, enabled: bool, value=None) -> str:
    if lang == "py":
        text = _rewrite_python(text, key, enabled)
    else:
        text = _rewrite_clike(text, key, enabled)
    text = call_pattern(key, "bool").sub(_literal(value, enabled, "bool", lang), text)
    text = call_pattern(key, "value").sub(lambda _: _literal(value, enabled, "value", lang), text)
    return _fold_ternaries(text, lang)


_ATOM = r"(\"[^\"\n]*\"|'[^'\n]*'|`[^`\n]*`|-?\d+(?:\.\d+)?|[A-Za-z_$][\w$.]*(?:\([^()\n]*\))?)"


def _fold_ternaries(text: str, lang: str) -> str:
    """`true ? a : b` -> `a`, `X if False else Y` -> `Y` (simple operands only)."""
    if lang == "py":
        pat = re.compile(_ATOM + r"\s+if\s+(not\s+)?(True|False)\s+else\s+" + _ATOM)
        return pat.sub(lambda m: m.group(1) if (m.group(3) == "True") != bool(m.group(2)) else m.group(4), text)
    pat = re.compile(r"(!\s*)?\b(true|false)\s*\?\s*" + _ATOM + r"\s*:\s*" + _ATOM)

    def sub(m):
        truthy = (m.group(2) == "true") != bool(m.group(1))
        return m.group(3) if truthy else m.group(4)
    text = pat.sub(sub, text)
    # `=> ("new")` -> `=> "new"`
    return re.sub(r"(=>|[^=!<>]=|\breturn)\s*\(\s*" + _ATOM + r"\s*\)(?=\s*[;,\n])", r"\1 \2", text)


def plan_rewrite(root: Path, key: str, enabled: bool, value=None) -> dict[Path, tuple[str, str]]:
    """Return {path: (old, new)} for every file the rewrite would change."""
    changes = {}
    pat = call_pattern(key)
    for path in iter_code_files(root):
        old = path.read_text(errors="ignore")
        if not pat.search(old):
            continue
        lang = "py" if path.suffix == ".py" else "c"
        new = rewrite_text(old, lang, key, enabled, value)
        if new != old:
            changes[path] = (old, new)
    return changes


def unified(root: Path, changes: dict[Path, tuple[str, str]]) -> str:
    out = []
    for path, (old, new) in changes.items():
        rel = path.relative_to(root).as_posix()
        out.extend(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}",
        ))
    return "".join(out)
