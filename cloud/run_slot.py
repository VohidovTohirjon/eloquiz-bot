#!/usr/bin/env python3
"""@eloquiz cloud slot: one reel per run, on GitHub Actions (no laptop needed).

  python cloud/run_slot.py build   [--slot N] [--dry]   -> picks + renders, writes site/ (for GitHub Pages) + work/plan.json
  python cloud/run_slot.py publish [--dry]              -> Instagram (Graph API, video_url = the Pages copy) + Telegram

Flow of one slot
  1. slot = the latest slot time that has passed (Tashkent), rubric rotates per slot and per day (time effect vs rubric effect)
  2. candidates: FACEIT EU ladder (top + random positions) + players.json; unused clips only, no player twice in 7 days
  3. Claude (Opus 5.5, low effort) sees up to 6 candidates (stats + Allstar thumbnails), picks one, writes the hook line
  4. render with make_quiz_brag.py / make_brag_edit.py (via pipeline.build)
  5. publish: Pages URL -> /media (REELS) -> wait FINISHED -> /media_publish; then Telegram (video + caption + link)
State lives in state/ and is committed back by the workflow.
Secrets (env): ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TG_CHAT_ID, IG_USER_ID, IG_ACCESS_TOKEN (each optional: missing = step skipped)
"""
from __future__ import annotations

import csv, datetime as dt, html, json, os, random, re, shutil, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REELS, STATE, SITE, WORK = ROOT / "reels", ROOT / "state", ROOT / "site", ROOT / "work"
sys.path.insert(0, str(REELS))
import pipeline as P  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=5))
SLOTS = [s.strip() for s in os.environ.get("ELOQUIZ_SLOTS", "09:30,12:30,15:30,18:30,21:00,23:30").split(",")]
NAMES = {"elo": "Guess the Elo", "clutch": "Clutch or choke?", "legit": "Legit or cheating?", "pure": "Pure edit"}
PAGES = os.environ.get("PAGES_URL", "https://vohidovtohirjon.github.io/eloquiz-bot").rstrip("/")
IG = "https://graph.instagram.com/v27.0"
THUMB_OK = re.compile(r"https://[a-z0-9.-]*allstar\.gg/[\w./-]+\.(?:jpg|jpeg|png|webp)")
MODEL = "claude-opus-5-5"


def log(*a):
    print(dt.datetime.now(TZ).strftime("%H:%M:%S"), *a, flush=True)


def load(name, default):
    p = STATE / name
    return json.loads(p.read_text()) if p.exists() else default


