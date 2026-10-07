#!/usr/bin/env python3
"""Stop hook: "ids carry names" - a style rule enforced in code.

A reader will not recognise a bare task number, issue id or PR number. When
the drafted final reply's prose has `#N`, `PR #N`, `PRs #N and #M`,
`issue #N`, `<repo> #N` or a range `#N-#M` where an id has no name in the
same clause and was not named earlier in the same reply, this hook blocks
ONCE per turn with:

    ids carry names: <ids> appear without a name

so the model rewrites the reply with each id's name beside it.

Why mechanical: the rule lived in a prompt and was broken repeatedly.

What counts as a name (adjacent to the id):
  * a comma, colon, dash or parenthesis followed by at least two words of
    description ("PR #7, the R2 reconnect tests", "#7 (R2 reconnect tests)");
    status words and function words ("merged", "which is") do not count; or
  * a quoted title, after or immediately before the id.
An id named earlier in the same reply may stand alone afterwards. The whole
drafted reply is checked, every id separately; an id cited inside another id's
description ("PR #46, checklist 5a of #5") needs its own parenthesised or
quoted name. A range needs every endpoint named earlier.

Deliberately skipped (false positives annoy): fenced and inline code, URLs,
chat mention tokens (<@123>, <#123>), HTML entities, color hex, and
single-digit rankings such as "#1 priority" (a bare single digit is only an
id when a PR/issue word precedes it, or a verb follows: "so #3 can't land").

The three guard properties (see guard_lib.py): blocks once per turn, fails
open on any error, skips known false-positive zones.

Tests: test_stop_ids_carry_names.py.   Kill switch: IDS_CARRY_NAMES_OFF=1.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard_lib  # noqa: E402

# ---------------------------------------------------------------- stripping

_FENCE = re.compile(r"(```|~~~).*?(?:\1|\Z)", re.S)
_INLINE = re.compile(r"`[^`\n]*`")
_URL = re.compile(r"<?https?://[^\s>)\]]+>?|\bwww\.\S+")
_MENTION_TOKEN = re.compile(r"<(?:[@#&!:]|a:)[^>\n]*>")
_ENTITY = re.compile(r"&#?\w+;")


def prose_only(text: str) -> str:
    """Reply text with fenced code, inline code, URLs, chat mention tokens
    and HTML entities blanked out and markdown emphasis stars removed."""
    t = text or ""
    t = _FENCE.sub(lambda m: " " * len(m.group(0)), t)
    t = _INLINE.sub(lambda m: " " * len(m.group(0)), t)
    t = _URL.sub(lambda m: " " * len(m.group(0)), t)
    t = _MENTION_TOKEN.sub(lambda m: " " * len(m.group(0)), t)
    t = _ENTITY.sub(lambda m: " " * len(m.group(0)), t)
    return t.replace("*", " ")


# ------------------------------------------------------------------ tokens

_DASH = r"[-–—]"
# One alternation so a range is consumed whole and never seen as two ids.
_MENTION = re.compile(
    r"(?<![&/\w#<=:\-])(?:"
    r"(?P<a>\w*)#(?P<n1>\d{1,5})\s*" + _DASH + r"\s*#?(?P<n2>\d{1,5})(?![\w])"
    r"|#(?P<n>\d{1,5})(?![\w])"
    r")"
    r"|(?<![\w#])(?P<rw>PRs?|pull requests?|issues?)\s+#?(?P<r1>\d{1,5})\s+(?:through|thru|to)\s+#?(?P<r2>\d{1,5})(?![\w])",
    re.I,
)
# `myrepo#738` style: allow a word glued before '#' by matching it as group a.
_MENTION_GLUED = re.compile(r"(?<![&/#<=:\-])(?P<g>[A-Za-z][\w.-]*)#(?P<n>\d{1,5})(?![\w])")

_ANY_ID = re.compile(r"(?<![&/\w#<=:\-])#\d{1,5}(?![\w])|(?<=\w)#\d{1,5}(?![\w])")

_PR_WORDS = {"pr", "prs", "pull", "request", "requests", "issue", "issues",
             "ticket", "tickets", "task", "tasks", "bug", "bugs"}
_NOT_ID_PREFIX = {"step", "room", "chapter", "episode", "track", "apt", "apartment",
                  "unit", "lane", "seat", "table", "number", "no", "item", "line",
                  "row", "page", "column", "level", "tier", "round", "rank", "ranked",
                  "position", "place", "spot", "wave", "color", "colour", "hex",
                  "background", "fill", "border", "stroke", "slide", "figure", "fig",
                  "part", "section", "week", "day", "grade", "seed", "lot"}
# Verbs/aux after a bare single-digit "#N" that make it a reference, not a rank.
_VERBISH = {"is", "was", "are", "were", "has", "have", "had", "will", "can", "cannot",
            "can't", "cant", "could", "would", "should", "won't", "wont", "merged",
            "closed", "landed", "lands", "failed", "fails", "passes", "passed", "needs",
            "depends", "blocked", "also", "and", "or", "but", "still", "now", "then",
            "must", "may", "might", "does", "did", "doesn't", "didn't", "isn't", "wasn't",
            "got", "gets", "stays", "remains", "waits", "touches", "breaks", "fixes"}

_FUNCTION_WORDS = {
    "and", "or", "but", "so", "then", "also", "is", "are", "was", "were", "be", "been",
    "it", "its", "this", "that", "these", "those", "which", "who", "whom", "whose", "has",
    "have", "had", "will", "would", "can", "could", "should", "to", "of", "the", "a", "an",
    "in", "on", "at", "by", "for", "with", "as", "now", "just", "yet", "still", "not",
    "no", "if", "when", "while", "i", "we", "you", "he", "she", "they", "there", "here",
    "from", "into", "after", "before", "per", "via", "up", "down", "out", "off", "all",
}
_STATUS_WORDS = {
    "merged", "closed", "open", "opened", "green", "red", "done", "passing", "passed",
    "failing", "failed", "landed", "ready", "approved", "blocked", "pending", "draft",
    "stale", "conflicting", "mergeable", "clean", "rebased", "reviewed", "pushed",
    "fine", "good", "ok", "okay", "complete", "completed", "shipped", "live", "deployed",
    "queued", "running", "stuck", "waiting", "unchanged", "again", "too", "either",
}
_PREPOSITIONS = {"of", "from", "in", "for", "to", "on", "with", "via", "by", "after",
                 "before", "against", "than", "as", "like", "see", "per", "vs", "versus",
                 "under", "over", "into", "onto", "about", "at"}

_REF_PREPS = {"of", "from", "in", "for", "with", "via", "into", "onto", "against", "after", "before"}

_WORD = re.compile(r"[A-Za-z0-9][\w'’/-]*")
_SENT_END = re.compile(r"[.!?](?=\s|$)|;|\n")
_QUOTE_AFTER = re.compile(r"""^\s*[:,(\-–—]*\s*["“]([^"”\n]{2,120})["”]""")
_QUOTE_BEFORE = re.compile(r"""["”]\s*[(\[,:\-–—]?\s*(?:[A-Za-z]+\s+){0,2}$""")
_SEP_AFTER = re.compile(r"^(?P<ws>\s*)(?P<sep>[,:(]|\s[-–—]+\s|—|–)")


