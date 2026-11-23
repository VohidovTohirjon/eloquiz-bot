#!/usr/bin/env python3
"""@eloquiz PURE rubric v2, built with the /brag-slim creative laws (installed at ~/.claude/skills/brag*).

  make_brag_edit.py <clip.mp4> <out_dir> --nick N --title "Dust 2 AK-47 4K" [--kills 4] [--map "Dust 2"]
                    [--weapon AK-47] [--track 1]

Shape (about 15 s, 1080x1920, 30 fps):
  HOOK      2 beats x2  the final kill first, slowed, "4 KILLS. / ONE ROUND." (flash-forward)
  PLAY      the round from just before kill 1; every kill is retimed (speed 0.8-1.6x) to land ON a beat,
            each kill = zoom punch + flash + big counter; the last kill hits the music's drop, then slow-mo
  OUTRO     6 beats: freeze, big result, player line, "rate it 1-10", follow, credits (clip + music)
Kills are found in the Allstar killfeed (the player's own kills have a red outline, top right).
Music: Sascha Ende, ende.app "Happy Beats / Business Moves" (CC BY 4.0, credit in the outro and caption).
Text stays inside y 250-1536 and out of the right 140 px of the lower half (Reels UI).
Writes <out_dir>/reel.mp4, reel_cover.jpg (= frame 0), brag-plan.md.
"""
from __future__ import annotations

import argparse, json, math, subprocess, sys, tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from clipcut import clean_end, card_end  # noqa: E402
from make_quiz_reel import text_sprite, WM, BLACK, BOLD, ORANGE, WHITE, GREY  # noqa: E402

W, H, FPS = 1080, 1920, 30
BRAG = HERE / "assets/brag" if (HERE / "assets/brag").exists() else Path.home() / ".claude/skills/brag/assets"
TRACKS = {v: f"happy-beats-business-moves-vol-{v}-by-ende-dot-app" for v in ("1", "9", "10", "11", "12")}
SAFE_TOP, SAFE_BOT = 250, 1536


def run(*a):
    subprocess.run([str(x) for x in a], check=True)


# ------------------------------------------------------------------ analysis
def find_kills(clip: str, lo: float, hi: float, k: int | None) -> list[float]:
    """Times of the player's own kills. Their killfeed entries (top right) are dark boxes with a red outline:
    the right edge column (x~1907 at 1080p) is red while 8 px inside is dark. A Dust 2 red shop sign is red on
    both, so it doesn't count. Entries are ~34 px tall; a new entry = count goes up and stays up (debounced)."""
    x0, y0, w, h = 1880, 40, 40, 320
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{lo}", "-to", f"{hi}", "-i", clip, "-vf",
                          f"scale=1920:1080,fps=20,crop={w}:{h}:{x0}:{y0}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).astype(int)

    def isred(px):
        r, g, b = px[..., 0], px[..., 1], px[..., 2]
        return (r > 140) & (g < 110) & (b < 100) & (r - g > 70)

    counts, lows = [], []
    for f in fr:
        edge = isred(f[:, 26:29]).any(axis=1)                    # x 1906-1908
        inner = isred(f[:, 16:19]).any(axis=1)                   # x 1896-1898 (inside the box)
        col = edge & ~inner
        n, y, low = 0, 0, -1
        while y < h:
            if col[y]:
                s0 = y
                while y < h and (col[y] or (y + 1 < h and col[y + 1])): y += 1
                ln = y - s0
                if ln >= 18:
                    n += max(1, round(ln / 34)); low = y
            y += 1
        counts.append(n); lows.append(low)

    def smooth(v):
        v = np.array(v)
        return np.array([int(np.median(v[max(0, i - 3):i + 4])) for i in range(len(v))])

    def held(sm, i, val, tol=0):
        return all(abs(x - val) <= tol for x in sm[i:i + 4])

    sc, sl = smooth(counts), smooth(lows)
    events = []
    st_c, st_l = sc[0], sl[0]
    for i in range(1, len(sc)):
        t = round(lo + (i - 2) / 20, 2)
        if sc[i] > st_c and held(sc, i, sc[i]):                  # more red boxes than before
            events += [t] * int(sc[i] - st_c)
        elif sl[i] - st_l >= 25 and st_l >= 0 and held(sl, i, sl[i], 4):  # new box below the lowest one
            events.append(t)
        if held(sc, i, sc[i]): st_c = sc[i]
        if held(sl, i, sl[i], 4): st_l = sl[i]
    kills: list[float] = []
    for t in sorted(events):                                     # one kill per 0.25 s unless the count said 2 at once
        if not kills or t - kills[-1] >= 0.25 or events.count(t) > 1:
            kills.append(t)
    return kills[:k] if k else kills


