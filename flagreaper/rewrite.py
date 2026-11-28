"""Rewrite source so a retired flag's checks disappear and only the live branch stays.

How it works (one flag at a time):
  1. Every SDK call for the flag is replaced by a private marker:
       hasFeature("k") / is_feature_enabled("k")  ->  ⟦T⟧ or ⟦F⟧
       getValue("k") / get_feature_value("k")      ->  ⟦v:<literal>⟧
  2. Markers are folded: `⟦v:"a"⟧ === "a"` -> ⟦T⟧, `!⟦T⟧` -> ⟦F⟧, `not ⟦F⟧` -> ⟦T⟧.
  3. `if`/`else` statements and ternaries whose condition is a marker keep only the live branch:
       Python  if / elif / else
       C-like  if (...) {...} else if (...) {...} else {...}   (JS/TS, Java, Kotlin, Go, Dart)
  4. Remaining markers become plain literals (`true`, `True`, `"annual_first"`, `50`).

Because folding only ever looks at markers, unrelated code (even an existing `if (true)`) is never touched.
"""
from __future__ import annotations

import ast
import difflib
import json
import re
from pathlib import Path

from .analyze import call_pattern, iter_code_files

T, F = "⟦T⟧", "⟦F⟧"
VAL = re.compile(r"⟦v:(.*?)⟧")
BOOL = r"(?:⟦T⟧|⟦F⟧)"

# --------------------------------------------------------------------------- helpers


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _dedent(lines: list[str], n: int) -> list[str]:
    return [ln[min(n, _indent(ln)):] for ln in lines]


def _lit(value, lang: str) -> str:
    if lang == "py":
        return repr(value)
    return json.dumps(value)


def _parse(lit: str, lang: str):
    try:
        return ast.literal_eval(lit) if lang == "py" else json.loads(lit.replace("'", '"') if lit[:1] == "'" else lit)
    except (ValueError, SyntaxError):
        raise ValueError(lit) from None


_STR_OR_NUM = r"(\"[^\"\n]*\"|'[^'\n]*'|-?\d+(?:\.\d+)?|true|false|True|False|None|null)"


def _fold(text: str, lang: str) -> str:
    """Fold comparisons and negations that involve markers, until nothing changes."""
    def cmp(a, op, b):
        try:
            x, y = _parse(a, lang), _parse(b, lang)
        except ValueError:
            return None
        eq = x == y
        return T if (eq if op in ("==", "===") else not eq) else F

    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"⟦v:(.*?)⟧\s*(===|!==|==|!=)\s*" + _STR_OR_NUM,
                      lambda m: cmp(m.group(1), m.group(2), m.group(3)) or m.group(0), text)
        text = re.sub(_STR_OR_NUM + r"\s*(===|!==|==|!=)\s*⟦v:(.*?)⟧",
                      lambda m: cmp(m.group(3), m.group(2), m.group(1)) or m.group(0), text)
        if lang == "py":
            text = re.sub(r"\bnot\s+⟦T⟧", F, text)
            text = re.sub(r"\bnot\s+⟦F⟧", T, text)
        else:
            text = re.sub(r"!\s*⟦T⟧", F, text)
            text = re.sub(r"!\s*⟦F⟧", T, text)
        text = re.sub(r"\(\s*(" + BOOL + r")\s*\)", r"\1", text)
    return text


# --------------------------------------------------------------------------- python


def _py_block_end(lines: list[str], start: int, base: int) -> int:
    """Index after the indented block that begins at `start`."""
    j, last = start, start - 1
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


def _body(lines: list[str], base: int) -> list[str]:
    first = next((b for b in lines if b.strip()), None)
    return _dedent(lines, _indent(first) - base) if first is not None else []


_PY_EXIT = re.compile(r"(return|raise|continue|break)\b")


def _py_exits(block: list[str], base: int) -> bool:
    """True if the last statement at indent `base` in `block` always leaves the block."""
    top = [ln for ln in block if ln.strip() and _indent(ln) == base]
    return bool(top) and bool(_PY_EXIT.match(top[-1].lstrip()))


def _py_dead_end(lines: list[str], start: int, base: int) -> int:
    """Index after the statements from `start` on that sit in the same block (indent >= base)."""
    end, j = start, start
    while j < len(lines):
        if lines[j].strip():
            if _indent(lines[j]) < base:
                break
            end = j + 1
        j += 1
    return end


def _rewrite_python(text: str) -> str:
    head = re.compile(r"^(\s*)if\s+(" + BOOL + r")\s*:\s*(#.*)?$")
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = head.match(lines[i])
        if not m:
            i += 1
            continue
        base = _indent(lines[i])
        keep_if = m.group(2) == T
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
            new = _body(lines[i + 1:if_end], base)
        elif len(branches) > 1 and branches[1][0] == "elif":
            _, s, _ = branches[1]
            new = [re.sub(r"^(\s*)elif\b", r"\1if", lines[s], count=1)] + lines[s + 1:chain_end]
        elif len(branches) > 1 and branches[1][0] == "else":
            _, s, e = branches[1]
            new = _body(lines[s + 1:e], base)
        else:
            new = []

        if not new:
            # keep the enclosing block syntactically valid
            prev = next((lines[k] for k in range(i - 1, -1, -1) if lines[k].strip()), "")
            nxt = next((lines[k] for k in range(chain_end, len(lines)) if lines[k].strip()), "")
            if prev.rstrip().endswith(":") and _indent(nxt) <= _indent(prev):
                new = [" " * base + "pass"]
        elif base > 0 and _py_exits(new, base):
            # the kept branch always returns/raises, so what followed the `if` in this block
            # can no longer run: `if not flag: return a` + `return b` -> `return a`
            chain_end = _py_dead_end(lines, chain_end, base)
        lines[i:chain_end] = new
        # re-scan the inserted lines: a kept branch may hold another marker `if`
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