def save(name, data):
    STATE.mkdir(exist_ok=True)
    (STATE / name).write_text(json.dumps(data, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ slot + rubric
def current_slot(now: dt.datetime, forced: int | None):
    if forced:
        return forced
    mins = now.hour * 60 + now.minute
    past = [i for i, s in enumerate(SLOTS, 1) if int(s[:2]) * 60 + int(s[3:]) <= mins + 20]   # cron can fire a bit early/late
    return past[-1] if past else len(SLOTS)


def rubric_for(slot: int, day: dt.date) -> str:
    return P.RUBRICS[(slot - 1 + 2 * day.toordinal()) % 4]


# ------------------------------------------------------------------ candidates
def recent_nicks(posts: list[dict], days=7) -> set[str]:
    cut = (dt.datetime.now(TZ).date() - dt.timedelta(days=days)).isoformat()
    return {p["nick"].lower() for p in posts if p.get("date", "") >= cut}


def ladder_players(rnd: random.Random) -> list[str]:
    out = []
    try:
        out += [r["user"]["nickname"] for r in P.get("https://www.faceit.com/api/ranking/v1/globalranking/cs2/EU?limit=15")["payload"]]
    except Exception as ex:
        log("top ladder failed:", ex)
    for pos in P.SPREAD:
        try:
            rows = P.get(f"https://www.faceit.com/api/ranking/v1/globalranking/cs2/EU?limit=20&position={rnd.randint(pos, pos + 20000)}")["payload"]
            out += [r["user"]["nickname"] for r in rnd.sample(rows, min(4, len(rows)))]
        except Exception as ex:
            log("spread ladder failed:", ex)
    return out


def candidates(rubric: str, used: dict, busy: set[str], want=6, budget_s=420):
    rnd = random.Random(time.time())
    nicks = [p["nick"] for p in json.loads((REELS / "players.json").read_text()) if p.get("permission") != "no"]
    nicks += ladder_players(rnd)
    rnd.shuffle(nicks)
    found, other, seen, t0 = [], {}, set(), time.time()
    for nick in nicks:
        if len(found) >= want or time.time() - t0 > budget_s:
            break
        if nick.lower() in busy or nick.lower() in seen:
            continue
        seen.add(nick.lower())
        try:
            info = P.player_info(nick)
            clips = P.get(f"https://www.faceit.com/api/allstar/v1/games/cs2/users/{info['uid']}/clips"
                          "?sort=latest&offset=0&expired=false&limit=12")["clips"]
        except Exception as ex:
            log(f"{nick}: lookup failed ({ex})"); continue
        for c in clips:
            if c.get("id") in used or c.get("status") != "CLIP_STATUS_PROCESSED":
                continue
            if not (P.STRONG.search(c.get("title", "")) or int(c.get("views") or 0) >= 300):
                continue
            mp4 = re.sub(r"/thumbs/([0-9a-f]+)_thumb\.jpg$", r"/clips/\1.mp4", c.get("thumbnail_url") or "")
            if not P.SAFE_MP4.fullmatch(mp4):
                continue
            el = P.eligible(info, P.parse_title(c["title"]), c)
            cand = {"info": info, "clip": c, "mp4": mp4}
            if rubric in el:
                found.append(cand); break                        # one clip per player
            for r in el:
                other.setdefault(r, []).append(cand)
    return found, other


# ------------------------------------------------------------------ Claude picks + writes the hook
def claude_pick(rubric: str, cands: list[dict]):
    """Returns (index, hook_line or None, note). Falls back to the most-viewed clip without a key or on any error."""
    default = max(range(len(cands)), key=lambda i: int(cands[i]["clip"].get("views") or 0))
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return default, None, "no ANTHROPIC_API_KEY: most-viewed clip"
    import anthropic
    rows, content = [], []
    for i, c in enumerate(cands):
        info, clip, p = c["info"], c["clip"], P.parse_title(c["clip"]["title"])
        rows.append({"index": i, "title": clip["title"], "views": int(clip.get("views") or 0), "map": p["map"],
                     "kills": p["kills"], "clutch_vs": p["clutch_vs"], "weapon": p["weapon"], "wallbang": p["wallbang"],
                     "faceit_level": info["level"], "elo": info["elo"], "eu_rank": info["rank_pos"], "famous_pro": bool(info["pro"])})
        thumb = clip.get("thumbnail_url") or ""
        if THUMB_OK.fullmatch(thumb):
            content += [{"type": "text", "text": f"Candidate {i} thumbnail:"},
                        {"type": "image", "source": {"type": "url", "url": thumb}}]
    rules = {
        "elo": "Viewers guess the player's FACEIT Elo. Prefer plays that look impressive but whose Elo is NOT obvious "
               "(mid levels 4-9 are great surprises). NEVER mention the Elo, level or rank in the hook.",
        "clutch": "Viewers vote CLUTCH or CHOKE before the last kill. Prefer tense 1vX situations (higher X is better).",
        "legit": "Viewers vote LEGIT or CHEATING. Prefer the most unbelievable-looking aim from top players. Never "
                 "suggest the player cheats: the answer is always LEGIT.",
        "pure": "A no-text highlight edit. Prefer the most satisfying multi-kill (ace/4K, wallbangs, AWP).",
    }[rubric]
    prompt = (f"You pick the next Instagram Reel for @eloquiz, a CS2 quiz account. Rubric: {NAMES[rubric]}. {rules}\n"
              f"Candidates (JSON):\n{json.dumps(rows, ensure_ascii=False)}\n\n"
              "Pick the one most likely to get comments and full watch-through, then write hook_line: the first line of "
              "the caption, English, max 110 characters, punchy, at most one emoji, no hashtags, no @mentions, "
              "no numbers about Elo/rank, only facts from the data above (map, weapon, kills, clutch). why: one short sentence.")
    content.append({"type": "text", "text": prompt})
    schema = {"type": "object", "additionalProperties": False, "required": ["choice", "hook_line", "why"],
              "properties": {"choice": {"type": "integer"}, "hook_line": {"type": "string"}, "why": {"type": "string"}}}
    client = anthropic.Anthropic()
    kw = dict(model=MODEL, max_tokens=4000, messages=[{"role": "user", "content": content}],
              output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}})
    try:
        try:
            r = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)
        except (TypeError, anthropic.BadRequestError) as ex:              # older SDK / beta unavailable: plain request
            log("fallback-less request:", str(ex)[:120])
            r = client.messages.create(**kw)
        u = r.usage
        with (STATE / "claude_usage.csv").open("a", newline="") as f:
            csv.writer(f).writerow([dt.datetime.now(TZ).isoformat(timespec="minutes"), r.model, u.input_tokens, u.output_tokens])
        if r.stop_reason == "refusal":
            return default, None, "Claude declined: most-viewed clip"
        data = json.loads(next(b.text for b in r.content if b.type == "text"))
        i = int(data["choice"])
        if not 0 <= i < len(cands):
            return default, None, "bad index from Claude"
        hook = re.sub(r"\s+", " ", data["hook_line"]).strip()
        if len(hook) > 140 or re.search(r"\d{3,}|[#@]", hook) or "cheat" in hook.lower() and rubric != "legit":
            hook = None                                          # keep the template line if the hook breaks a rule
        return i, hook, data.get("why", "")
    except anthropic.APIError as ex:
        log("Claude API error:", str(ex)[:200])
        return default, None, "Claude API error: most-viewed clip"