def load_track(num: str):
    stem = TRACKS.get(num, TRACKS["1"])
    cues = json.loads((BRAG / "music/cues" / f"{stem}.music-cues.json").read_text())
    period = 60.0 / cues["tempo"]
    top = max(c["intensity"] for c in cues["strongCues"])
    drops = sorted(c["time"] for c in cues["strongCues"] if c["intensity"] >= 0.97 * top)
    return BRAG / "music" / f"{stem}.mp3", period, drops


# ------------------------------------------------------------------ timeline
def timeline(kills: list[float], period: float, end_src: float):
    """Segments (src_a, src_b, speed) and output kill times, every kill on the beat grid."""
    hook_beats = 4
    H0 = hook_beats * period
    last = kills[-1]
    segs = [(max(0.0, last - 0.75), last + 0.3, None)]          # hook, retimed to fill H0
    segs[0] = (segs[0][0], segs[0][1], (segs[0][1] - segs[0][0]) / H0)
    lead = 3 * period
    a = max(0.0, kills[0] - lead * 1.0)
    u, prev, U = H0, a, []
    for kt in kills:
        dt = kt - prev
        best = None
        for n in range(1, 40):
            v = dt / (n * period)
            goal = 1.35 if dt > 2.5 else 1.0                     # chaotic: rush the quiet stretches
            if 0.8 <= v <= 1.6 and (best is None or abs(v - goal) < abs(best[1] - goal)):
                best = (n, v)
        n, v = best or (max(1, round(dt / period)), dt / (max(1, round(dt / period)) * period))
        segs.append((prev, kt, v)); u += n * period; U.append(u); prev = kt
    slo = min(0.5, end_src - last - 0.05)
    segs.append((last, last + slo, 0.5))                         # 2 beats of slow-mo on the final kill
    u += slo / 0.5
    tail = min(0.45, max(0.0, end_src - last - slo))
    if tail > 0.05:
        segs.append((last + slo, last + slo + tail, 1.0)); u += tail
    return segs, U, H0, u


# ------------------------------------------------------------------ render
SQ_Y = 430                      # sharp 1080x1080 gameplay square (source 1:1, no upscale) sits at y 430-1510
KF = (1525, 28, 395, 210)       # killfeed in the 1080p source: x, y, w, h -> pasted back top-right of the square
CB = (170, 810, 760, 260)       # Allstar intro player card inside the square (nickname = spoiler) -> blurred