_C_EXIT = re.compile(r"(return|throw|break|continue)\b[^{};]*;?\s*(//.*)?$")


def _enclosing_close(text: str, pos: int) -> int | None:
    """Index of the `}` that closes the block containing `pos`, or None (skips strings/comments)."""
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
        elif c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                return i
            depth -= 1
        i += 1
    return None


def _drop_dead_clike(text: str, after: int, repl: str, base_ws: str) -> str:
    """If `repl` (just inserted, ending at `after`) ends in return/throw/break/continue at its own
    top level, remove the statements that follow it in the same block: they can't run any more."""
    top = [l for l in repl.split("\n") if l.strip() and _indent(l) == len(base_ws)]
    if not top or not _C_EXIT.match(top[-1].strip()):
        return text
    close = _enclosing_close(text, after)
    if close is None:
        return text
    dead = text[after:close]
    if not dead.strip() or re.search(r"\bcase\b|\bdefault\s*:", dead):
        return text  # nothing to drop, or a switch: fall-through labels are still reachable
    nl = text.rfind("\n", after, close)
    if nl >= 0 and not text[nl + 1:close].strip():
        return text[:after] + text[nl:]  # keep the closing brace's own line and indent
    return text[:after] + " " + text[close:]


def _rewrite_clike(text: str) -> str:
    head = re.compile(r"if\s*\(?\s*(" + BOOL + r")\s*\)?\s*\{")
    pos = 0
    while True:
        m = head.search(text, pos)
        if not m:
            return text
        keep_if = m.group(1) == T
        line_start = text.rfind("\n", 0, m.start()) + 1
        prefix = text[line_start:m.start()]
        own_line = prefix.strip() == ""
        base_ws = prefix if own_line else ""
        if_open = m.end() - 1
        if_close = _match(text, if_open, "{", "}")
        if_body = text[if_open + 1:if_close]

        cur, else_body, first_else_if = if_close + 1, None, None
        while True:
            mm = re.compile(r"\s*else\s+if\s*\(").match(text, cur)
            if mm:
                first_else_if = first_else_if or mm
                p_close = _match(text, mm.end() - 1, "(", ")")
                cur = _match(text, text.index("{", p_close), "{", "}") + 1
                continue
            mm = re.compile(r"\s*else\s*\{").match(text, cur)
            if mm:
                b_close = _match(text, mm.end() - 1, "{", "}")
                if first_else_if is None:
                    else_body = text[mm.end():b_close]
                cur = b_close + 1
            break
        chain_end = cur
        start = line_start if own_line else m.start()

        if keep_if:
            repl = _reindent_body(if_body, base_ws)
        elif first_else_if is not None:
            repl = base_ws + "if " + text[first_else_if.end() - 1:chain_end].lstrip()
            text = text[:start] + repl + text[chain_end:]
            pos = start + len(base_ws)
            continue
        elif else_body is not None:
            repl = _reindent_body(else_body, base_ws)
        else:
            repl = ""

        end = chain_end
        if not repl and own_line and end < len(text) and text[end] == "\n":
            end += 1
        text = text[:start] + repl + text[end:]
        if repl and own_line:
            text = _drop_dead_clike(text, start + len(repl), repl, base_ws)
        pos = start


# --------------------------------------------------------------------------- ternaries + cleanup

_ATOM = r"(\"[^\"\n]*\"|'[^'\n]*'|`[^`\n]*`|-?\d+(?:\.\d+)?|[A-Za-z_$][\w$.]*(?:\([^()\n]*\))?)"


def _fold_ternaries(text: str, lang: str) -> str:
    """`⟦T⟧ ? a : b` -> `a`, `x if ⟦F⟧ else y` -> `y` (simple operands only)."""
    if lang == "py":
        pat = re.compile(_ATOM + r"\s+if\s+(" + BOOL + r")\s+else\s+" + _ATOM)
        return pat.sub(lambda m: m.group(1) if m.group(2) == T else m.group(3), text)
    pat = re.compile(r"(" + BOOL + r")\s*\?\s*" + _ATOM + r"\s*:\s*" + _ATOM)
    text = pat.sub(lambda m: m.group(2) if m.group(1) == T else m.group(3), text)
    # `=> ("new")` -> `=> "new"`
    return re.sub(r"(=>|[^=!<>]=|\breturn)\s*\(\s*" + _ATOM + r"\s*\)(?=\s*[;,\n])", r"\1 \2", text)


def _unmark(text: str, lang: str) -> str:
    t, f = ("True", "False") if lang == "py" else ("true", "false")
    return VAL.sub(lambda m: m.group(1), text.replace(T, t).replace(F, f))


# --------------------------------------------------------------------------- public


def rewrite_text(text: str, lang: str, key: str, enabled: bool, value=None) -> str:
    text = call_pattern(key, "bool").sub(T if enabled else F, text)
    text = call_pattern(key, "value").sub(lambda _: f"⟦v:{_lit(value, lang)}⟧", text)
    text = _fold(text, lang)
    text = _rewrite_python(text) if lang == "py" else _rewrite_clike(text)
    text = _fold_ternaries(text, lang)
    return _unmark(text, lang)


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