# ------------------------------------------------------------------ build
def gh_output(**kv):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.writelines(f"{k}={v}\n" for k, v in kv.items())


def build(args):
    now = dt.datetime.now(TZ)
    gh_output(go="false")
    forced = int(args[args.index("--slot") + 1]) if "--slot" in args else None
    slot = current_slot(now, forced)
    key = f"{now.date()}:{slot}"
    done = load("slots.json", {})
    if done.get(key, {}).get("status") == "published" and "--force" not in args:
        log(key, "already published, nothing to do"); return
    want = rubric_for(slot, now.date())
    used, posts = load("used.json", {}), load("posts.json", [])
    found, other = candidates(want, used, recent_nicks(posts))
    rubric = want
    if not found:
        alt = max(other, key=lambda r: len(other[r]), default=None)
        if not alt:
            raise SystemExit("no clip candidates at all")
        rubric, found = alt, other[alt][:6]
        log(f"no {want} clip: using {rubric}")
    i, hook, why = claude_pick(rubric, found)
    c = found[i]
    log(f"slot {slot} {rubric}: {c['info']['nick']} · {c['clip']['title']} · {why}")
    folder = P.build(rubric, c["info"], c["clip"], c["mp4"])
    cap = (folder / "caption.txt").read_text()
    if hook:
        lines = cap.split("\n"); lines[0] = hook; cap = "\n".join(lines)
        (folder / "caption.txt").write_text(cap)
    WORK.mkdir(exist_ok=True)
    rid = f"{now:%Y%m%d}-{slot}-{uuid.uuid4().hex[:6]}"
    shutil.rmtree(SITE, ignore_errors=True); (SITE / "r").mkdir(parents=True)
    shutil.copy(folder / "reel.mp4", SITE / "r" / f"{rid}.mp4")
    if (folder / "reel_cover.jpg").exists():
        shutil.copy(folder / "reel_cover.jpg", SITE / "r" / f"{rid}.jpg")
    (SITE / "index.html").write_text("<!doctype html><title>eloquiz</title>")
    plan = {"key": key, "slot": slot, "time": SLOTS[slot - 1], "rubric": rubric, "planned": want, "rid": rid,
            "folder": str(folder), "nick": c["info"]["nick"], "clip_id": c["clip"]["id"], "title": c["clip"]["title"],
            "hook": hook, "why": why, "caption": cap}
    (WORK / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    used[c["clip"]["id"]] = {"nick": c["info"]["nick"], "date": str(now.date()), "rubric": rubric}
    save("used.json", used)
    done[key] = {"status": "built", "rid": rid}; save("slots.json", done)
    gh_output(go="true")


# ------------------------------------------------------------------ publish
def http(method, url, data=None, headers=None, timeout=60):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}")
        except Exception:
            return {"error": {"message": f"HTTP {e.code}"}}


