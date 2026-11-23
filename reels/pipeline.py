#!/usr/bin/env python3
"""CS2 highlight -> Instagram reel pipeline (FACEIT x Allstar public highlights).

For every player in players.json:
  * reads the public highlight list (FACEIT web API), skips clips already done (state.json)
  * keeps strong clips (clutch / 3K+ / ace / wallbang / quickscope, or many views)
  * downloads the clip MP4, builds a 1080x1920 reel with our analysis overlay, writes a caption
Output (v3, 2026-11-23): queue/<rubric>/<nick>_<clip>/ with reel.mp4, caption.txt, spec.json, source.json
  rubrics: elo (Guess the Elo) · clutch (Clutch or choke?) · legit (Legit or cheating?) · pure (no-text edit)
  Each clip goes to the eligible rubric whose queue is shortest, so all four stay stocked for eloquiz_drop.py.
  Player permission is no longer required (Javlon, 2026-11-23): every clip is credited and tagged instead.
Never posts anything. Usage: python3 pipeline.py [--top 10] [--spread] [--target 4] [--max-total 8] [--max-per-player 1]
  Famous pros (pros.py) never get Guess the Elo; they go to pure / legit / clutch.
  --spread adds random players from EU ladder positions 20k..1.5M (level 10 down to 4) so answers vary.
  --target N stops once every rubric has >= N reels waiting.   --requeue rebuilds old needs_permission/ reels.
"""
from __future__ import annotations

import datetime as dt, json, re, shutil, subprocess, sys, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from clipcut import clean_end  # noqa: E402
from pros import famous_pro  # noqa: E402

HERE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/131 Safari/537.36"
OUTRO_SEC = 8.4          # Allstar end card length, cut it off
MAPS = ["Dust 2", "Dust2", "Mirage", "Inferno", "Nuke", "Ancient", "Anubis", "Vertigo", "Overpass", "Cache", "Train", "Office", "Italy"]
SPREAD = [20000, 100000, 250000, 500000, 900000, 1500000]   # EU ladder positions: level 10 down to level 4
STRONG = re.compile(r"clutch|\b[3-5]k\b|ace|wallbang|quickscope|no.?scope|jump|deagle", re.I)


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def parse_title(title: str) -> dict:
    t = title.strip()
    m = re.match(r"(\w[\w ]*?)\s+(.*)", t)
    mapname = next((m for m in MAPS if re.search(rf"\b{m}\b", t, re.I)), "")
    up = t.upper()
    kills = re.search(r"\b([2-5])K\b", up)
    clutch = re.search(r"1V([1-5])", up)
    weapon = None
    for w in ("AK-47", "M4A4", "M4A1", "AWP", "DEAGLE", "DESERT EAGLE", "USP", "GLOCK", "SSG", "SCOUT", "P250",
              "MP9", "MAC-10", "FAMAS", "GALIL", "AUG", "SG 553", "TEC-9", "FIVE-SEVEN", "CZ75", "P90", "UMP"):
        if w in up:
            weapon = w.title() if w not in ("AWP", "M4A4", "M4A1", "AK-47", "SSG", "USP", "MP9", "P90", "UMP", "P250") else w
            break
    return {"map": mapname, "kills": int(kills.group(1)) if kills else (5 if "ACE" in up else None),
            "clutch_vs": int(clutch.group(1)) if clutch else None, "weapon": weapon,
            "wallbang": "WALLBANG" in up, "quickscope": "QUICKSCOPE" in up or "NOSCOPE" in up,
            "match_point": "MATCH POINT" in up}


def grade(p: dict) -> str:
    k, c = p["kills"] or 0, p["clutch_vs"] or 0
    if k >= 5: return "S"
    if c >= 3 or k == 4: return "A+"
    if c >= 1 and k >= 3: return "A"
    if c >= 2 or k == 3: return "A-"
    if c == 1 or k == 2: return "B+"
    return "B"


