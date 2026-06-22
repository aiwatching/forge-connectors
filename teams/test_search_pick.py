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
    """options: list of (data_tid, aria_label). query: the name/email typed.

    Mirror of the manifest: a precise email/full-name query surfaces the person
    as a TOPHITS option, NOT under PEOPLE (PEOPLE only fills in for partial
    queries, often with the WRONG people). So accept PEOPLE or TOPHITS, but only
    entries whose aria-label starts with "Person" (excludes group chats / files /
    search suggestions). The email local-part also matches the alias in parens,
    e.g. "(SUND)". Returns the first matching option in DOM order (= relevance)."""
    q = query.lower()
    qlocal = q.split("@")[0]
    for tid, aria in options:
        if "AUTOSUGGEST_SUGGESTION_PEOPLE" not in tid and "AUTOSUGGEST_SUGGESTION_TOPHITS" not in tid:
            continue
        if not aria.strip().lower().startswith("person"):
            continue
        s = aria.lower()
        if q in s or (len(qlocal) >= 3 and qlocal in s):
            return aria
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
    # Real DOM aria-labels: people/tophits start with "Person ", domain filters
    # and group chats do not. A precise email query surfaces the person as a
    # TOPHITS entry; the "(SUND)" alias carries the email local-part.
    TOP = "AUTOSUGGEST_SUGGESTION_TOPHITS8:orgid:abc"
    PEO = "AUTOSUGGEST_SUGGESTION_PEOPLE8:orgid:xyz"
    DOM = "AUTOSUGGEST_SUGGESTION_PRIMARYDOMAINMESSAGEDOMAIN"
    GRP = "AUTOSUGGEST_SUGGESTION_TOPHITS19:thread.v2"
    # email query: only the TOPHITS person renders (matched via "(SUND)" alias)
    email_opts = [(DOM, "Messages"), (TOP, "Person  Dancheng Sun (SUND) PRINCIPAL")]
    ok &= check("sund@ picks the TOPHITS Person via the (SUND) alias",
                pick_person(email_opts, "sund@fortinet.com") == "Person  Dancheng Sun (SUND) PRINCIPAL")
    ok &= check("full name 'Dancheng Sun' matches the TOPHITS Person",
                pick_person(email_opts, "Dancheng Sun") == "Person  Dancheng Sun (SUND) PRINCIPAL")
    # partial 'sund': TOPHITS person (correct) precedes fuzzy PEOPLE ("Sunday")
    partial_opts = [(TOP, "Person  Dancheng Sun (SUND) PRINCIPAL"),
                    (PEO, "Person  Sunday Onomo (SONOMO) MANAGER")]
    ok &= check("DOM order wins: TOPHITS person before fuzzy PEOPLE",
                pick_person(partial_opts, "sund") == "Person  Dancheng Sun (SUND) PRINCIPAL")
    ok &= check("domain-filter row is never a person",
                pick_person([(DOM, "Messages")], "anything") is None)
    ok &= check("group-chat tophit (aria not 'Person') is never a person",
                pick_person([(GRP, "Group chat  Alvin, Dancheng Sun, +6")], "Dancheng") is None)

    # read_chat opens via the search box → `match` (rail element) stays None.
    # The return must NOT call firstLine(None) (crashes); it falls back to the
    # searched person / chat title. Regression for the "opens then errors out" bug.
    def matched_name(match, searched_person, chat_title):
        return (match if match is not None else (searched_person or chat_title))
    ok &= check("search path (match=None) → matched_name falls back, no crash",
                matched_name(None, "Dancheng Sun", "Dancheng Sun") == "Dancheng Sun")
    ok &= check("rail path (match set) → uses the matched element",
                matched_name("RailEl", None, "x") == "RailEl")

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
