"""Render demo/transcript.json into demo/flag-reaper-demo.mp4 (Pillow + ffmpeg, no audio).

usage: python3 demo/render.py
"""
import json
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
W, H, FPS = 1280, 720, 30
FONT_DIR = "/usr/share/fonts/truetype/dejavu/"
MONO = ImageFont.truetype(FONT_DIR + "DejaVuSansMono.ttf", 15)
MONO_B = ImageFont.truetype(FONT_DIR + "DejaVuSansMono-Bold.ttf", 15)
SANS = lambda s, b=False: ImageFont.truetype(FONT_DIR + ("DejaVuSans-Bold.ttf" if b else "DejaVuSans.ttf"), s)

BG, TERM, BAR = (11, 12, 22), (20, 22, 36), (32, 34, 52)
FG, DIM = (222, 226, 240), (120, 126, 150)
GREEN, RED, YELLOW, CYAN, PURPLE = (110, 220, 140), (240, 110, 110), (245, 200, 90), (110, 200, 240), (150, 120, 255)

TX, TY, TW, TH = 40, 64, 1200, 552          # terminal window
LH = 20                                      # line height
CW = int(MONO.getlength("M"))
COLS = (TW - 40) // CW
ROWS = (TH - 56) // LH


def color_for(line: str):
    s = line.lstrip()
    if line.startswith("$ "):
        return GREEN, True
    if line.startswith("+++") or line.startswith("---"):
        return FG, True
    if line.startswith("+"):
        return GREEN, False
    if line.startswith("-") and not line.startswith("--\n") and line != "--":
        return RED, False
    if line.startswith("@@"):
        return CYAN, False
    if line.startswith("?"):
        return YELLOW, True
    if "safety stop" in line:
        return RED, True
    if s.startswith(("PR:", "wrote", "committed", "[demo] archived", "url:")):
        return GREEN, False
    if "stale_on" in line or "stale_off" in line or "orphan" in line:
        return YELLOW if "stale_off" in line else (GREEN if "stale_on" in line else RED), False
    if " active " in line or line.endswith(": active"):
        return DIM, False
    return FG, False


def wrap(text: str):
    out = []
    for ln in text.split("\n"):
        out.extend(textwrap.wrap(ln, COLS, drop_whitespace=False, replace_whitespace=False) or [""])
    return out


def frame_term(lines, caption, cursor=False, step_label=""):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((TX, 22), "Flag Reaper", font=SANS(20, True), fill=FG)
    d.text((TX + 150, 26), "flag debt cleanup for Flagsmith · human approves every write", font=SANS(15), fill=DIM)
    if step_label:
        d.text((W - TX - d.textlength(step_label, font=SANS(15)), 26), step_label, font=SANS(15), fill=DIM)
    d.rounded_rectangle((TX, TY, TX + TW, TY + TH), 12, fill=TERM)
    d.rounded_rectangle((TX, TY, TX + TW, TY + 30), 12, fill=BAR)
    d.rectangle((TX, TY + 18, TX + TW, TY + 30), fill=BAR)
    for i, c in enumerate((RED, YELLOW, GREEN)):
        d.ellipse((TX + 14 + i * 20, TY + 9, TX + 26 + i * 20, TY + 21), fill=c)
    d.text((TX + TW / 2 - 70, TY + 7), "~/demo-shop — zsh", font=SANS(13), fill=DIM)
    vis = lines[-ROWS:]
    y = TY + 42
    for ln in vis:
        col, bold = color_for(ln)
        if ln.startswith("$ "):
            d.text((TX + 20, y), "$", font=MONO_B, fill=PURPLE)
            d.text((TX + 20 + 2 * CW, y), ln[2:], font=MONO_B, fill=FG)
        else:
            d.text((TX + 20, y), ln, font=MONO_B if bold else MONO, fill=col)
        y += LH
    if cursor:
        last = vis[-1] if vis else ""
        cx = TX + 20 + len(last) * CW
        d.rectangle((cx, y - LH + 2, cx + CW - 2, y - 3), fill=FG)
    # caption
    d.rounded_rectangle((TX, 636, TX + TW, 700), 10, fill=(34, 28, 70))
    d.rectangle((TX, 636, TX + 6, 700), fill=PURPLE)
    cap = caption
    f = SANS(21, True)
    while d.textlength(cap, font=f) > TW - 50 and f.size > 15:
        f = SANS(f.size - 1, True)
    d.text((TX + 24, 668), cap, font=f, fill=FG, anchor="lm")
    return img


def card(title, lines, foot="", title_size=56):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 8), fill=PURPLE)
    y = 150 if len(lines) < 5 else 110
    d.text((90, y), title, font=SANS(title_size, True), fill=FG)
    y += title_size + 40
    for ln in lines:
        if isinstance(ln, tuple):
            txt, col, size, bold = ln
        else:
            txt, col, size, bold = ln, FG, 26, False
        d.text((90, y), txt, font=SANS(size, bold), fill=col)
        y += int(size * 1.7)
    if foot:
        d.text((90, H - 70), foot, font=SANS(18), fill=DIM)
    return img