def overlay_text(nick: str, elo, level, clip: dict, p: dict, permitted: bool) -> dict:
    head = []
    if p["clutch_vs"]: head.append(f"1v{p['clutch_vs']} CLUTCH")
    if p["kills"]: head.append("ACE" if p["kills"] == 5 else f"{p['kills']}K")
    if p["wallbang"]: head.append("WALLBANG")
    if p["quickscope"]: head.append("QUICKSCOPE")
    title = " · ".join(head) or clip["title"].upper()
    sub = " · ".join(x for x in [p["map"], f"round {clip.get('round')}" if clip.get("round") else "", p["weapon"] or "",
                                 f"{nick} ({elo:,} Elo)" if elo else nick] if x)
    notes = []
    if p["clutch_vs"]: notes.append(f"Last alive against {p['clutch_vs']}, wins the round")
    if p["kills"]: notes.append(f"{p['kills']} kills in a single round" if p["kills"] < 5 else "Takes down all 5 enemies alone")
    if p["wallbang"]: notes.append("Kill through the wall: pre-aim on a common spot")
    if p["quickscope"]: notes.append("Quickscope: shot before the full scope-in")
    if p["match_point"]: notes.append("Happens at match point: maximum pressure")
    if p["weapon"] and len(notes) < 3: notes.append(f"Weapon: {p['weapon']}")
    while len(notes) < 3: notes.append("Watch the crosshair: already on head height")
    lvl = f"FACEIT level {level} · " if level else ""
    return {"title": title, "sub": sub, "grade": f"ROUND GRADE: {grade(p)}", "notes": notes[:3],
            "stats": f"{lvl}{elo:,} Elo · {int(clip.get('views') or 0):,} views on FACEIT" if elo else f"{int(clip.get('views') or 0):,} views on FACEIT",
            "credit": (f"Clip: {nick} on FACEIT · via Allstar" if permitted else f"PREVIEW · ask {nick} before posting")}


COUNTRY = {"no": "Norway", "de": "Germany", "fr": "France", "pl": "Poland", "ru": "Russia", "ua": "Ukraine", "uz": "Uzbekistan",
           "kz": "Kazakhstan", "se": "Sweden", "dk": "Denmark", "fi": "Finland", "gb": "UK", "us": "USA", "br": "Brazil", "tr": "Turkey"}


def elo_options(elo: int, seed: str) -> tuple[list[str], int]:
    """3 plausible options in ascending order; the right one is the closest. Its position varies (A/B/C)."""
    import random
    rnd = random.Random(seed)
    ans = int(round(elo, -2))
    lo, hi = 300, 5800                                  # realistic FACEIT range (EU #1 is ~5,800)
    for _ in range(200):
        layout = rnd.choice(["low", "mid", "high"])     # where the answer sits among the 3
        g1, g2 = rnd.choice([400, 500, 600, 700, 800]), rnd.choice([500, 600, 700, 900, 1100])
        if layout == "low": vals = [ans, ans + g1, ans + g1 + g2]
        elif layout == "mid": vals = [ans - g1, ans, ans + g2]
        else: vals = [ans - g1 - g2, ans - g1, ans]
        if all(lo <= v <= hi for v in vals): break
    else:
        vals = sorted({max(lo, min(hi, ans + d)) for d in (-1300, -600, 0)}) if ans > 2000 else [ans, ans + 600, ans + 1300]
    vals = sorted(vals)
    return [f"{v:,}" for v in vals], vals.index(ans)


def quiz_spec(nick, elo, level, country, rank_pos, region, clip, p, permitted) -> dict:
    opts, ai = elo_options(elo or 2000, clip["id"])
    head = []
    if p["clutch_vs"]: head.append(f"1v{p['clutch_vs']} clutch")
    if p["kills"]: head.append("ACE" if p["kills"] == 5 else f"{p['kills']}K")
    if p["wallbang"]: head.append("wallbang")
    what = " ".join(head) or "highlight"
    rnd_lines = [" · ".join(x for x in [what.upper(), p["weapon"] or ""] if x),
                 f"{p['map']} round {clip.get('round', '?')}  ·  Grade: {grade(p)}"]
    reg = {"EU": "EUROPE", "NA": "NORTH AMERICA", "SA": "SOUTH AMERICA", "SEA": "ASIA", "OCE": "OCEANIA"}.get(region, region)
    rank = f"#{rank_pos:,} ON FACEIT IN {reg}" if rank_pos else f"FACEIT LEVEL {level}"
    exact = opts[ai].replace(",", "") == str(elo)
    return {"rubric": "elo", "hook": "GUESS HIS ELO", "hook_sub": f"{what} on {p['map'] or 'FACEIT'} · answer at the end",
            "options": opts, "answer_index": ai,
            "reveal": {"answer_line": f"ANSWER: {'ABC'[ai]}) {opts[ai]}" + ("" if exact else " (closest)"),
                       "big": f"{elo:,} ELO" if elo else "?", "count_to": elo, "count_suffix": " ELO", "rank": rank,
                       "who": "  ·  ".join(x for x in [nick, f"Level {level}" if level else "", COUNTRY.get(country or "", (country or "").upper())] if x),
                       "round": rnd_lines, "match": f"{int(clip.get('views') or 0):,} views on FACEIT",
                       "credit": (f"Clip: {nick} on FACEIT  ·  captured by Allstar" if permitted else f"PREVIEW: ask {nick} before posting")}}