def _content_words(s: str) -> int:
    n = 0
    for w in _WORD.findall(s):
        lw = w.lower().strip("-'/")
        if not re.search(r"[A-Za-z]", lw):
            continue
        if lw in _FUNCTION_WORDS or lw in _STATUS_WORDS:
            continue
        n += 1
    return n


def _next_id_pos(s: str) -> int:
    m = _ANY_ID.search(s)
    return m.start() if m else len(s)


def _description_after(rest: str) -> tuple[str, str] | None:
    """If `rest` (text right after an id) starts a comma/colon/dash/paren
    description, return (sep, description-text); else None."""
    m = _SEP_AFTER.match(rest)
    if not m:
        return None
    sep = m.group("sep").strip() or m.group("sep")
    body = rest[m.end():]
    if sep == "(":
        close = body.find(")")
        return "(", body[:close] if close >= 0 else body
    end = min(_next_id_pos(body), (_SENT_END.search(body).start() if _SENT_END.search(body) else len(body)))
    return sep, body[:end]


# ---------------------------------------------------------------- detection

def _prefix_word(text: str, start: int) -> str:
    m = re.search(r"([A-Za-z][\w.-]*):?\s*$", text[:start])
    return m.group(1).lower() if m else ""


def _next_word(text: str, end: int) -> str:
    m = re.match(r"\s*([A-Za-z][\w'’]*)", text[end:])
    return m.group(1).lower() if m else ""


