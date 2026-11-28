"""Render demo/agent-run.json (a recorded Claude Code session using the Agent Skill) into
the agent section of demo/flag-reaper-demo.mp4. Used by demo/render.py.
"""
import re
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw

import render
from render import (BG, BAR, CYAN, DIM, FG, GREEN, H, LH, MONO, MONO_B, PURPLE, RED, SANS, TERM,
                    TH, TW, TX, TY, W, YELLOW)

HERE = Path(__file__).resolve().parent
COLS = render.COLS
ROWS = render.ROWS
MAX_RESULT_LINES = 40

CAPTIONS = [
    "You ask in chat. The agent loads the skill and runs one compact scan",
    "You pick the flag. The agent previews the diff and asks before writing anything",
    "You approve. It applies the change, removes a dead line the rewriter missed, opens the PR",
]


def clean_md(s):
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    return s.replace("`", "")


def wrap(text, indent=""):
    out = []
    for ln in text.split("\n"):
        out.extend(textwrap.wrap(ln, COLS - len(indent), drop_whitespace=False, replace_whitespace=False,
                                 subsequent_indent="  " if ln.lstrip().startswith(("-", "|")) else "") or [""])
    return [indent + o for o in out]


def result_style(ln):
    s = ln[5:]  # every result line starts with a 5-character gutter
    if s.startswith(("+++", "---")):
        return FG, True
    if s.startswith("--"):  # a CLI note like "--dry-run: nothing written.", not a diff line
        return DIM, False
    if s.startswith("+"):
        return GREEN, False
    if s.startswith("-"):
        return RED, False
    if s.startswith("@@"):
        return CYAN, False
    if s.startswith(("https://", "wrote", "auto-approved")):
        return GREEN, False
    return DIM, False


def event_lines(ev):
    """-> list of (text, colour, bold)."""
    if ev["kind"] == "tool":
        label = {"Bash": "Bash", "Skill": "Skill", "Edit": "Edit"}.get(ev["tool"], ev["tool"])
        arg = ev["arg"].split("\n")[0]
        if ev["tool"] == "Bash" and "gh pr create" in arg:
            arg = arg[: arg.index("gh pr create") + len("gh pr create")] + " …"
        lines = wrap(f"● {label}({arg})")
        return [(l, CYAN, True) for l in lines]
    if ev["kind"] == "result":
        body = ev["text"].rstrip("\n").split("\n")
        if ev["error"]:
            body = ["blocked: sed needs approval to change a file (the agent used the Edit tool instead)"]
        if len(body) > MAX_RESULT_LINES:
            body = body[:MAX_RESULT_LINES] + [f"… {len(body) - MAX_RESULT_LINES} more lines"]
        out = []
        for i, b in enumerate(body):
            for w in wrap(b, "  └  " if i == 0 else "     "):
                col, bold = (YELLOW, False) if ev["error"] else result_style(w)
                out.append((w, col, bold))
        return out
    # assistant text
    out = []
    for i, w in enumerate(wrap(clean_md(ev["text"]))):
        out.append((("● " if i == 0 else "  ") + w, FG, False))
    return out


def frame(lines, caption, label, cursor=False):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((TX, 22), "Flag Reaper", font=SANS(20, True), fill=FG)
    d.text((TX + 150, 26), "Agent Skill in Claude Code · you approve every write in chat", font=SANS(15), fill=DIM)
    d.text((W - TX - d.textlength(label, font=SANS(15)), 26), label, font=SANS(15), fill=DIM)
    d.rounded_rectangle((TX, TY, TX + TW, TY + TH), 12, fill=TERM)
    d.rounded_rectangle((TX, TY, TX + TW, TY + 30), 12, fill=BAR)
    d.rectangle((TX, TY + 18, TX + TW, TY + 30), fill=BAR)
    for i, c in enumerate((RED, YELLOW, GREEN)):
        d.ellipse((TX + 14 + i * 20, TY + 9, TX + 26 + i * 20, TY + 21), fill=c)
    d.text((TX + TW / 2 - 70, TY + 7), "claude — ~/demo-shop", font=SANS(13), fill=DIM)
    y = TY + 42
    vis = lines[-ROWS:]
    for txt, col, bold in vis:
        d.text((TX + 20, y), txt, font=MONO_B if bold else MONO, fill=col)
        y += LH
    if cursor and vis:
        cx = TX + 20 + MONO.getlength(vis[-1][0])
        d.rectangle((cx + 2, y - LH + 2, cx + 10, y - 3), fill=FG)
    d.rounded_rectangle((TX, 636, TX + TW, 700), 10, fill=(34, 28, 70))
    d.rectangle((TX, 636, TX + 6, 700), fill=PURPLE)
    f = SANS(21, True)
    while d.textlength(caption, font=f) > TW - 50 and f.size > 15:
        f = SANS(f.size - 1, True)
    d.text((TX + 24, 668), caption, font=f, fill=FG, anchor="lm")
    return img


def play_agent(v, run, prev, narr=None):
    """Play the recorded agent session into Video `v`, one narration line per turn. Returns the last frame."""
    lines = []
    total = len(run["turns"])
    for i, turn in enumerate(run["turns"]):
        cap, label = CAPTIONS[i], f"agent · turn {i + 1}/{total}"
        if lines:
            lines.append(("", FG, False))
        prompt = wrap("> " + turn["user"])
        first = frame(lines + [(prompt[0][:2], PURPLE, True)], cap, label, True)
        v.fade(prev, first, 0.3)
        t_end = v.say(narr[i]) if narr else 0
        v.hold(first, 0.4)
        for pl in prompt:  # type the user's message
            for k in range(4, len(pl) + 4, 4):
                v.hold(frame(lines + [(pl[:k], PURPLE, True)], cap, label, True), 1 / 30)
            lines.append((pl, PURPLE, True))
        v.hold(frame(lines, cap, label), 0.4)
        for ev in turn["events"]:
            lines.append(("", FG, False))
            new = event_lines(ev)
            step = 0.03 if ev["kind"] == "result" else 0.06
            for ln in new:
                lines.append(ln)
                v.hold(frame(lines, cap, label), step)
            v.hold(frame(lines, cap, label), {"tool": 0.3, "result": 0.5, "text": 0.5}[ev["kind"]])
        tu = turn["usage"]
        lines.append((f"  · turn {i + 1}: {tu['output_tokens']:,} output tokens, "
                      f"{tu['input_tokens'] + tu['cache_creation_input_tokens']:,} new input, "
                      f"{tu['cache_read_input_tokens']:,} cached", DIM, False))
        prev = frame(lines, cap, label)
        v.hold_until(prev, t_end + 0.6, 2.5)  # let the answer sit on screen while the line finishes
    return prev


def model_name(run):
    return run["model"].replace("claude-sonnet-", "Claude Sonnet ").replace("-", ".")