RUBRICS = ["elo", "clutch", "pure"]          # legit-or-cheating dropped 2026-11-23: the answer was always LEGIT
SAFE_MP4 = re.compile(r"https://media2\.allstar\.gg/[0-9a-f]{12,40}/clips/[0-9a-f]{12,40}\.mp4")
SAFE_UID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
QUEUE = HERE / "queue"
PY = sys.executable
CREDIT = "Clip: {nick} on FACEIT  ·  captured by Allstar"
FOOT = ("🎥 Clip: {nick} on @faceitcom · captured by @playsharestar\nFollow @eloquiz: a new CS2 quiz every day.\n"
        "🎵 Music: Sascha Ende, ende.app (CC BY 4.0)")


def queued(rubric: str) -> int:
    d = QUEUE / rubric
    return sum(1 for f in d.iterdir() if (f / "reel.mp4").exists()) if d.exists() else 0


def tags(p: dict, last: str) -> str:
    """Instagram allows max 5 hashtags per Reel."""
    play = "#clutch" if p["clutch_vs"] else "#ace" if p["kills"] == 5 else "#cs2highlights"
    mp = f"#{p['map'].lower().replace(' ', '')}" if p["map"] else "#cs2clips"
    return " ".join(dict.fromkeys(["#cs2", "#faceit", play, mp, last]))


def what_of(p: dict) -> str:
    head = []
    if p["clutch_vs"]: head.append(f"1v{p['clutch_vs']} clutch")
    if p["kills"]: head.append("ACE" if p["kills"] == 5 else f"{p['kills']}K")
    if p["wallbang"]: head.append("wallbang")
    if p["quickscope"]: head.append("quickscope")
    return " ".join(head) or "highlight"


def caption(nick: str, clip: dict, p: dict, txt: dict) -> str:
    what = txt["hook_sub"].split(" · ")[0]
    weapon = f" with the {p['weapon']}" if p["weapon"] else ""
    opts = "   ".join(f"{'ABC'[i]}) {o}" for i, o in enumerate(txt["options"]))
    return (f"Guess his FACEIT Elo 👀 {what}{weapon}.\n\n🗳 {opts}\n"
            f"Comment A, B or C before the reveal 👇 (answer at the end, no cheating)\n\n"
            + FOOT.format(nick=nick) + "\n\n" + tags(p, "#guesstheelo"))


def eligible(info: dict, p: dict, clip: dict) -> list[str]:
    out = ["pure"] if (p["kills"] or 0) >= 3 or p["clutch_vs"] or p["wallbang"] or p["quickscope"] \
        or int(clip.get("views") or 0) >= 1000 else []
    if p["clutch_vs"]:
        out.append("clutch")
    if not info["pro"]:
        out.append("elo")            # pro rule: famous pros never go into Guess the Elo
    return out


def player_info(nick: str) -> dict:
    u = get(f"https://www.faceit.com/api/users/v1/nicknames/{urllib.parse.quote(nick, safe='')}")["payload"]
    if not SAFE_UID.fullmatch(str(u.get("id", ""))):
        raise ValueError(f"{nick}: unexpected FACEIT id")
    g = (u.get("games") or {}).get("cs2") or {}
    region = g.get("region") or "EU"
    rank_pos = None
    for _ in range(3):
        try:
            rank_pos = get(f"https://www.faceit.com/api/ranking/v1/globalranking/cs2/{region}/{u['id']}").get("payload"); break
        except Exception as ex:
            print(f"{nick}: ranking lookup retry ({ex})"); __import__("time").sleep(3)
    return {"nick": nick, "uid": u["id"], "elo": g.get("faceit_elo"), "level": g.get("skill_level"), "region": region,
            "country": u.get("country"), "rank_pos": rank_pos, "pro": famous_pro(nick)}