def _mentions(text: str):
    """Yield (start, end, ids, is_range, label, bare_single_digit_prefixless)."""
    out = []
    for m in _MENTION.finditer(text):
        if m.group("rw"):
            a, b = m.group("r1"), m.group("r2")
            out.append((m.start(), m.end(), [a, b], True, f"#{a}–#{b}", False))
        elif m.group("n1"):
            a, b = m.group("n1"), m.group("n2")
            s = m.start() + len(m.group("a") or "")
            out.append((s, m.end(), [a, b], True, f"#{a}–#{b}", False))
        elif m.group("n"):
            out.append((m.start(), m.end(), [m.group("n")], False, f"#{m.group('n')}", False))
    for m in _MENTION_GLUED.finditer(text):
        out.append((m.start("g"), m.end(), [m.group("n")], False, f"#{m.group('n')}", False))
    out.sort(key=lambda x: x[0])
    # drop overlapping duplicates
    res, last_end = [], -1
    for o in out:
        if o[0] < last_end:
            continue
        res.append(o)
        last_end = o[1]
    return res


def find_unnamed_ids(reply: str) -> list[str]:
    """Return the ids (as '#N' / '#N–#M' labels, first-seen order, deduped) that
    appear in `reply`'s prose without a name."""
    text = prose_only(reply)
    named: set[str] = set()
    unnamed: list[str] = []
    # End offset of the last mention that was named by a comma/colon/dash line:
    # an id cited inside that line ("PR #46, checklist 5a of #5") is nested.
    open_line_from = -1

    for start, end, ids, is_range, label, _ in _mentions(text):
        pw = _prefix_word(text, start)
        if pw in _NOT_ID_PREFIX:
            continue
        if not is_range and ids[0].startswith("0"):
            continue  # #000 / #007: a color or a code, never a PR number
        if not is_range and len(ids[0]) == 1:
            # Bare single digit: ranking ("#1 priority") unless PR/issue-ish
            # prefix, or a verb follows ("so #3 can't land").
            glued = start > 0 and text[start - 1].isalnum()
            if (pw not in _PR_WORDS and pw not in _REF_PREPS and not glued
                    and _next_word(text, end) not in _VERBISH):
                continue

        rest = text[end:]
        before = text[:start]
        is_named = False

        if open_line_from >= 0 and _SENT_END.search(text[open_line_from:start]):
            open_line_from = -1
        nested = open_line_from >= 0 and pw in _PREPOSITIONS

        if not is_range:
            q = _QUOTE_AFTER.match(rest)
            if q and _content_words(q.group(1)) >= 1:
                is_named = True
            elif _QUOTE_BEFORE.search(before[-100:]):
                is_named = True
            else:
                d = _description_after(rest)
                if d:
                    sep, desc = d
                    if (sep == "(" or not nested) and _content_words(desc) >= 2:
                        is_named = True
                        if sep != "(":
                            open_line_from = end

        if is_named:
            named.update(ids)
            continue
        if all(i in named for i in ids):
            continue
        if label not in unnamed:
            unnamed.append(label)
    return unnamed


# ---------------------------------------------------------------- hook main

def build_reason(ids: list[str]) -> str:
    return (f"ids carry names: {', '.join(ids)} appear without a name. Put each id's name "
            "beside it (\"PR #7, the R2 reconnect tests\"), or name it once earlier in "
            "the reply, then send the reply again.")


def decide(reply: str, payload: dict | None = None):
    """Pure decision: a block reason, or None to let the stop through."""
    ids = find_unnamed_ids(reply)
    return build_reason(ids) if ids else None


if __name__ == "__main__":
    sys.exit(guard_lib.run_guard("ids-carry-names", decide, off_env="IDS_CARRY_NAMES_OFF"))
