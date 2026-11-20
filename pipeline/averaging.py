"""The averaging window (issue #236): one rule for every figure that averages the household's recent months, so the look ahead's
pace (#148), the emergency fund's outgo (#150), the suggested plan and the Last month tab's category averages (#162), the year
review (#157) and the review context all stand on the same budget months and say the same count. Standalone, like before_after.py:
the standard library only, no database.

For the budget month c a figure is for (the closing month: the latest loaded month, or each month in turn for the emergency fund):
  trailing    the last TRAILING (12) loaded budget months up to and including c. This is the window unless a new normal moves it.
  new normal  a life event the household marked `new_normal` (#159), in month M <= c. Once SWITCH_AFTER (6) loaded budget months
              after M (M+1 .. c) are in, the window is the loaded months from M (the event's own month, the first of the new pattern)
              through c, and the figures say "since <event>". Before that the trailing window is kept, and the figures say when the
              switch comes: the month the sixth month after M would be loaded, counting a missing month as still to come.
  cap         once CAP (12) months have passed since M (c >= M + 12), the event no longer moves anything: the window is the trailing
              twelve again and the since-notes go. By then the trailing twelve are all after the event, unless months are missing.
With more than one marked event, the latest that has switched (and is under the cap) cuts the window; of those after it still to
switch, the one that switches first names its month (the next time the window moves). A switch that would change nothing (no loaded
month before M in the window) is not announced, nor one that would come only at or after the cap. The
before-and-after comparison's 3/6/12 months (before_after.compare) are a different thing and do not use this.

`window` returns the window and what to say about it:
  months       the budget months averaged, oldest first
  from         months[0], the first month averaged (None with no months)
  n            len(months)
  since        the cutting event's label when the window starts at a new normal, else None
  switch       the budget month the averages move to a pending new normal, else None
  switch_since that event's label; switch_event its month
`fields(prefix, w)` spreads it into a dataset row: <prefix>_from, _months, _since, _switch, _switch_since, _switch_event.
"""
from __future__ import annotations

TRAILING = 12
SWITCH_AFTER = 6
CAP = 12


def _idx(m: str) -> int:
    return int(m[:4]) * 12 + int(m[5:]) - 1


def _mon(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def _marked(e: dict) -> bool:
    return e.get("new_normal") is True and bool(e.get("m"))


def window(months, through: str | None, events: list[dict] | None) -> dict:
    """The averaging window for budget month `through` over the loaded budget `months` (any order, duplicates allowed); see the
    module docstring. With `through` None, the latest loaded month."""
    loaded = sorted(set(months))
    if through is None:
        through = loaded[-1] if loaded else None
    out = {"months": [], "from": None, "n": 0, "since": None, "switch": None, "switch_since": None, "switch_event": None}
    if not through:
        return out
    upto = [m for m in loaded if m <= through]
    trailing = upto[-TRAILING:]
    c = _idx(through)
    chosen, since = trailing, None
    pending = []
    live = sorted((e for e in events or [] if _marked(e) and e["m"] <= through and c - _idx(e["m"]) < CAP),
                  key=lambda e: (e["m"], e.get("label") or ""), reverse=True)
    for e in live:
        after = [m for m in upto if m > e["m"]]
        if len(after) >= SWITCH_AFTER:
            cut = [m for m in upto if m >= e["m"]]
            if cut != trailing:
                chosen, since = cut, e
            break
        switch = _mon(c + SWITCH_AFTER - len(after))   # a month not loaded is still to come
        if _idx(switch) - _idx(e["m"]) < CAP:   # it would switch before the cap; else it never moves anything
            pending.append((e, switch))
    # the next switch, when it would change something (the window still holds a month before its event)
    pending = min(((e, sw) for e, sw in pending if any(m < e["m"] for m in chosen)), key=lambda p: (p[1], -_idx(p[0]["m"])), default=None)
    out.update(months=chosen, n=len(chosen), **{"from": chosen[0] if chosen else None})
    if since:
        out["since"] = since.get("label")
    if pending:
        out.update(switch=pending[1], switch_since=pending[0].get("label"), switch_event=pending[0]["m"])
    return out


def fields(prefix: str, w: dict) -> dict:
    """The window as a dataset row's fields: <prefix>_from, _months, _since, _switch, _switch_since, _switch_event."""
    return {f"{prefix}_from": w["from"], f"{prefix}_months": w["n"], f"{prefix}_since": w["since"],
            f"{prefix}_switch": w["switch"], f"{prefix}_switch_since": w["switch_since"], f"{prefix}_switch_event": w["switch_event"]}