def build(rubric: str, info: dict, c: dict, mp4: str) -> Path:
    nick, p = info["nick"], parse_title(c["title"])
    folder = QUEUE / rubric / f"{nick}_{c['id'][-6:]}"
    folder.mkdir(parents=True, exist_ok=True)
    raw = folder / "clip.mp4"
    if not SAFE_MP4.fullmatch(mp4):                              # only Allstar's own clip CDN, https only
        raise ValueError(f"unexpected clip URL: {mp4[:80]}")
    subprocess.run(["curl", "-sSL", "--proto", "=https", "--proto-redir", "=https", "--max-filesize", "80000000",
                    "--max-time", "180", "-A", UA, "-o", str(raw), mp4], check=True, timeout=300)
    credit = CREDIT.format(nick=nick)
    try:
        if rubric == "pure":   # v2: /brag-slim edit (beat-synced kills, hook, counter, music); old plain edit as fallback
            track = "1 9 10 11 12".split()[int(c["id"][-4:], 16) % 5]
            args = [PY, str(HERE / "make_brag_edit.py"), str(raw), str(folder), "--nick", nick, "--title", c["title"],
                    "--map", p["map"] or "", "--weapon", p["weapon"] or "", "--track", track]
            if p["kills"]: args += ["--kills", str(p["kills"])]
            try:
                subprocess.run(args, check=True)
            except subprocess.CalledProcessError:
                subprocess.run([PY, str(HERE / "make_edit_reel.py"), str(raw), str(folder / "reel.mp4"),
                                f"{nick} on FACEIT  ·  captured by Allstar", str(round(max(0.0, clean_end(str(raw)) - 16), 2)), "auto"], check=True)
            kills_txt = {2: "2 kills", 3: "3 kills", 4: "4 kills", 5: "5 kills"}.get(p["kills"] or 0, "One round")
            cap = (f"{kills_txt}{', one round' if p['kills'] else ''} on {p['map'] or 'FACEIT'}"
                   f"{' with the ' + p['weapon'] if p['weapon'] else ''}{', at match point' if p['match_point'] else ''}. 🔊\n\nRate it 1-10 in the comments 👇\n\n"
                   + FOOT.format(nick=nick) + "\n\n" + tags(p, "#cs2clips"))
            spec = {"rubric": "pure", "builder": "brag"}
        else:
            end = clean_end(str(raw))
            lvl = f"Level {info['level']}" if info["level"] else ""
            elo_s = f"{info['elo']:,} Elo" if info["elo"] else ""
            if rubric == "elo":
                spec = quiz_spec(nick, info["elo"], info["level"], info["country"], info["rank_pos"], info["region"], c, p, True)
                spec["start"] = round(max(0.0, end - 18), 2)
                cap = caption(nick, c, p, spec)
            elif rubric == "clutch":   # kill-count quiz: Allstar only clips WON clutches, so we ask how many kills he ends with
                K = p["kills"] or 1
                vals = killcount_options(K, c["id"])
                labels = [kill_label(v) for v in vals]
                ai = vals.index(K)
                vs = p["clutch_vs"]
                spec = {"rubric": "clutch", "mode": "killcount", "kills_total": K, "hook": "HOW MANY KILLS?",
                        "hook_sub": f"1v{vs} on {p['map'] or 'FACEIT'} · watch closely",
                        "question": "HOW MANY KILLS IN THIS ROUND?", "options": labels, "answer_index": ai,
                        "reveal": {"answer_line": f"ANSWER: {'ABC'[ai]}) {labels[ai]}", "big": kill_label(K, big=True),
                                   "rank": f"#{info['rank_pos']:,} ON FACEIT" if info["rank_pos"] else f"FACEIT LEVEL {info['level']}",
                                   "who": "  ·  ".join(x for x in [nick, lvl, elo_s] if x),
                                   "round": [f"1v{vs} clutch won  ·  {K} kill{'s' if K > 1 else ''}",
                                             "  ·  ".join(x for x in [p["weapon"] or "", f"{p['map']} round {c.get('round', '?')}"] if x)],
                                   "match": f"{int(c.get('views') or 0):,} views on FACEIT", "credit": credit}}
                opts = "   ".join(f"{'ABC'[i]}) {l}" for i, l in enumerate(labels))
                cap = (f"1v{vs} on {p['map'] or 'FACEIT'}{' with the ' + p['weapon'] if p['weapon'] else ''}. "
                       f"How many kills does he finish the round with? 🤔\n\n🗳 {opts}\n"
                       f"Comment A, B or C, then watch the rest 👇\n\n" + FOOT.format(nick=nick) + "\n\n" + tags(p, "#cs2clips"))
            else:  # legit
                spec = {"rubric": "legit", "start": round(max(0.0, end - 18), 2), "hook": "LEGIT OR CHEATING?",
                        "hook_sub": f"{what_of(p)} on {p['map'] or 'FACEIT'} · decide before the end",
                        "options": ["LEGIT", "CHEATING"], "answer_index": 0,
                        "reveal": {"answer_line": "ANSWER: A) LEGIT", "big": "100% LEGIT",
                                   "rank": f"#{info['rank_pos']:,} ON FACEIT" if info["rank_pos"] else f"FACEIT LEVEL {info['level']}",
                                   "who": "  ·  ".join(x for x in [nick, lvl, elo_s] if x),
                                   "round": ["Played on FACEIT with FACEIT Anti-Cheat",
                                             f"Just aim: {what_of(p)}" + (f" with the {p['weapon']}" if p["weapon"] else "")],
                                   "match": f"{int(c.get('views') or 0):,} views on FACEIT", "credit": credit}}
                cap = (f"Legit or cheating? 🤔 {what_of(p)} on {p['map'] or 'FACEIT'}"
                       f"{' with the ' + p['weapon'] if p['weapon'] else ''}. Your verdict before the reveal.\n\n"
                       f"🗳 A) LEGIT   B) CHEATING\nComment A or B 👇 (answer at the end)\n\n" + FOOT.format(nick=nick)
                       + "\n\n" + tags(p, "#cs2clips"))
            (folder / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1))
            track = "1 9 10 11 12".split()[int(c["id"][-4:], 16) % 5]
            args = [PY, str(HERE / "make_quiz_brag.py"), str(raw), str(folder), str(folder / "spec.json"), "--track", track]
            if p["kills"]: args += ["--kills", str(p["kills"])]
            try:                                                 # v3: /brag beat-synced quiz; v2 builder as fallback
                subprocess.run(args, check=True)
            except subprocess.CalledProcessError:
                subprocess.run([PY, str(HERE / "make_quiz_reel.py"), str(raw), str(folder / "reel.mp4"), str(end),
                                str(folder / "spec.json")], check=True)
        (folder / "caption.txt").write_text(cap)
        (folder / "source.json").write_text(json.dumps({"nick": nick, "rubric": rubric, "clip": c, "mp4": mp4, "info": info,
                                                        "built": dt.datetime.now().isoformat(timespec="minutes")},
                                                       ensure_ascii=False, indent=1))
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        raw.unlink(missing_ok=True)
    return folder


