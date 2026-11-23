#!/usr/bin/env python3
"""@eloquiz quiz reel v2: gameplay with hook -> freeze + animated 2-3 option quiz with countdown
-> (optional payoff: the rest of the clip) -> animated reveal card (Elo counter, staged stats).

Usage: make_quiz_reel.py <clip.mp4> <out.mp4> <freeze_sec|auto> <spec.json>
  auto = cut right before the Allstar outro transition (see clipcut.py)

spec.json
{
  "rubric": "elo" | "clutch" | "legit",          (optional, default elo)
  "start": 7.5,                                  (optional: skip the quiet lead-in, seconds)
  "hook": "GUESS HIS ELO", "hook_sub": "ACE on Cache · watch till the end",
  "question": "GUESS HIS FACEIT ELO",            (optional, default per rubric)
  "options": ["4,400", "5,300", "5,800"],        (2 or 3)
  "answer_index": 1,
  "payoff": {"start": 14.2, "end": 18.0, "title": "WATCH...", "sub": "", "label": "LET'S SEE..."},
            (optional: after the countdown, play clip[start:end] as the answer, then the stats card)
  "reveal": {"answer_line": "...", "big": "5,299 ELO", "count_to": 5299, "count_suffix": " ELO",
             "rank": "...", "who": "...", "round": ["..", ".."], "match": "...", "credit": "..."}
}
All readable content stays inside the centre square y 430-1490 (Instagram web square crop + Reels UI).
"""
import json, random, subprocess, sys, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sfx  # noqa: E402
from clipcut import clean_end, card_end, CARD  # noqa: E402

W, H, FPS = 1080, 1920, 30
def _font(*paths):
    """macOS Arial when present; on the Linux cloud runner the open-licence lookalikes in fonts/."""
    return next((str(p) for p in paths if Path(p).exists()), str(paths[-1]))


_FD = Path(__file__).resolve().parent / "fonts"
BLACK = _font("/System/Library/Fonts/Supplemental/Arial Black.ttf", _FD / "ArchivoBlack-Regular.ttf")
BOLD = _font("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")
ORANGE, BLUE, WHITE, GREY = (255, 120, 30), (154, 209, 255), (255, 255, 255), (200, 200, 200)
GREEN, GREEN_LINE = (34, 170, 92), (140, 255, 175)
HERE = Path(__file__).resolve().parent
WM = Image.open(HERE / "brand" / "eloquiz_watermark.png").convert("RGBA")
QUESTION = {"elo": "GUESS HIS FACEIT ELO", "clutch": "CLUTCH OR CHOKE?", "legit": "LEGIT OR CHEATING?"}
VID_Y = 690


def F(path, size): return ImageFont.truetype(path, size)


def run(*cmd): subprocess.run([str(c) for c in cmd], check=True)


# ---------------------------------------------------------------- sprites
_probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))


