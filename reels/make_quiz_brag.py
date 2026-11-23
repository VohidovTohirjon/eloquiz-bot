#!/usr/bin/env python3
"""@eloquiz quiz reels v3 (Guess the Elo / Clutch or choke / Legit or cheating), built with the /brag-slim laws.

  make_quiz_brag.py <clip.mp4> <out_dir> <spec.json> [--kills N] [--track 1|9|10|11|12]

One beat grid for the whole reel (music tempo, ~0.5 s a beat):
  PLAY    full-bleed 9:16 gameplay, retimed so every kill lands on a beat (punch + flash + counter).
          Big question for the first 4 beats, then it shrinks to a chip at the top. Clutch stops ~1 s
          before the final kill.
  QUIZ    8-10 beats: freeze + PAUSE, question, options one by one, 3-2-1 countdown on the beats, lock.
  PAYOFF  (clutch) the final kill, slow-mo.
  REVEAL  answer line, Elo count-up that LANDS on a strong beat of the track, stats one per beat,
          follow + credits (clip + music). Held long enough to read.
Same spec.json as make_quiz_reel.py. Readable text stays in y 250-1536, out of the right 140 px of the
lower half. Frame 0 = the settled quiz frame (cover). Writes reel.mp4, reel_cover.jpg, brag-plan.md.
"""
from __future__ import annotations

import argparse, json, random, re, subprocess, sys, tempfile
from pathlib import Path

from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sfx  # noqa: E402
from clipcut import clean_end, card_end  # noqa: E402
from make_quiz_reel import (text_sprite, option_sprite, ring_sprite, put, prog, ease, pop, zoomed, WM, BLACK, BOLD,  # noqa: E402
                            ORANGE, WHITE, GREY, BLUE, QUESTION)
from make_brag_edit import find_kills, load_track, build_base, punch_square, shadowed, W, H, FPS  # noqa: E402


def run(*a):
    subprocess.run([str(x) for x in a], check=True)