class Video:
    def __init__(self, path):
        self.p = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
             "-movflags", "+faststart", str(path)], stdin=subprocess.PIPE)
        self.frames = 0

    def hold(self, img, seconds):
        b = img.tobytes()
        n = max(1, int(seconds * FPS))
        for _ in range(n):
            self.p.stdin.write(b)
        self.frames += n

    def fade(self, a, b, seconds=0.4):
        n = int(seconds * FPS)
        for i in range(1, n + 1):
            self.p.stdin.write(Image.blend(a, b, i / n).tobytes())
        self.frames += n

    def close(self):
        self.p.stdin.close()
        self.p.wait()


def play_step(v, step, idx, total, prev_img):
    caption, cmd, out = step["caption"], step["cmd"], step["out"]
    label = f"step {idx}/{total}"
    lines = ["$ "]
    first = frame_term(lines, caption, True, label)
    v.fade(prev_img, first)
    v.hold(first, 1.0)
    # type the command
    for i in range(1, len(cmd) + 1):
        lines[-1] = "$ " + cmd[:i]
        v.hold(frame_term(lines, caption, True, label), 1 / 28)
    v.hold(frame_term(lines, caption, True, label), 0.4)
    # stream output; pause on approval prompts and type the answer
    out_lines = wrap(out)
    for ln in out_lines:
        if ln.startswith("?") and ln.rstrip().endswith("y") and "[y/N]" in ln:
            q = ln[: ln.rindex("[y/N]") + 6]
            lines.append(q)
            v.hold(frame_term(lines, caption, True, label), 1.6)
            lines[-1] = ln
            v.hold(frame_term(lines, caption, True, label), 0.5)
            continue
        lines.append(ln)
        v.hold(frame_term(lines, caption, False, label), 0.07)
    end = frame_term(lines, caption, False, label)
    v.hold(end, 3.0 + min(len(out_lines), ROWS) * 0.12)
    return end


def main():
    steps = json.loads((HERE / "transcript.json").read_text())
    out = HERE / "flag-reaper-demo.mp4"
    v = Video(out)

    title = card("Flag Reaper 🪓".replace(" 🪓", ""), [
        ("Flagsmith flag debt, cleaned up by an agent.", FG, 30, False),
        ("It finds dead flags, removes their code, opens the PR, archives the flag.", DIM, 24, False),
        ("A human approves every write.", PURPLE, 28, True),
    ], "Toggle Wars · Flagsmith hackathon · Track 02 AI Agents & Feature Flags · Track 04 Agent Skills", 72)
    black = Image.new("RGB", (W, H), BG)
    v.fade(black, title, 0.6)
    v.hold(title, 5)

    problem = card("The problem", [
        "Every rollout and every A/B test leaves a flag behind.",
        "The winner ships to 100%, but the flag check and the losing branch stay in the code.",
        "Nobody knows which flags are safe to delete, so nobody deletes them.",
        ("Result: dead code, more paths to test, a Flagsmith dashboard nobody trusts.", RED, 26, False),
    ])
    v.fade(title, problem)
    v.hold(problem, 8)

    how = card("How Flagsmith fits", [
        ("Flagsmith Admin API → every flag, its state in each environment,", FG, 25, False),
        ("segment & identity overrides, multivariate options, last change", FG, 25, False),
        ("Code scan → where each flag is called (Python, JS/TS, Go, Java…)", FG, 25, False),
        ("Verdict: stale_on · stale_off · orphan · active (never touched)", YELLOW, 25, True),
        ("Code PR first, flag archived in Flagsmith last, only after merge", GREEN, 25, False),
    ], "This run uses an offline snapshot of the Flagsmith project (--demo). Live mode calls the Admin API, Cloud or self-hosted.")
    v.fade(problem, how)
    v.hold(how, 9)

    prev = how
    for i, s in enumerate(steps, 1):
        prev = play_step(v, s, i, len(steps), prev)

    end = card("Flag Reaper", [
        ("github.com/karanjakhar/flag-reaper", PURPLE, 30, True),
        ("pip install git+https://github.com/karanjakhar/flag-reaper", FG, 22, False),
        ("Agent Skill included: skill/SKILL.md, compact JSON, approval at every write", FG, 22, False),
        ("Zero dependencies · 20 tests · MIT · Flagsmith Cloud & self-hosted", DIM, 22, False),
        ("Demo PR: github.com/karanjakhar/flag-reaper-demo-shop/pull/1", DIM, 22, False),
    ], "Built for Toggle Wars, the Flagsmith Experimentation Hackathon", 64)
    v.fade(prev, end, 0.6)
    v.hold(end, 7)
    v.close()
    print(f"{out}  {v.frames / FPS:.1f}s")


if __name__ == "__main__":
    main()