def build_base(clip, segs, tmp: Path, card: float = 0.0) -> Path:
    """Retimed 1080x1920 gameplay: blurred full-bleed background, a sharp 1:1 square of the centre of the
    1080p source (crosshair centred), and the real killfeed in its top-right corner (video + game audio)."""
    vf, af, cat = [], [], ""
    kx, ky, kw, kh = KF
    for i, (a, b, v) in enumerate(segs):
        blur = ""
        if a < card - 0.05:
            cx, cy, cw, ch = CB
            blur = (f",split[q1{i}][q2{i}];[q2{i}]crop={cw}:{ch}:{cx}:{cy},boxblur=22:3[cb{i}];"
                    f"[q1{i}][cb{i}]overlay={cx}:{cy}:enable='lt(t,{card - a:.2f})'")
        vf.append(f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,split=3[x{i}][y{i}][k{i}];"
                  f"[x{i}]scale=-2:{H},crop={W}:{H},boxblur=32:2,eq=brightness=-0.32:saturation=1.1[bg{i}];"
                  f"[y{i}]crop={W}:{W}:420:0,eq=contrast=1.05:saturation=1.12{blur},split[l1{i}][l2{i}];"
                  f"[l2{i}]crop=96:100:0:30,boxblur=16:2[lg{i}];[l1{i}][lg{i}]overlay=0:30[sq{i}];"   # FACEIT|ALLSTAR logo edge
                  f"[k{i}]crop={kw}:{kh}:{kx}:{ky},eq=brightness=-0.08:contrast=1.25:saturation=0.85,"
                  f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.75:t=5[kf{i}];"
                  f"[bg{i}][sq{i}]overlay=0:{SQ_Y}[t{i}];[t{i}][kf{i}]overlay={W - kw - 12}:{SQ_Y + 14}[c{i}];"
                  f"[c{i}]setpts=PTS/{v},fps={FPS}[v{i}]")
        t = []
        x = v
        while x < 0.5: t.append("atempo=0.5"); x /= 0.5
        while x > 2.0: t.append("atempo=2.0"); x /= 2.0
        t.append(f"atempo={x:.5f}")
        af.append(f"[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS,{','.join(t)}[a{i}]")
        cat += f"[v{i}][a{i}]"
    fc = ";".join(vf + af) + f";{cat}concat=n={len(segs)}:v=1:a=1[v][a]"
    out = tmp / "base.mov"
    run("ffmpeg", "-v", "error", "-y", "-i", clip, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-crf", "12", "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", out)
    return out


def punch_square(img: Image.Image, z: float, fl: float) -> Image.Image:
    """Zoom punch + flash on the gameplay square only (the background stays calm)."""
    if z <= 1.001 and fl <= 0.001:
        return img
    sq = img.crop((0, SQ_Y, W, SQ_Y + W))
    if z > 1.001:
        c = int(W / z); o = (W - c) // 2
        sq = sq.crop((o, o, o + c, o + c)).resize((W, W), Image.LANCZOS)
    if fl > 0.001:
        sq = Image.blend(sq, Image.new("RGBA", (W, W), (255, 255, 255, 255)), fl)
    img = img.copy(); img.paste(sq, (0, SQ_Y))
    return img


def ease_out(x):
    return 1 - (1 - min(1, max(0, x))) ** 3


def place(canvas: Image.Image, spr: Image.Image, cy: int, scale: float = 1.0, alpha: float = 1.0, cx: int = W // 2):
    if scale != 1.0:
        spr = spr.resize((max(1, int(spr.width * scale)), max(1, int(spr.height * scale))), Image.LANCZOS)
    if alpha < 1.0:
        spr = spr.copy(); spr.putalpha(spr.getchannel("A").point(lambda p: int(p * alpha)))
    canvas.alpha_composite(spr, (max(0, int(cx - spr.width / 2)), int(cy - spr.height / 2)))


def shadowed(text, font, size, color):
    s = text_sprite(text, font, size, color)
    sh = Image.new("RGBA", (s.width + 24, s.height + 24), (0, 0, 0, 0))
    blk = Image.new("RGBA", s.size, (0, 0, 0, 255)); blk.putalpha(s.getchannel("A"))
    sh.alpha_composite(blk, (12, 16)); sh = sh.filter(ImageFilter.GaussianBlur(7))
    sh.alpha_composite(s, (12, 8))
    return sh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip"); ap.add_argument("out_dir")
    ap.add_argument("--nick", required=True); ap.add_argument("--title", default="")
    ap.add_argument("--kills", type=int); ap.add_argument("--map", default=""); ap.add_argument("--weapon", default="")
    ap.add_argument("--track", default="1")
    o, _ = ap.parse_known_args()
    out = Path(o.out_dir); out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="bragedit_"))
    card, hi = card_end(o.clip), clean_end(o.clip)
    lo = 0.3                                                     # kills before the intro card ends count too (card gets blurred)
    kills = find_kills(o.clip, lo, hi, o.kills)
    if not kills:
        sys.exit("no kills found in the killfeed")
    music, period, drops = load_track(o.track)
    segs, U, H0, play_end = timeline(kills, period, hi)
    drop = next((d for d in drops if d >= U[-1]), drops[-1])   # strongest beat the final kill can land on
    outro = 6 * period
    total = play_end + outro
    M = drop - U[-1]                                             # music offset: final kill lands on the drop
    if M < 0:
        M += math.ceil(-M / period) * period * 1.0
    n = len(kills)
    show_counter = not o.kills or n == o.kills                   # killfeed missed one: trust the title, hide the counter
    if o.kills:
        n = o.kills
    big = {1: "1 KILL.", 2: "2 KILLS.", 3: "3 KILLS.", 4: "4 KILLS.", 5: "ACE."}.get(n, f"{n} KILLS.")
    result = {1: "1K", 2: "2K", 3: "3K", 4: "4K", 5: "ACE"}.get(n, f"{n}K")
    sub = " · ".join(x for x in [o.map.upper(), o.weapon.upper()] if x)

    base = build_base(o.clip, segs, tmp, card)
    # sprites
    S = {"hook1": shadowed(big, BLACK, 150, WHITE), "hook2": shadowed("ONE ROUND.", BLACK, 104, ORANGE),
         "hooksub": shadowed(sub, BLACK, 52, WHITE) if sub else None,
         "res": shadowed(result, BLACK, 300, ORANGE), "who": shadowed(o.nick, BLACK, 64, WHITE),
         "where": shadowed(sub or "FACEIT", BOLD, 44, GREY), "rate": shadowed("RATE IT 1-10", BLACK, 70, WHITE),
         "follow": shadowed("follow @eloquiz for daily CS2", BOLD, 42, ORANGE),
         "c1": text_sprite(f"Clip: {o.nick} on FACEIT · captured by Allstar", BOLD, 30, GREY),
         "c2": text_sprite("Music: Sascha Ende, ende.app (CC BY 4.0)", BOLD, 30, GREY)}
    counters = {k: shadowed(f"{k}", BLACK, 190, WHITE if k < n else ORANGE) for k in range(1, max(n, len(U)) + 1)}
    klabels = {1: shadowed("KILL", BLACK, 54, ORANGE), 2: shadowed("KILLS", BLACK, 54, ORANGE)}
    wm = WM

    frames_in = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(base), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                 stdout=subprocess.PIPE)
    src = []
    while True:
        buf = frames_in.stdout.read(W * H * 3)
        if len(buf) < W * H * 3: break
        src.append(buf)
    frames_in.wait()
    nplay = len(src)
    last_img = Image.frombuffer("RGB", (W, H), src[-1]).convert("RGBA")
    dark_bg = Image.blend(last_img, Image.new("RGBA", (W, H), (0, 0, 0, 255)), 0.62).filter(ImageFilter.GaussianBlur(6))

    def frame(i: int) -> Image.Image:
        t = i / FPS
        if i < nplay:
            img = Image.frombuffer("RGB", (W, H), src[i]).convert("RGBA")
            z, fl = 1.0, 0.0
            for u in U + [0.0]:                                  # hook opens with a punch too
                if 0 <= t - u < 0.32:
                    q = (t - u) / 0.32
                    z = max(z, 1.0 + 0.14 * (1 - ease_out(q))); fl = max(fl, 0.55 * (1 - q) ** 2)
            if t >= U[-1]:                                       # slow push-in through the final slow-mo
                z = max(z, 1.0 + 0.08 * min(1, (t - U[-1]) / (play_end - U[-1] + 1e-6)))
            img = punch_square(img, z, fl)
            if t < H0:                                           # HOOK text
                place(img, S["hook1"], 610, 1 + 0.25 * (1 - ease_out((t - 0.05) / 0.18)), min(1, (t - 0.0) / 0.1))
                if t > 0.35:
                    place(img, S["hook2"], 745, 1 + 0.25 * (1 - ease_out((t - 0.35) / 0.18)), min(1, (t - 0.35) / 0.1))
                if S["hooksub"] and t > 0.6:
                    place(img, S["hooksub"], 1350, 1.0, min(1, (t - 0.6) / 0.15))
            else:                                                # kill counter
                k = sum(1 for u in U if t >= u)
                if k and show_counter:
                    u = U[k - 1]
                    place(img, counters[k], 575, 1 + 0.45 * (1 - ease_out((t - u) / 0.16)), cx=150)
                    place(img, klabels[1 if k == 1 else 2], 690, cx=150)
            img.alpha_composite(wm, ((W - wm.width) // 2, 300))   # brand in the top band, square stays clean
            return img
        # OUTRO, lines arrive on beats
        to = t - play_end
        img = dark_bg.copy()
        place(img, S["res"], 640, 1 + 0.3 * (1 - ease_out(to / 0.2)), min(1, to / 0.08))
        for key, cy, beat in (("who", 850, 1), ("where", 930, 1), ("rate", 1080, 2), ("follow", 1180, 3)):
            bt = beat * period
            if to >= bt:
                place(img, S[key], cy, 1.0, min(1, (to - bt) / 0.12))
        if to >= 3 * period:
            place(img, S["c1"], 1320, 1.0, min(1, (to - 3 * period) / 0.12))
            place(img, S["c2"], 1365, 1.0, min(1, (to - 3 * period) / 0.12))
        img.alpha_composite(wm, ((W - wm.width) // 2, 300))
        return img

    nframes = int(round(total * FPS))
    poster_i = int((play_end + 3.2 * period) * FPS)             # settled outro frame (result + player + rate)
    poster = frame(min(poster_i, nframes - 1)).convert("RGB")
    poster.save(out / "reel_cover.jpg", quality=92)
    if "--stills" in sys.argv:                                  # brag check: every scene + mid-transition + safe bands
        st = out / "stills"; st.mkdir(exist_ok=True)
        times = [0.15, 0.5, 1.2, H0 - 0.04, H0 + 0.04] + [u + d for u in U for d in (0.03, 0.2)] + \
                [play_end - 0.05, play_end + 0.05, play_end + period * 1.2, total - 0.1]
        for t in times:
            im = frame(min(int(t * FPS), nframes - 1)).convert("RGB")
            d = ImageDraw.Draw(im)
            d.line([(0, SAFE_TOP), (W, SAFE_TOP)], fill=(0, 255, 0), width=3)
            d.line([(0, SAFE_BOT), (W, SAFE_BOT)], fill=(0, 255, 0), width=3)
            d.rectangle([W - 140, H // 2, W, H], outline=(0, 255, 0), width=3)
            im.resize((540, 960)).save(st / f"t{t:05.2f}.jpg", quality=80)
        print("stills", sorted(x.name for x in st.iterdir()), "kills", kills, "U", [round(u, 2) for u in U])
        return
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "17", "-preset", "slow",
                            "-pix_fmt", "yuv420p", str(tmp / "v.mp4")], stdin=subprocess.PIPE)
    for i in range(nframes):
        enc.stdin.write((poster if i == 0 else frame(i).convert("RGB")).tobytes())
    enc.stdin.close(); enc.wait()

    # audio: music from offset M (fade in), game audio under it, soft SFX on each kill, all on one grid
    sfx_hit = BRAG / "sfx/impact/impactGeneric_light_000.ogg"
    sfx_big = BRAG / "sfx/impact/impactBell_heavy_000.ogg"
    sfx_whoosh = BRAG / "sfx/interface/glitch_002.ogg"
    ins = ["-i", tmp / "v.mp4", "-i", base, "-ss", f"{M:.3f}", "-i", music, "-i", sfx_hit, "-i", sfx_big, "-i", sfx_whoosh]
    parts = [f"[1:a]volume=0.75,apad,atrim=0:{total:.3f}[g]",
             f"[2:a]atrim=0:{total:.3f},afade=t=in:d=0.25,afade=t=out:st={total - 0.8:.3f}:d=0.8,volume=0.55[m]"]
    mix = "[g][m]"
    for j, u in enumerate(U):
        src_i = 4 if j == len(U) - 1 else 3
        parts.append(f"[{src_i}:a]adelay={int(u * 1000)}|{int(u * 1000)},volume={0.5 if src_i == 4 else 0.35}[s{j}]")
        mix += f"[s{j}]"
    parts.append(f"[5:a]adelay={int(H0 * 1000 - 120)}|{int(H0 * 1000 - 120)},volume=0.4[w]")
    mix += "[w]"
    parts.append(f"{mix}amix=inputs={len(U) + 3}:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    run("ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart", out / "reel.mp4")

    plan = [f"# brag plan: {o.nick} {o.title}", "", f"Angle: {big} {('ONE ROUND.')} The round, cut to the beat.",
            f"Tone: chaotic (ALL CAPS, flash/zoom cuts). Track: {music.name} ({60 / period:.0f} BPM), starts at {M:.2f}s so the final kill hits the drop ({drop:.2f}s).",
            f"Kills found in the killfeed at source {kills} -> output {[round(u, 2) for u in U]}.", "",
            "| Scene | Output time | What |", "|---|---|---|",
            f"| Hook | 0.00-{H0:.2f} | final kill first, slowed; '{big}' / 'ONE ROUND.' / '{sub}' |",
            f"| Play | {H0:.2f}-{play_end:.2f} | the round; each kill on a beat = punch + flash + counter; slow-mo on the last |",
            f"| Outro | {play_end:.2f}-{total:.2f} | {result}, player, 'RATE IT 1-10', follow, credits |", "",
            f"Segments (src_a, src_b, speed): {[(round(a, 2), round(b, 2), round(v, 3)) for a, b, v in segs]}"]
    (out / "brag-plan.md").write_text("\n".join(plan) + "\n")
    print("built", out / "reel.mp4", f"{total:.2f}s", "kills", kills)


if __name__ == "__main__":
    main()
