"""Famous-pro guard. Famous pros (or FACEIT nicks that look like one, e.g. donk666 -> donk) never go into
Guess the Elo: the answer would be obvious and we must not imply someone is a pro. They go to the Pro rubric
(pure edit / legit-or-cheating) instead. Add names to PROS when needed (lowercase, as the pro writes it)."""
import re

PROS = """donk zywoo m0nesy niko s1mple ropz frozen broky b1t im jl w0nderful aleksib sh1ro magixx zont1x chopper
xantares xertion siuhy jimpphat torzsi mezii karrigan rain twistzz elige naf yekindar stewie2k nafany electronic
perfecto hobbit ax1le interz headtr1ck kyxsan spinx flamez apex mzinho malbsmd jks hunter nexa snax xyp9x dupreeh
device blamef cadian stavn jabbi teses sjuush br0 kscerato yuurih fallen coldzera fer taco molodoy makazze nertz
tn1r brollan rez hampus plopski bit wonderful jimpphat ultimate hooxi xccurate kensi kyousuke sdy norwi zyw0o
monesy niku wonder krimbo tabsen degster boombl4 qikert buster jame fl1t fame n0rb3r7 kaze hades dycha
hallzerk jackz zywoo mantuu brehze nitr0 cerq skullz exit lucaozy ztr dav1deus biguzera latto dumau insani
""".split()


def _norm(s: str) -> str:
    s = s.lower().translate(str.maketrans("0134$7", "oieast"))
    return re.sub(r"[^a-z]", "", s)


_PRO_N = {_norm(p) for p in PROS}


def famous_pro(nick: str):
    """Return the pro name a nick matches, or None. 'donk666' -> 'donk', 'ZywOo_' -> 'zywoo'."""
    base = re.sub(r"[\W_]*\d*[\W_]*$", "", nick.lower())          # drop trailing digits/symbols
    for cand in (_norm(base), _norm(nick)):
        if cand in _PRO_N:
            return cand
    n = _norm(base)
    for p in _PRO_N:                                                  # prefixes like 'donk' + 'tv', 'faze' tags
        if len(p) >= 4 and (n.startswith(p) and len(n) - len(p) <= 3 or n.endswith(p) and len(n) - len(p) <= 5):
            return p
    return None


if __name__ == "__main__":
    for t in ["donk666", "ZywOo", "m0NESY-", "taini-", "IbraMagic", "koti666", "w0pzyy", "sh1ro_", "faze_ropz", "artem", "Art1"]:
        print(t, famous_pro(t))
