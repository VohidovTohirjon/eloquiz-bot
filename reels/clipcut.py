#!/usr/bin/env python3
"""Find where the Allstar outro transition (orange streaks / hex end card) starts, so reels end on clean gameplay.
Usage: clipcut.py clip.mp4  -> prints the clean gameplay end in seconds."""
import subprocess, sys
import numpy as np


def duration(clip):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", clip],
                                capture_output=True, text=True).stdout.strip())


CARD = (0.3125, 0.76, 0.385, 0.222)   # Allstar intro player card (name, kills): x, y, w, h as fractions of the frame


def card_end(clip, fps=10, scan=9.0):
    """Seconds until the Allstar intro player card (shows the nickname = spoiler) is gone. 0 if none."""
    x, y, w, h = CARD
    raw = subprocess.run(["ffmpeg", "-v", "error", "-t", f"{scan}", "-i", clip, "-vf",
                          f"fps={fps},crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale=96:32", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, 32, 96, 3).astype(int)
    last = None
    for i, f in enumerate(fr):
        r, g, b = f[..., 0], f[..., 1], f[..., 2]
        if ((r > 200) & (g > 70) & (g < 150) & (b < 60)).mean() > 0.06:
            last = i / fps
    return round(last + 0.4, 2) if last is not None else 0.0


def clean_end(clip, fallback_outro=8.4, fps=20):
    dur = duration(clip)
    start = max(card_end(clip) + 0.5, dur - 13)
    w, h = 96, 54
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start}", "-i", clip, "-vf", f"fps={fps},scale={w}:{h}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).astype(int)
    prev = None
    for i, f in enumerate(fr):
        r, g, b = f[..., 0], f[..., 1], f[..., 2]
        orange = ((r > 170) & (g < 130) & (b < 90) & (r - g > 80)).mean()
        dark = (f.max(axis=2) < 25).mean()
        darkish = (f.max(axis=2) < 60).mean()
        jump = np.abs(f - prev).mean() if prev is not None else 0
        prev = f
        tail = start + i / fps >= dur - 9.0          # Allstar outro always sits in the last ~8.5 s
        # orange streaks on a dark screen = outro; orange on a bright frame = red walls (Nuke, Dust 2 signs)
        if (orange > 0.035 and darkish > 0.15) or (tail and (orange > 0.035 or dark > 0.6 or jump > 60)):
            return round(max(start + i / fps - 0.15, 1.0), 2)
    return round(max(dur - fallback_outro, 1.0), 2)


if __name__ == "__main__":
    print("card ends", card_end(sys.argv[1]), "· clean end", clean_end(sys.argv[1]))