def text_sprite(text, path, size, fill, maxw=W - 100):
    while size > 18 and _probe.textlength(text, font=F(path, size)) > maxw:
        size -= 2
    f = F(path, size)
    l, t, r, b = _probe.textbbox((0, 0), text, font=f)
    img = Image.new("RGBA", (r - l + 8, b - t + 8), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((4 - l, 4 - t), text, font=f, fill=fill)
    return img


def option_sprite(letter, text, state):
    w, h = W - 300, 104
    img = Image.new("RGBA", (w + 12, h + 12), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    fill, line = ((GREEN, GREEN_LINE) if state == "correct" else ((22, 22, 22, 235), WHITE))
    d.rounded_rectangle([6, 6, w + 6, h + 6], radius=26, fill=fill, outline=line, width=5)
    d.ellipse([22, 20, 98, 96], fill=WHITE if state == "correct" else ORANGE)
    lf = F(BLACK, 44)
    l, t, r, b = d.textbbox((0, 0), letter, font=lf)
    d.text((60 - (r + l) / 2, 58 - (b + t) / 2), letter, font=lf, fill=(20, 20, 20))
    s = text_sprite(text, BLACK, 54, WHITE, maxw=w - 160)
    img.alpha_composite(s, (int(60 + (w + 12) / 2 - s.width / 2), int((h + 12 - s.height) / 2)))
    return img


def ring_sprite(frac, num_spr, scale):
    s = 190
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    d.ellipse([10, 10, s - 10, s - 10], fill=(0, 0, 0, 120), outline=(90, 90, 90), width=9)
    if frac > 0:
        d.arc([10, 10, s - 10, s - 10], start=-90, end=-90 + 360 * frac, fill=ORANGE, width=11)
    n = num_spr if scale == 1 else num_spr.resize((int(num_spr.width * scale), int(num_spr.height * scale)), Image.BICUBIC)
    img.alpha_composite(n, ((s - n.width) // 2, (s - n.height) // 2))
    return img


def put(frame, spr, cy, alpha=1.0, scale=1.0, dy=0, cx=W / 2):
    """Composite sprite centred at (cx, cy) with opacity/scale/offset."""
    if alpha <= 0.01: return
    s = spr
    if abs(scale - 1) > 0.005:
        s = spr.resize((max(1, int(spr.width * scale)), max(1, int(spr.height * scale))), Image.BICUBIC)
    if alpha < 0.99:
        s = s.copy(); s.putalpha(s.getchannel("A").point(lambda v: int(v * alpha)))
    x, y = int(cx - s.width / 2), int(cy - s.height / 2 + dy)
    frame.alpha_composite(s, (max(0, x), max(0, y)))


def prog(t, start, dur):
    return 0.0 if t <= start else 1.0 if t >= start + dur else (t - start) / dur


def ease(x): return 1 - (1 - x) ** 3


def pop(t, start, dur=0.22, over=0.18):
    """scale that overshoots then settles (1+over -> 1)."""
    p = prog(t, start, dur)
    return 1 + over * (1 - ease(p))


def zoomed(img, z, shake=(0, 0)):
    if z <= 1.001 and shake == (0, 0): return img
    w, h = int(W * z), int(H * z)
    big = img.resize((w, h), Image.BILINEAR)
    x, y = (w - W) // 2 + shake[0], (h - H) // 2 + shake[1]
    return big.crop((x, y, x + W, y + H))


# ---------------------------------------------------------------- video helpers
LAYOUT = ("[0:v]split=2[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
          "boxblur=30:3,eq=brightness=-0.25[bg];[b]scale=1080:-2[fg];[bg][fg]overlay=0:690[v0];[v0][1:v]overlay=0:0[v]")


def gameplay(clip, out, start, end, title, sub, tmp, name):
    band = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(band)
    d.rectangle([0, 430, W, VID_Y], fill=(0, 0, 0, 165))
    put(band, text_sprite(title, BLACK, 92, WHITE), 520)
    if sub: put(band, text_sprite(sub, BOLD, 36, ORANGE), 628)
    band.alpha_composite(WM, ((W - WM.width) // 2, VID_Y + 608 + 30))
    band.save(tmp / f"{name}.png")
    layout = LAYOUT
    hide = card_end(clip) - start                     # Allstar intro card shows the nickname: blur it (no spoilers)
    if hide > 0:
        x, y, w, h = CARD
        layout = LAYOUT.replace("[b]scale=1080:-2[fg]",
            f"[b]split[b1][b2];[b2]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},boxblur=24:4[m];"
            f"[b1][m]overlay=x=W*{x}:y=H*{y}:enable='lt(t,{hide:.2f})'[b3];[b3]scale=1080:-2[fg]")
    run("ffmpeg", "-v", "error", "-y", "-ss", f"{start}", "-i", clip, "-i", tmp / f"{name}.png", "-t", f"{end - start}",
        "-filter_complex", layout, "-map", "[v]", "-map", "0:a?", "-r", "60", "-c:v", "libx264", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", out)


def grab(clip, at, tmp, name):
    run("ffmpeg", "-v", "error", "-y", "-ss", f"{max(at, 0)}", "-i", clip, "-frames:v", "1",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", tmp / f"{name}.png")
    base = Image.open(tmp / f"{name}.png").convert("RGB").resize((W, H))
    blur = base.filter(ImageFilter.GaussianBlur(14))
    mean = sum(blur.convert("L").getdata()) / (W * H)
    k = min(0.33, 32 / max(mean, 1))                      # adaptive: bright maps (Mirage, Dust 2) get darker so text reads
    dark = blur.point(lambda v: int(v * k))
    return base.convert("RGBA"), dark.convert("RGBA")


def render(frames_fn, n, track, out, tmp, name):
    wav = tmp / f"{name}.wav"; track.write(wav)
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(FPS), "-i", "-", "-i", str(wav), "-r", "60", "-c:v", "libx264", "-crf", "18",
                          "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(out)],
                         stdin=subprocess.PIPE)
    for i in range(n):
        p.stdin.write(frames_fn(i / FPS).convert("RGB").tobytes())
    p.stdin.close()
    if p.wait(): raise SystemExit(f"ffmpeg failed on {name}")


# ---------------------------------------------------------------- quiz
def quiz_part(spec, base, dark, out, tmp, payoff):
    opts, ans = spec["options"], spec["answer_index"]
    letters = "ABC"[:len(opts)]
    q_text = spec.get("question") or QUESTION.get(spec.get("rubric", "elo"), QUESTION["elo"])
    s_pause = text_sprite("PAUSE", BLACK, 52, ORANGE)
    s_q = text_sprite(q_text, BLACK, 66, WHITE)
    s_opt = [option_sprite(letters[i], o, "normal") for i, o in enumerate(opts)]
    s_ok = option_sprite(letters[ans], opts[ans], "correct")
    nums = {n: text_sprite(str(n), BLACK, 100, ORANGE) for n in (3, 2, 1)}
    s_cta = text_sprite(f"comment {', '.join(letters[:-1])} or {letters[-1]}", BOLD, 38, GREY)
    s_see = text_sprite(payoff.get("label", "LET'S SEE..."), BLACK, 64, ORANGE) if payoff else None
    oy = [700 + i * 132 for i in range(3)] if len(opts) == 3 else [730 + i * 150 for i in range(2)]
    ring_y, cta_y, wm_y = 1135, 1262, 1350
    C0, C1 = 0.95, 3.95                                       # countdown window
    LOCK, END = C1, (C1 + 0.55 if payoff else C1 + 1.05)

    def frame(t):
        e = ease(prog(t, 0, 0.3))
        bg = Image.blend(base, dark, e) if e < 1 else dark
        bg = zoomed(bg, 1 + 0.035 * prog(t, 0.3, C1 - 0.3)).copy()
        fl = 1 - prog(t, 0, 0.12)
        if fl > 0: bg = Image.blend(bg, Image.new("RGBA", (W, H), (255, 255, 255, 255)), 0.45 * fl)
        out_a = 1 - ease(prog(t, END - 0.22, 0.22))           # everything fades before the next part
        put(bg, s_pause, 468, out_a * ease(prog(t, 0.08, 0.15)), pop(t, 0.08, 0.25, 0.3))
        put(bg, s_q, 560, out_a * ease(prog(t, 0.2, 0.25)), dy=30 * (1 - ease(prog(t, 0.2, 0.25))))
        for i, s in enumerate(s_opt):
            st = 0.4 + 0.13 * i
            a = ease(prog(t, st, 0.25)) * out_a
            if t >= LOCK and not payoff:
                if i == ans:
                    put(bg, s_ok, oy[i], out_a, pop(t, LOCK, 0.3, 0.12)); continue
                a *= 1 - 0.65 * prog(t, LOCK, 0.15)
            put(bg, s, oy[i], a, dy=40 * (1 - ease(prog(t, st, 0.25))))
        if t < LOCK:
            k = min(2, int(max(0, t - C0)))
            frac = 1 - prog(t, C0, C1 - C0)
            sc = pop(t, C0 + k, 0.18, 0.35) if t >= C0 else 1
            put(bg, ring_sprite(frac, nums[3 - k], sc), ring_y, ease(prog(t, 0.6, 0.25)))
            put(bg, s_cta, cta_y, ease(prog(t, 0.75, 0.25)))
        elif payoff:
            put(bg, s_see, ring_y, out_a, pop(t, LOCK, 0.25, 0.25))
        put(bg, WM, wm_y + WM.height // 2, ease(prog(t, 0.5, 0.3)) * out_a)
        return bg

    tr = sfx.Track(END)
    tr.add(0, sfx.tape_stop()); tr.add(0.38, sfx.whoosh(0.35, 0.2))
    for k in range(3):
        tr.add(C0 + k, sfx.tick()); tr.add(C0 + k, sfx.heartbeat(0.7 + 0.15 * k))
    if payoff: tr.add(LOCK, sfx.whoosh(0.45, 0.35))
    else: tr.add(LOCK, sfx.ding()); tr.add(END - 0.3, sfx.whoosh(0.4, 0.3))
    render(frame, int(END * FPS), tr, out, tmp, "quiz")


# ---------------------------------------------------------------- reveal
def reveal_part(spec, dark, out, tmp, hold=8.2):
    r = spec["reveal"]; ans = spec["answer_index"]
    ans_line = r.get("answer_line") or f"ANSWER: {'ABC'[ans]}) {spec['options'][ans]}"
    s_ans = text_sprite(ans_line, BOLD, 38, GREY)
    count_to, suffix = r.get("count_to"), r.get("count_suffix", "")
    s_big = text_sprite(r["big"], BLACK, 132, ORANGE)
    rows = []  # (sprite, centre_y, start_time)
    if r.get("rank"): rows.append((text_sprite(r["rank"], BLACK, 54, WHITE), 668, 1.1))
    if r.get("who"): rows.append((text_sprite(r["who"], BOLD, 38, BLUE), 736, 1.35))
    div = Image.new("RGBA", (W - 180, 4), ORANGE + (255,))
    y = 812
    if r.get("round"):
        rows.append((text_sprite("THIS ROUND", BLACK, 38, ORANGE), y, 1.7)); y += 54
        for i, line in enumerate(r["round"]):
            rows.append((text_sprite(line, BOLD, 40, WHITE), y, 1.85 + 0.15 * i)); y += 54
    if r.get("match"):
        rows.append((text_sprite("THIS MATCH", BLACK, 38, ORANGE), y + 14, 2.3))
        rows.append((text_sprite(r["match"], BOLD, 36, WHITE), y + 64, 2.45)); y += 64
    wm_y = y + 100
    rows.append((WM, wm_y, 2.7))
    rows.append((text_sprite(r["credit"], BOLD, 30, GREY), wm_y + 92, 2.9))
    rows.append((text_sprite("follow @eloquiz for a new one every day", BOLD, 32, ORANGE), wm_y + 136, 3.0))
    LAND = 1.0 if count_to else 0.15

    def frame(t):
        sh = (0, 0)
        if LAND <= t < LAND + 0.16:
            rr = random.Random(int(t * 1000)); m = int(10 * (1 - prog(t, LAND, 0.16)))
            sh = (rr.randint(-m, m), rr.randint(-m, m))
        bg = zoomed(dark, 1.04 + 0.03 * prog(t, 0, hold), sh).copy()
        put(bg, s_ans, 462, ease(prog(t, 0, 0.2)))
        if count_to and t < LAND:
            v = int(count_to * ease(prog(t, 0.05, LAND - 0.05)))
            put(bg, text_sprite(f"{v:,}{suffix}", BLACK, 132, ORANGE), 560, ease(prog(t, 0, 0.12)))
        elif t >= LAND - 0.01 or not count_to:
            put(bg, s_big, 560, ease(prog(t, LAND - 0.15, 0.15)) if not count_to else 1, pop(t, LAND, 0.25, 0.22))
        for spr, cy, st in rows:
            p = ease(prog(t, st, 0.25))
            put(bg, spr, cy, p, dy=24 * (1 - p))
        dp = ease(prog(t, 1.5, 0.3))
        if dp > 0:
            put(bg, div.resize((max(1, int(div.width * dp)), 4)), 780)
        return bg

    tr = sfx.Track(hold)
    if count_to:
        k = 0.05
        while k < LAND - 0.02:
            tr.add(k, sfx.counter_blip()); k += 0.035 + 0.09 * (k / LAND) ** 2
    tr.add(LAND, sfx.impact())
    for _, _, st in rows[:2]:
        tr.add(st, sfx.whoosh(0.25, 0.12))
    render(frame, int(hold * FPS), tr, out, tmp, "reveal")


# ---------------------------------------------------------------- main
def main():
    clip, out, spec = sys.argv[1], sys.argv[2], json.load(open(sys.argv[4]))
    end = clean_end(clip) if sys.argv[3] == "auto" else float(sys.argv[3])
    print("gameplay ends at", end)
    tmp = Path(tempfile.mkdtemp(prefix="quizreel_"))
    payoff = spec.get("payoff")
    gameplay(clip, tmp / "s1.mp4", float(spec.get("start", 0)), end, spec["hook"], spec.get("hook_sub", ""), tmp, "hook")
    base, dark = grab(clip, end - 0.12, tmp, "freeze")
    quiz_part(spec, base, dark, tmp / "s2.mp4", tmp, payoff)
    parts = ["s1", "s2"]
    if payoff:
        gameplay(clip, tmp / "s3.mp4", payoff["start"], payoff["end"], payoff.get("title", "WATCH..."),
                 payoff.get("sub", ""), tmp, "payoff")
        parts.append("s3")
        _, dark = grab(clip, payoff["end"] - 0.12, tmp, "freeze2")
    reveal_part(spec, dark, tmp / "s4.mp4", tmp)
    parts.append("s4")
    lst = tmp / "list.txt"
    lst.write_text("\n".join(f"file '{tmp / p}.mp4'" for p in parts) + "\n")
    run("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c:v", "libx264", "-crf", "18",
        "-preset", "slow", "-pix_fmt", "yuv420p", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000", "-movflags", "+faststart", out)
    cover = Path(out).with_name(Path(out).stem + "_cover.jpg")
    run("ffmpeg", "-v", "error", "-y", "-ss", "0.4", "-i", out, "-frames:v", "1", "-q:v", "2", cover)
    print("built", out)


if __name__ == "__main__":
    main()