def recent_nicks(days: int = 7) -> set[str]:
    """Players with a reel waiting in the queue or dropped in the last `days` days: skip them, spread the faces."""
    out = set()
    for src in QUEUE.glob("*/*/source.json"):
        out.add(json.loads(src.read_text()).get("nick", "").lower())
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    for src in (HERE / "dropped").glob("*/*/source.json"):
        if src.parent.parent.name >= cutoff:
            out.add(json.loads(src.read_text()).get("nick", "").lower())
    return out - {""}


def kill_label(v: int, big: bool = False) -> str:
    return "ACE" if v >= 5 else (f"{v} KILL" if v == 1 else f"{v} KILLS")


def killcount_options(k: int, seed: str) -> list[int]:
    """3 kill counts (1-5) that include the real one; where it sits (A/B/C) varies with the clip."""
    import random
    rnd = random.Random(seed)
    for _ in range(50):
        layout = rnd.choice(["low", "mid", "high"])
        vals = {"low": [k, k + 1, k + rnd.choice([2, 3])], "mid": [k - 1, k, k + 1],
                "high": [k - rnd.choice([2, 3]), k - 1, k]}[layout]
        if all(1 <= v <= 5 for v in vals):
            return sorted(set(vals))
    return sorted({1, 2, 3, 4, 5} - {v for v in (1, 5) if v != k})[:3] if k in (2, 3, 4) else ([1, 2, 3] if k == 1 else [3, 4, 5])


def pick_rubric(options: list[str]):
    order = {r: i for i, r in enumerate(["clutch", "pure", "elo"])}  # scarcer rubrics win ties
    return min(options, key=lambda r: (queued(r), order[r])) if options else None


