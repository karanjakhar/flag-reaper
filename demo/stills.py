"""Render README images from demo/transcript.json (the recorded live session).

Produces terminal screenshots sized to their content and an animated GIF of the
`close` -> `reap` loop.

usage: python3 demo/stills.py          # writes docs/img/*.png and docs/img/*.gif
"""
import json
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "docs" / "img"
sys.path.insert(0, str(HERE))

import render  # noqa: E402  (colours, fonts and the title card come from the video renderer)

FONT_DIR = render.FONT_DIR
SIZE = 17
MONO = ImageFont.truetype(FONT_DIR + "DejaVuSansMono.ttf", SIZE)
MONO_B = ImageFont.truetype(FONT_DIR + "DejaVuSansMono-Bold.ttf", SIZE)
SANS = render.SANS
CW = MONO.getlength("M")
LH = 24
COLS = 104
PAD = 22
BAR_H = 34
W = PAD * 2 + int(COLS * CW + 0.5) + 4


def wrap(text):
    out = []
    for ln in text.split("\n"):
        out.extend(textwrap.wrap(ln, COLS, drop_whitespace=False, replace_whitespace=False) or [""])
    return out


def terminal(lines, title="~/demo-shop", rows=None, cursor=False):
    """Draw a macOS-style terminal window holding `lines` (commands start with '$ ')."""
    rows = rows or len(lines)
    h = BAR_H + PAD + rows * LH + PAD
    img = Image.new("RGB", (W, h), render.TERM)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, BAR_H), fill=render.BAR)
    for i, c in enumerate((render.RED, render.YELLOW, render.GREEN)):
        d.ellipse((16 + i * 22, 11, 29 + i * 22, 24), fill=c)
    tf = SANS(14)
    d.text(((W - d.textlength(title, font=tf)) / 2, 9), title, font=tf, fill=render.DIM)
    y = BAR_H + PAD
    for ln in lines[-rows:]:
        col, bold = render.color_for(ln)
        if ln.startswith("$ "):
            d.text((PAD, y), "$", font=MONO_B, fill=render.PURPLE)
            d.text((PAD + 2 * CW, y), ln[2:], font=MONO_B, fill=render.FG)
        else:
            d.text((PAD, y), ln, font=MONO_B if bold else MONO, fill=col)
        y += LH
    if cursor and lines:
        cx = PAD + len(lines[-1]) * CW
        d.rectangle((cx, y - LH + 3, cx + CW - 2, y - 4), fill=render.FG)
    return img


def session(steps):
    """All the lines for a run of transcript steps, one blank line between commands."""
    lines = []
    for s in steps:
        if lines:
            lines.append("")
        lines.append("$ " + s["cmd"])
        lines.extend(wrap(s["out"].rstrip("\n")))
    return lines


def save_png(img, name):
    img.save(OUT / name, optimize=True)
    print(f"{OUT / name}  {img.size[0]}x{img.size[1]}  {(OUT / name).stat().st_size // 1024} KB")


def gif(steps, name, rows):
    """Type each command, stream its output, pause on each [y/N] prompt, hold the end."""
    frames, durations = [], []

    def snap(lines, ms, cursor=False):
        frames.append(terminal(lines, rows=rows, cursor=cursor))
        durations.append(ms)

    lines = []
    for s in steps:
        if lines:
            lines.append("")
        lines.append("$ ")
        snap(lines, 600, True)
        cmd = s["cmd"]
        for i in range(4, len(cmd) + 4, 4):
            lines[-1] = "$ " + cmd[:i]
            snap(lines, 60, True)
        snap(lines, 500, True)
        for ln in wrap(s["out"].rstrip("\n")):
            if ln.startswith("?") and "[y/N]" in ln:
                lines.append(ln[: ln.rindex("[y/N]") + 6])
                snap(lines, 1400, True)
                lines[-1] = ln
                snap(lines, 400)
            else:
                lines.append(ln)
                snap(lines, 70)
        durations[-1] = 2600
    durations[-1] = 5000
    # one shared palette keeps the file small and the colours stable between frames
    # (built from a strip of every 4th frame so no colour that appears only early on is lost)
    sample = frames[::4] + [frames[-1]]
    swatch = (render.PURPLE, render.GREEN, render.RED, render.YELLOW, render.CYAN, render.FG, render.DIM)
    strip = Image.new("RGB", (W, sum(f.height for f in sample) + 400))
    sd = ImageDraw.Draw(strip)  # big blocks of each theme colour so small glyphs keep their exact colour
    for i, c in enumerate(swatch):
        sd.rectangle((i * W // len(swatch), 0, (i + 1) * W // len(swatch), 400), fill=c)
    y = 400
    for f in sample:
        strip.paste(f, (0, y))
        y += f.height
    pal = strip.quantize(colors=128, method=Image.Quantize.MAXCOVERAGE)
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    q[0].save(OUT / name, save_all=True, append_images=q[1:], duration=durations, loop=0, optimize=True,
              disposal=1)
    print(f"{OUT / name}  {len(q)} frames  {(OUT / name).stat().st_size // 1024} KB")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    t = json.loads((HERE / "transcript.json").read_text())
    scan, close, _rescan, reap, pr, refuse, not_yet, archive, js = t

    save_png(terminal(session([scan])), "scan.png")
    save_png(terminal(session([close])), "close.png")
    save_png(terminal(session([reap])), "reap.png")
    save_png(terminal(session([pr])), "pr.png")
    save_png(terminal(session([refuse, not_yet, archive])), "guardrails.png")
    save_png(terminal(session([js])), "agent-json.png")

    # hero: the whole loop on one experiment, close -> reap
    gif([close, reap], "flag-reaper.gif", rows=26)  # scrolls like a real terminal


if __name__ == "__main__":
    main()