def wait_public(url, tries=30):
    for _ in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=20) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(10)
    return False


def instagram(plan) -> tuple[str | None, str]:
    uid, tok = os.environ.get("IG_USER_ID"), os.environ.get("IG_ACCESS_TOKEN")
    if not (uid and tok):
        return None, "Instagram not connected yet: post it from your phone"
    video = f"{PAGES}/r/{plan['rid']}.mp4"
    if not wait_public(video):
        return None, "video link not public in time: post it from your phone"
    auth = {"Authorization": f"Bearer {tok}"}
    form = {"media_type": "REELS", "video_url": video, "caption": plan["caption"], "share_to_feed": "true"}
    if (SITE / "r" / f"{plan['rid']}.jpg").exists():
        form["cover_url"] = f"{PAGES}/r/{plan['rid']}.jpg"
    r = http("POST", f"{IG}/{uid}/media", form, auth)
    cid = r.get("id")
    if not cid:
        return None, f"Instagram refused the upload: {r.get('error', {}).get('message', r)}"[:300]
    for _ in range(40):                                          # up to ~10 min
        st = http("GET", f"{IG}/{cid}?fields=status_code,status", None, auth)
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            return None, f"Instagram processing {code}: {st.get('status', '')}"[:300]
        time.sleep(15)
    else:
        return None, "Instagram still processing after 10 min"
    pub = http("POST", f"{IG}/{uid}/media_publish", {"creation_id": cid}, auth)
    mid = pub.get("id")
    if not mid:
        return None, f"publish failed: {pub.get('error', {}).get('message', pub)}"[:300]
    link = http("GET", f"{IG}/{mid}?fields=permalink", None, auth).get("permalink", "")
    return link or f"media {mid}", "posted"


def telegram(plan, status_line):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not (token and chat):
        log("Telegram not configured"); return
    tg = WORK / "tg.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(SITE / "r" / f"{plan['rid']}.mp4"), "-c:v", "libx264",
                    "-preset", "slow", "-b:v", "5000k", "-maxrate", "6000k", "-bufsize", "12000k", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tg)], check=True)
    label = f"🎬 <i>@eloquiz · {plan['slot']}/{len(SLOTS)} · {plan['time']} · {NAMES[plan['rubric']]}</i>\n{status_line}\n"
    body = plan["caption"].strip()[: 1000 - len(label)]
    b = uuid.uuid4().hex
    fields = {"chat_id": chat, "parse_mode": "HTML", "supports_streaming": "true", "width": "1080", "height": "1920",
              "caption": label + f"<pre>{html.escape(body)}</pre>"}
    data = b"".join(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode() for k, v in fields.items())
    data += (f"--{b}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"reel.mp4\"\r\nContent-Type: video/mp4\r\n\r\n").encode()
    data += tg.read_bytes() + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendVideo", data=data,
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            log("telegram:", json.loads(r.read()).get("ok"))
    except urllib.error.HTTPError as e:
        log("telegram error:", e.code, e.read()[:200])


def publish(args):
    plan = json.loads((WORK / "plan.json").read_text())
    if "--dry" in args:
        link, status = None, "dry run: not posted"
    else:
        link, status = instagram(plan)
    line = f"✅ Posted: <a href=\"{html.escape(link)}\">open on Instagram</a>" if status == "posted" else f"📲 {html.escape(status)}"
    telegram(plan, line)
    done = load("slots.json", {})
    done[plan["key"]] = {"status": "published" if status == "posted" else "delivered", "rid": plan["rid"], "note": status}
    save("slots.json", done)
    posts = load("posts.json", [])
    posts.append({"date": plan["key"][:10], "slot": plan["slot"], "time": plan["time"], "rubric": plan["rubric"],
                  "planned": plan["planned"], "nick": plan["nick"], "clip_id": plan["clip_id"], "title": plan["title"],
                  "hook": plan["hook"], "why": plan["why"], "instagram": link, "status": status})
    save("posts.json", posts[-2000:])
    log("done:", status)


if __name__ == "__main__":
    {"build": build, "publish": publish}[sys.argv[1]](sys.argv[2:])