def requeue(state: dict) -> list[str]:
    """Rebuild the old needs_permission/ reels (they carry a 'PREVIEW: ask X' credit) into queue/."""
    made, cache = [], {}
    for src in sorted((HERE / "needs_permission").glob("*/source.json")):
        s = json.loads(src.read_text()); nick, c = s["nick"], s["clip"]
        if nick.lower() in recent_nicks():
            print(f"requeue {nick}: already has a reel queued, dropping duplicate player"); shutil.rmtree(src.parent); continue
        try:
            info = cache.get(nick) or player_info(nick); cache[nick] = info
            r = pick_rubric(eligible(info, parse_title(c["title"]), c)) or ("pure" if info["pro"] else "elo")
            f = build(r, info, c, s["mp4"])
            shutil.rmtree(src.parent)
            state[c["id"]] = {"nick": nick, "date": dt.date.today().isoformat(), "folder": str(f.relative_to(HERE)), "rubric": r}
            made.append(f"{r}: {nick} {c['title']}")
        except Exception as ex:
            print(f"requeue {nick} failed: {ex}")
    return made


def main() -> None:
    a = sys.argv
    max_pp = int(a[a.index("--max-per-player") + 1]) if "--max-per-player" in a else 1
    min_views = int(a[a.index("--min-views") + 1]) if "--min-views" in a else 300
    target = int(a[a.index("--target") + 1]) if "--target" in a else None
    max_total = int(a[a.index("--max-total") + 1]) if "--max-total" in a else 99
    state = load(HERE / "state.json", {})
    made = []
    if "--requeue" in a:
        made += requeue(state)
        (HERE / "state.json").write_text(json.dumps(state, indent=1))
    players = [pl for pl in load(HERE / "players.json", []) if pl.get("permission") != "no"]
    known = {pl["nick"].lower() for pl in players}
    if "--top" in a:
        try:
            for row in get(f"https://www.faceit.com/api/ranking/v1/globalranking/cs2/EU?limit={int(a[a.index('--top') + 1])}")["payload"]:
                nk = row["user"]["nickname"]
                if nk.lower() not in known:
                    known.add(nk.lower()); players.append({"nick": nk})
        except Exception as ex:
            print("top ladder lookup failed:", ex)
    if "--spread" in a:
        import random
        rnd = random.Random(dt.datetime.now().isoformat())
        for pos in SPREAD:
            try:
                rows = get(f"https://www.faceit.com/api/ranking/v1/globalranking/cs2/EU?limit=20&position={rnd.randint(pos, pos + 20000)}")["payload"]
            except Exception as ex:
                print("spread lookup failed:", ex); continue
            for row in rnd.sample(rows, min(3, len(rows))):
                nk = row["user"]["nickname"]
                if nk.lower() not in known:
                    known.add(nk.lower()); players.append({"nick": nk})
    import random as _r
    _r.Random(dt.datetime.now().isoformat()).shuffle(players)
    today = dt.date.today().isoformat()
    built = 0
    busy = recent_nicks()
    for pl in players:
        if built >= max_total or (target and all(queued(r) >= target for r in RUBRICS)):
            break
        nick = pl["nick"]
        if nick.lower() in busy:
            continue                      # one player, one reel at a time: no repeats while they're queued / recent
        try:
            info = player_info(nick)
            clips = get(f"https://www.faceit.com/api/allstar/v1/games/cs2/users/{info['uid']}/clips?sort=latest&offset=0&expired=false&limit=12")["clips"]
        except Exception as ex:
            print(f"{nick}: lookup failed ({ex})"); continue
        done = 0
        for c in clips:
            if done >= max_pp or built >= max_total: break
            if c["id"] in state or c.get("status") != "CLIP_STATUS_PROCESSED": continue
            if not (STRONG.search(c["title"]) or int(c.get("views") or 0) >= min_views): continue
            mp4 = re.sub(r"/thumbs/([0-9a-f]+)_thumb\.jpg$", r"/clips/\1.mp4", c.get("thumbnail_url") or "")
            if not mp4.endswith(".mp4"): continue
            opts = eligible(info, parse_title(c["title"]), c)
            if target:   # only build for rubrics that are still short
                opts = [r for r in opts if queued(r) < target] or []
            r = pick_rubric(opts)
            if not r: continue
            try:
                f = build(r, info, c, mp4)
            except Exception as ex:
                print(f"{nick}: build failed ({ex})"); continue
            state[c["id"]] = {"nick": nick, "date": today, "folder": str(f.relative_to(HERE)), "rubric": r}
            (HERE / "state.json").write_text(json.dumps(state, indent=1))
            made.append(f"{r}: {nick} {c['title']} -> {f.relative_to(HERE)}")
            busy.add(nick.lower())
            done += 1; built += 1
    print("\n".join(made) or "No new clips.")
    print("queue:", {r: queued(r) for r in RUBRICS})


if __name__ == "__main__":
    main()
