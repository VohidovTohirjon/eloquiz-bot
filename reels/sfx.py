"""Tiny synthesized sound-effects kit for @eloquiz reels (no samples, no licensing issues).
Every effect is a numpy mono float array at SR. `Track` mixes effects at given times."""
import wave
import numpy as np

SR = 48000
_rng = np.random.default_rng(7)


def _t(dur): return np.arange(int(SR * dur)) / SR


def tone(f, dur, decay, amp=0.5):
    t = _t(dur)
    return amp * np.sin(2 * np.pi * f * t) * np.exp(-t / decay)


def sweep(f0, f1, dur, decay, amp=0.5):
    t = _t(dur)
    f = f0 * (f1 / f0) ** (t / dur)                   # exponential glide
    return amp * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / decay)


def noise(dur, smooth=6):
    n = _rng.standard_normal(int(SR * dur))
    return np.convolve(n, np.ones(smooth) / smooth, mode="same")


def mix(*sigs):
    out = np.zeros(max(len(x) for x in sigs))
    for x in sigs: out[:len(x)] += x
    return out


def tick(amp=0.45):            # countdown clock tick
    return mix(tone(1800, 0.06, 0.010, amp), tone(3600, 0.04, 0.005, amp * 0.35))


def heartbeat(amp=0.9):        # lub-dub under the countdown
    a = sweep(95, 45, 0.22, 0.07, amp)
    b = sweep(85, 42, 0.22, 0.06, amp * 0.6)
    out = np.zeros(int(SR * 0.42)); out[:len(a)] += a; out[int(SR * 0.17):int(SR * 0.17) + len(b)] += b
    return out


def whoosh(dur=0.42, amp=0.32):
    t = _t(dur)
    env = np.sin(np.pi * (t / dur) ** 1.6) ** 2
    return amp * noise(dur, 10) * env


def impact(amp=1.0):           # reveal hit
    body = sweep(120, 38, 0.7, 0.22, amp)
    crack = noise(0.12, 3) * np.exp(-_t(0.12) / 0.025) * amp * 0.35
    body[:len(crack)] += crack
    return body


def ding(amp=0.35):            # correct answer
    return mix(tone(1046, 0.7, 0.30, amp), tone(1568, 0.7, 0.22, amp * 0.6), tone(2093, 0.5, 0.12, amp * 0.25))


def tape_stop(amp=0.38):       # gameplay freezes
    out = sweep(520, 55, 0.32, 0.16, amp)
    n = noise(0.05, 4) * 0.15
    out[:len(n)] += n
    return out


def counter_blip(amp=0.13):
    return tone(2600, 0.025, 0.006, amp)


class Track:
    def __init__(self, dur):
        self.buf = np.zeros(int(SR * dur) + 1)

    def add(self, at, sig):
        i = int(SR * at)
        if i >= len(self.buf): return
        sig = sig[: len(self.buf) - i]
        self.buf[i:i + len(sig)] += sig

    def write(self, path):
        x = np.tanh(self.buf * 0.9)                    # soft limiter, no harsh clipping
        pcm = (np.clip(x, -1, 1) * 0.89 * 32767).astype(np.int16)
        st = np.repeat(pcm[:, None], 2, axis=1).tobytes()
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(st)
