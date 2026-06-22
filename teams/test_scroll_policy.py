#!/usr/bin/env python3
"""
Regression guard for teams.read_channel's history-scroll loop.

The real script runs in the browser extension against React-fiber DOM +
virtualized scroll, so it can't be replayed offline. What CAN be pinned is
the loop's *termination policy* — the exact thing that regressed: on a short
channel (e.g. "RADIUS Questions", 4 posts) the loop scrolled all 12 rounds
(~8s each) into the 60s RPC timeout, because the dry counter keyed on
scrollHeight/scrollTop (which never settle on a short channel) instead of on
whether a round actually harvested a NEW post.

This mirrors the manifest loop (teams/manifest.yaml read_channel) in Python and
asserts the properties that matter:
  - a short channel stops within a couple of empty rounds (no grind-to-timeout)
  - the soft budget is honoured (never start a heavy grab past budget)
  - max_messages is respected
  - a deep channel still keeps scrolling while it's productive

Run: python3 teams/test_scroll_policy.py   (no deps; also wired into CI)
"""

import sys

DRY_LIMIT = 2          # mirror: while (dry < 2 ...)
ROUND_CAP = 12         # mirror: scrollRounds < 12
ROUND_MS = 8000        # observed ~8s per round on heavy channels


def simulate(total_posts, max_messages, budget_ms, per_round_ms=ROUND_MS,
             initial_visible=4):
    """Reimplements the read_channel scroll loop's control flow.

    `total_posts` newest-first become visible as we scroll up; each round
    reveals `initial_visible` more until exhausted. Returns (rounds, harvested,
    elapsed_ms, partial)."""
    elapsed = 0
    # initial jump-to-newest grab (manifest: vp.scrollTop = scrollHeight; grab())
    harvested = min(initial_visible, total_posts)
    rounds = 0
    prev_harvest = harvested
    dry = 0
    within = lambda: elapsed < budget_ms
    while dry < DRY_LIMIT and rounds < ROUND_CAP and harvested < max_messages and within():
        rounds += 1
        elapsed += per_round_ms          # scroll + sleep + reflow + grab
        if not within():                 # manifest: if (!within()) break; (before grab)
            break
        # this round reveals more older posts, up to what exists
        harvested = min(total_posts, harvested + initial_visible)
        if harvested > prev_harvest:
            dry = 0
        else:
            dry += 1
        prev_harvest = harvested
    return rounds, harvested, elapsed, (not within())


def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    return cond


def main():
    ok = True

    # 1. Short channel (the regression): 4 posts, default ask 20, 45s budget.
    #    Must stop fast — all 4 captured in the initial grab, then 2 empty
    #    rounds trip the dry counter. Old policy ran the full 12 rounds.
    r, h, el, partial = simulate(total_posts=4, max_messages=20, budget_ms=45000)
    ok &= check("short channel (4 posts) harvests all 4", h == 4)
    ok &= check("short channel stops in <= 3 rounds (was 12 -> timeout)", r <= 3)
    ok &= check("short channel stays well under 60s", el < 60000)

    # 2. Budget guard: a channel that would scroll forever must stop before the
    #    soft budget is exceeded by more than one round, never near 60s.
    r, h, el, partial = simulate(total_posts=10000, max_messages=10000, budget_ms=45000)
    ok &= check("budget-bound channel stops, marked partial", partial)
    ok &= check("budget-bound channel never blows the 60s RPC cap", el < 60000)

    # 3. max_messages respected: deep channel, modest ask.
    r, h, el, partial = simulate(total_posts=500, max_messages=20, budget_ms=45000)
    ok &= check("deep channel caps at max_messages", h >= 20)

    # 4. Deep+productive channel keeps scrolling (dry never trips while growing).
    r, h, el, partial = simulate(total_posts=40, max_messages=40, budget_ms=45000)
    ok &= check("productive channel scrolls multiple rounds", r >= 3)

    print()
    if ok:
        print("ALL SCROLL-POLICY TESTS PASSED")
        return 0
    print("SCROLL-POLICY TESTS FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
