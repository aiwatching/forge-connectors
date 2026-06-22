#!/usr/bin/env python3
"""
Regression guards for teams' two notoriously-flaky selection paths — both bugs
were caught by running the real scripts against the live DOM:

1. read_chat (search box): pick the PEOPLE suggestion that MATCHES the query
   (name or email local-part), NOT the first option. The popup shows stale /
   default recent contacts before the typed query resolves — grabbing the first
   one opened the WRONG person (e.g. sund@fortinet.com → "Jessica Jiang").

2. read_channel (rail/locate): EXACT team + EXACT channel preferred over a
   substring. "FortiNAC - Dev" is a substring of "FortiNAC - Development
   Questions" and "RADIUS" of "RADIUS Questions", so naive includes() opened
   the WRONG channel ("FortiNAC - Dev"/"RADIUS" → "RADIUS Questions").

Run: python3 teams/test_search_pick.py   (no deps; wired into CI)
"""
import sys


# ---- read_chat: people-suggestion pick (mirrors the manifest selector) ----
def pick_person(options, query):
    """options: list of (data_tid, label_text). query: the name/email typed.
    Mirror of: first PEOPLE option whose label/text contains the query or its
    email local-part (>=3 chars)."""
    q = query.lower()
    qlocal = q.split("@")[0]
    for tid, label in options:
        if not tid.startswith("AUTOSUGGEST_SUGGESTION_PEOPLE"):
            continue
        s = label.lower()
        if q in s or (len(qlocal) >= 3 and qlocal in s):
            return label
    return None


# ---- read_channel: team+channel locate scoring (mirrors locate()) ----
def locate_channel(rail, team_query, chan_query):
    """rail: list of (team_name, [channel_names]). Returns (team, channel) or None.
    exact-team preferred (fall back to includes only if no exact team); exact
    channel beats substring."""
    tq, cq = team_query.lower(), chan_query.lower()
    team_names = [t.lower() for t, _ in rail]
    has_exact_team = tq in team_names
    team_ok = (lambda n: n == tq) if has_exact_team else (lambda n: tq in n)
    best = None
    for team, chans in rail:
        if not team_ok(team.lower()):
            continue
        for ch in chans:
            cn = ch.lower()
            score = 2 if cn == cq else (1 if cq in cn else 0)
            if score and (best is None or score > best[0]):
                best = (score, team, ch)
    return (best[1], best[2]) if best else None


def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    return cond


def main():
    ok = True

    # --- read_chat person pick ---
    P = "AUTOSUGGEST_SUGGESTION_PEOPLE"
    D = "AUTOSUGGEST_SUGGESTION_PRIMARYDOMAINMESSAGEDOMAIN"
    # stale "Jessica" shown before query resolves, then the real match
    opts = [(D, "Messages"), (P + "_jess", "Jessica Jiang"),
            (P + "_sun", "Dancheng Sun (SUND) DIRECTOR")]
    ok &= check("sund@ picks Dancheng Sun (email local-part), not stale Jessica",
                pick_person(opts, "sund@fortinet.com") == "Dancheng Sun (SUND) DIRECTOR")
    ok &= check("display name 'Dancheng Sun' matches",
                pick_person(opts, "Dancheng Sun") == "Dancheng Sun (SUND) DIRECTOR")
    ok &= check("no domain-filter row is ever a person",
                pick_person([(D, "Messages")], "anything") is None)

    # --- read_channel exact-team / exact-channel ---
    rail = [
        ("FortiNAC - Development Questions", ["RADIUS Questions", "GUI Questions"]),
        ("FortiNAC - Dev", ["RADIUS", "FortiNAC - NCM"]),
    ]
    ok &= check("'FortiNAC - Dev'/'RADIUS' → exact team+channel, NOT RADIUS Questions",
                locate_channel(rail, "FortiNAC - Dev", "RADIUS") == ("FortiNAC - Dev", "RADIUS"))
    ok &= check("'FortiNAC - Development Questions'/'RADIUS Questions' → its own channel",
                locate_channel(rail, "FortiNAC - Development Questions", "RADIUS Questions")
                == ("FortiNAC - Development Questions", "RADIUS Questions"))
    # substring team only when no exact team exists
    ok &= check("no exact team → falls back to includes",
                locate_channel([("FortiNAC - Dev Team", ["General"])], "FortiNAC - Dev", "General")
                == ("FortiNAC - Dev Team", "General"))

    print()
    print("ALL SELECTION TESTS PASSED" if ok else "SELECTION TESTS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