def plan_segments(kills, p, start, stop):
    """Gameplay segments from `start` to `stop`; each kill inside lands on a beat. Returns segs, kill beat times, length."""
    segs, U, u, prev = [], [], 0.0, start
    for kt in [k for k in kills if start < k <= stop]:
        dt = kt - prev
        goal = 1.35 if dt > 2.5 else 1.0
        best = None
        for n in range(1, 40):
            v = dt / (n * p)
            if 0.8 <= v <= 1.6 and (best is None or abs(v - goal) < abs(best[1] - goal)):
                best = (n, v)
        n, v = best or (max(1, round(dt / p)), dt / (max(1, round(dt / p)) * p))
        segs.append((prev, kt, v)); u += n * p; U.append(u); prev = kt
    rest = stop - prev
    if rest > 0.05:                                              # tail, stretched to end on a beat
        n = max(1, round(rest / p)); v = rest / (n * p)
        v = min(1.6, max(0.8, v)); segs.append((prev, stop, v)); u += rest / v
    return segs, U, u


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip"); ap.add_argument("out_dir"); ap.add_argument("spec")
    ap.add_argument("--kills", type=int); ap.add_argument("--track", default="1")
    o, _ = ap.parse_known_args()
    spec = json.loads(Path(o.spec).read_text())
    rub = spec.get("rubric", "elo")
    out = Path(o.out_dir); out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="quizbrag_"))
    card, hi = card_end(o.clip), clean_end(o.clip)
    lo = 0.3                                                     # count kills under the intro card too; the card is blurred
    kills = find_kills(o.clip, lo, hi, o.kills)
    show_counter = bool(kills) and (not o.kills or len(kills) == o.kills)
    music, p, drops = load_track(o.track)

    # ---- gameplay plan
    first = kills[0] if kills else max(lo, hi - 10)
    start = max(lo, first - 3 * p, hi - 18)
    if rub == "clutch":
        last = kills[-1] if kills else hi - 1.0
        pause = max(start + 2 * p, last - 0.9)
        start = max(lo, min(first - 3 * p, pause - 9.0))     # every kill of the clutch + its setup                       # enough of the setup to feel the clutch
        segs, U, play_len = plan_segments(kills, p, start, pause)
        pay_segs = [(pause, last, 1.0), (last, min(hi, last + 0.5), 0.5)]
        if hi - (last + 0.5) > 0.1: pay_segs.append((last + 0.5, min(hi, last + 0.9), 1.0))
    else:
        stop = min(hi, (kills[-1] + 0.6) if kills else hi)
        segs, U, play_len = plan_segments(kills, p, start, stop)
        pay_segs = []
    base = build_base(o.clip, segs, tmp, card)
    play = read_frames(base)
    if pay_segs: (tmp / "p").mkdir(exist_ok=True)
    pay = read_frames(build_base(o.clip, pay_segs, tmp / "p", card)) if pay_segs else []
    nplay = len(play); play_len = nplay / FPS

    # ---- section times (all on the beat grid from u=0)
    Q0 = play_len                                                # segments are whole beats, so this sits on the grid
    C0, LOCK = 2 * p, 8 * p
    q_end = LOCK + (1 * p if pay_segs else 2 * p)
    P0 = Q0 + q_end
    R0 = P0 + len(pay) / FPS
    LAND_rel = 2 * p if spec["reveal"].get("count_to") else 0.25 * p
    land = R0 + LAND_rel
    hold = 15 * p
    total = R0 + hold
    drop = next((d for d in drops if d >= land), drops[-1])
    M = max(0.0, drop - land)
    pay_kill_u = P0 + (pay_segs[0][1] - pay_segs[0][0]) if pay_segs else None

    # ---- sprites
    hook = shadowed(spec.get("hook") or QUESTION[rub], BLACK, 110, WHITE)
    hook_chip = shadowed(spec.get("hook") or QUESTION[rub], BLACK, 58, WHITE)
    hook_sub = shadowed(spec.get("hook_sub", "").upper(), BOLD, 40, WHITE) if spec.get("hook_sub") else None
    nk = o.kills or len(kills)
    counters = {k: shadowed(str(k), BLACK, 170, ORANGE if k == nk else WHITE) for k in range(1, max(nk, len(kills)) + 2)}
    klabel = {1: shadowed("KILL", BLACK, 46, ORANGE), 2: shadowed("KILLS", BLACK, 46, ORANGE)}

    opts, ans = spec["options"], spec["answer_index"]
    letters = "ABC"[:len(opts)]
    s_pause = text_sprite("PAUSE", BLACK, 52, ORANGE)
    s_q = text_sprite(spec.get("question") or QUESTION.get(rub, QUESTION["elo"]), BLACK, 66, WHITE)
    s_opt = [option_sprite(letters[i], x, "normal") for i, x in enumerate(opts)]
    s_ok = option_sprite(letters[ans], opts[ans], "correct")
    nums = {n: text_sprite(str(n), BLACK, 100, ORANGE) for n in (3, 2, 1)}
    s_cta = text_sprite(f"comment {', '.join(letters[:-1])} or {letters[-1]}", BOLD, 38, GREY)
    s_see = text_sprite("LET'S SEE...", BLACK, 64, ORANGE)
    oy = [700 + i * 132 for i in range(3)] if len(opts) == 3 else [730 + i * 150 for i in range(2)]

    r = spec["reveal"]
    s_ans = text_sprite(r.get("answer_line") or f"ANSWER: {letters[ans]}) {opts[ans]}", BOLD, 38, GREY)
    count_to, suffix = r.get("count_to"), r.get("count_suffix", "")
    s_big = text_sprite(r["big"], BLACK, 132, ORANGE)
    rows, y, beat = [], 812, 2.5                                 # (sprite, y, start in beats after reveal)
    if r.get("rank"): rows.append((text_sprite(r["rank"], BLACK, 54, WHITE), 668, 3))
    if r.get("who"): rows.append((text_sprite(r["who"], BOLD, 38, BLUE), 736, 3.5))
    if r.get("round"):
        rows.append((text_sprite("THIS ROUND", BLACK, 38, ORANGE), y, 4)); y += 54
        for i, line in enumerate(r["round"]):
            rows.append((text_sprite(line, BOLD, 40, WHITE), y, 4.5 + 0.5 * i)); y += 54
    mv = re.match(r"^([\d,]+) views on FACEIT$", r.get("match") or "")
    if mv and int(mv.group(1).replace(",", "")) < 1000:
        r = {**r, "match": None}                                 # "38 views" undersells the clip: drop the row
    if r.get("match"):
        rows.append((text_sprite("THIS MATCH", BLACK, 38, ORANGE), y + 14, 6))
        rows.append((text_sprite(r["match"], BOLD, 36, WHITE), y + 64, 6.5)); y += 64
    wm_y = min(y + 96, 1330)
    rows += [(WM, wm_y, 7), (text_sprite(r["credit"], BOLD, 28, GREY), wm_y + 84, 7.5),
             (text_sprite("Music: Sascha Ende, ende.app (CC BY 4.0)", BOLD, 26, GREY), wm_y + 120, 7.5),
             (text_sprite("follow @eloquiz for a new one every day", BOLD, 32, ORANGE), wm_y + 164, 8)]
    div = Image.new("RGBA", (W - 180, 4), ORANGE + (255,))

    def darken(img):
        blur = img.convert("RGB").filter(ImageFilter.GaussianBlur(14))
        mean = sum(blur.convert("L").getdata()) / (W * H)
        k = min(0.33, 32 / max(mean, 1))
        return blur.point(lambda v: int(v * k)).convert("RGBA")

    q_base = Image.frombuffer("RGB", (W, H), play[-1]).convert("RGBA")
    q_dark = darken(q_base)
    r_dark = darken(Image.frombuffer("RGB", (W, H), pay[-1]).convert("RGBA")) if pay else q_dark

    def punch(img, t, hits):
        z, fl = 1.0, 0.0
        for u in hits:
            if 0 <= t - u < 0.32:
                q = (t - u) / 0.32
                z = max(z, 1.0 + 0.13 * (1 - ease(q))); fl = max(fl, 0.5 * (1 - q) ** 2)
        return punch_square(img, z, fl)

    def frame(i):
        t = i / FPS
        if i < nplay:                                            # PLAY
            img = punch(Image.frombuffer("RGB", (W, H), play[i]).convert("RGBA"), t, U + [0.0]).copy()
            if t < 4 * p:
                put(img, hook, 640, ease(prog(t, 0, 0.12)), pop(t, 0.02, 0.22, 0.3))
                if hook_sub: put(img, hook_sub, 750, ease(prog(t, 0.5 * p, 0.15)))
            else:
                k = ease(prog(t, 4 * p, 0.2))
                put(img, hook if k < 1 else hook_chip, 640 - 300 * k, 1, 1 - 0.47 * k if k < 1 else 1)
                kc = sum(1 for u in U if t >= u)
                if kc and show_counter:
                    put(img, counters[kc], 575, 1, pop(t, U[kc - 1], 0.18, 0.4), cx=150)
                    put(img, klabel[1 if kc == 1 else 2], 690, cx=150)
            put(img, WM, 1450)
            return img
        tq = t - Q0
        if tq < q_end:                                           # QUIZ
            e = ease(prog(tq, 0, 0.3))
            bg = (Image.blend(q_base, q_dark, e) if e < 1 else q_dark)
            bg = zoomed(bg, 1 + 0.035 * prog(tq, 0.3, LOCK - 0.3)).copy()
            fl = 1 - prog(tq, 0, 0.12)
            if fl > 0: bg = Image.blend(bg, Image.new("RGBA", (W, H), (255, 255, 255, 255)), 0.45 * fl)
            out_a = 1 - ease(prog(tq, q_end - 0.2, 0.2))
            put(bg, s_pause, 468, out_a * ease(prog(tq, 0.05, 0.15)), pop(tq, 0.05, 0.25, 0.3))
            put(bg, s_q, 560, out_a * ease(prog(tq, p, 0.2)), dy=30 * (1 - ease(prog(tq, p, 0.2))))
            for j, s in enumerate(s_opt):
                st = 1.5 * p + 0.5 * p * j
                a = ease(prog(tq, st, 0.2)) * out_a
                if tq >= LOCK and not pay_segs:
                    if j == ans:
                        put(bg, s_ok, oy[j], out_a, pop(tq, LOCK, 0.3, 0.12)); continue
                    a *= 1 - 0.65 * prog(tq, LOCK, 0.15)
                put(bg, s, oy[j], a, dy=40 * (1 - ease(prog(tq, st, 0.2))))
            if tq < LOCK:
                k = min(2, int(max(0, tq - C0) / (2 * p)))
                frac = 1 - prog(tq, C0, LOCK - C0)
                sc = pop(tq, C0 + 2 * p * k, 0.18, 0.35) if tq >= C0 else 1
                put(bg, ring_sprite(frac, nums[3 - k], sc), 1135, ease(prog(tq, p, 0.2)))
                put(bg, s_cta, 1262, ease(prog(tq, 1.5 * p, 0.2)))
            elif pay_segs:
                put(bg, s_see, 1135, out_a, pop(tq, LOCK, 0.25, 0.25))
            put(bg, WM, 1350 + WM.height // 2, ease(prog(tq, p, 0.3)) * out_a)
            return bg
        if t < R0:                                               # PAYOFF (clutch)
            j = min(len(pay) - 1, int((t - P0) * FPS))
            img = punch(Image.frombuffer("RGB", (W, H), pay[j]).convert("RGBA"), t, [pay_kill_u]).copy()
            put(img, hook_chip, 340)
            if show_counter and t >= pay_kill_u:
                put(img, counters[len(kills)], 575, 1, pop(t, pay_kill_u, 0.18, 0.4), cx=150)
                put(img, klabel[2 if len(kills) > 1 else 1], 690, cx=150)
            put(img, WM, 1450)
            return img
        tr_ = t - R0                                             # REVEAL
        sh = (0, 0)
        if LAND_rel <= tr_ < LAND_rel + 0.16:
            rr = random.Random(int(t * 1000)); m = int(10 * (1 - prog(tr_, LAND_rel, 0.16)))
            sh = (rr.randint(-m, m), rr.randint(-m, m))
        bg = zoomed(r_dark, 1.04 + 0.03 * prog(tr_, 0, hold), sh).copy()
        put(bg, s_ans, 462, ease(prog(tr_, 0, 0.2)))
        if count_to and tr_ < LAND_rel:
            c0 = int(round(count_to * 0.5, -2))
            v = int(c0 + (count_to - c0) * ease(prog(tr_, 0.0, LAND_rel)))
            put(bg, text_sprite(f"{v:,}{suffix}", BLACK, 132, ORANGE), 560, ease(prog(tr_, 0, 0.12)))
        else:
            over = min(0.22, max(0.0, (W - 40) / s_big.width - 1))      # never pop wider than the screen
            put(bg, s_big, 560, 1 if count_to else ease(prog(tr_, 0, 0.12)), pop(tr_, LAND_rel, 0.25, over))
        for spr, cy, b in rows:
            q = ease(prog(tr_, b * p, 0.22))
            put(bg, spr, cy, q, dy=24 * (1 - q))
        dp = ease(prog(tr_, 2.5 * p, 0.3))
        if dp > 0: put(bg, div.resize((max(1, int(div.width * dp)), 4)), 780)
        return bg

    n_frames = int(round(total * FPS))
    poster = frame(int((Q0 + LOCK - 0.6 * p) * FPS)).convert("RGB")
    poster.save(out / "reel_cover.jpg", quality=92)
    if "--stills" in sys.argv:
        st = out / "stills"; st.mkdir(exist_ok=True)
        for t in [0.3, 1.5, 4 * p + 0.3] + [u + 0.05 for u in U] + [Q0 - 0.03, Q0 + 0.04, Q0 + 3 * p, Q0 + LOCK - 0.3,
                  Q0 + LOCK + 0.3, P0 + 0.05, R0 - 0.03, R0 + 0.04, land - 0.2, land + 0.3, R0 + 9 * p, total - 0.05]:
            frame(min(int(t * FPS), n_frames - 1)).convert("RGB").resize((360, 640)).save(st / f"t{t:05.2f}.jpg", quality=80)
        print("stills", len(list(st.iterdir())), "kills", kills, "U", [round(x, 2) for x in U]); return
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                            "-i", "-", "-c:v", "libx264", "-crf", "17", "-preset", "slow", "-pix_fmt", "yuv420p", str(tmp / "v.mp4")],
                           stdin=subprocess.PIPE)
    for i in range(n_frames):
        enc.stdin.write((poster if i == 0 else frame(i).convert("RGB")).tobytes())
    enc.stdin.close(); enc.wait()

    # ---- sound: music (offset M so the reveal lands on a strong beat), game audio, quiz/reveal SFX on the grid
    tr = sfx.Track(total)
    tr.add(Q0, sfx.tape_stop()); tr.add(Q0 + 1.5 * p, sfx.whoosh(0.3, 0.15))
    for k in range(3):
        tr.add(Q0 + C0 + 2 * p * k, sfx.tick()); tr.add(Q0 + C0 + 2 * p * k, sfx.heartbeat(0.6 + 0.12 * k))
    tr.add(Q0 + LOCK, sfx.whoosh(0.45, 0.3) if pay_segs else sfx.ding())
    if count_to:
        k = 0.05
        while k < LAND_rel - 0.02:
            tr.add(R0 + k, sfx.counter_blip()); k += 0.035 + 0.09 * (k / LAND_rel) ** 2
    tr.add(land, sfx.impact())
    tr.write(tmp / "fx.wav")
    game = [base] + ([tmp / "p" / "base.mov"] if pay_segs else [])
    ins = ["-i", tmp / "v.mp4", "-ss", f"{M:.3f}", "-i", music, "-i", tmp / "fx.wav"] + sum((["-i", g] for g in game), [])
    fc = [f"[1:a]atrim=0:{total:.3f},afade=t=in:d=0.3,afade=t=out:st={total - 1.0:.3f}:d=1.0,volume=0.5[m]",
          f"[3:a]volume=0.7,apad=whole_dur={Q0:.3f}[g0]"]
    mixin = "[m][2:a][g0]"
    if pay_segs:
        fc.append(f"[4:a]volume=0.8,adelay={int(P0 * 1000)}|{int(P0 * 1000)}[g1]"); mixin += "[g1]"
    fc.append(f"{mixin}amix=inputs={4 if pay_segs else 3}:normalize=0:duration=first,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    run("ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(fc), "-map", "0:v", "-map", "[a]", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{total:.3f}", "-movflags", "+faststart", out / "reel.mp4")
    (out / "brag-plan.md").write_text(
        f"# brag plan: {rub} quiz\n\nTrack {music.name} ({60 / p:.0f} BPM) from {M:.2f}s; reveal lands on its strong beat {drop:.2f}s.\n"
        f"Kills (source) {kills} -> beats {[round(x, 2) for x in U]}{' + payoff' if pay_segs else ''}.\n\n"
        f"| Scene | Time |\n|---|---|\n| Play (hook 0-{4 * p:.1f}s) | 0-{Q0:.2f} |\n| Quiz | {Q0:.2f}-{P0:.2f} |\n"
        + (f"| Payoff | {P0:.2f}-{R0:.2f} |\n" if pay_segs else "") + f"| Reveal | {R0:.2f}-{total:.2f} |\n")
    print("built", out / "reel.mp4", f"{total:.1f}s", "kills", kills)


def read_frames(video: Path) -> list[bytes]:
    pr = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    fr = []
    while True:
        b = pr.stdout.read(W * H * 3)
        if len(b) < W * H * 3: break
        fr.append(b)
    pr.wait()
    return fr


if __name__ == "__main__":
    main()
