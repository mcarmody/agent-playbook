---
title: Never pass prose through argv — write it to a file and pass the path
author: amos
category: scar
verified_by: null
scar_level: critical
triggers: [shell parse error, unexpected EOF, apostrophe in message, notification script, cron prompt, argv quoting, eval unexpected EOF, syntax error near unexpected token]
pr_evidence: []
---

## Problem

An agent composes a message for a human — a Discord notification, a text,
a prompt for a scheduled job — and passes it as a shell argument:

```bash
notify.sh admin "Ian's build failed (PR #46) — see `logs/ci.log`"
```

Bash reads its own syntax out of that English. The apostrophe in `Ian's`
opens a quote that never closes. The parentheses are a subshell. The
backticks are command substitution, so `logs/ci.log` is *executed*. The
errors look like this, and they name nothing useful:

```
eval: unexpected EOF while looking for matching `''
syntax error near unexpected token `('
```

This happened nine times in five weeks in one fleet. Twice it broke a
nightly cron silently — the job ran, the shell mangled the argument, and
nothing surfaced until someone noticed the notification had never arrived.

## Why it's critical

The failure is in the *content*, not the code, so it is untestable by the
usual means. The script works for months, then someone's name has an
apostrophe in it and a production job dies. Worse is the reactive fix
people reach for: stripping apostrophes out of the prose by hand. That
leaves the text reading wrong, doesn't cover parens or backticks, and
silently breaks again on the next ordinary edit — it converts a loud
failure into a quiet one.

Command substitution is the subtle case. Backticks and `$(...)` are
*expanded inside double quotes*, so the "just quote it" reflex does not
save you: a backticked path in the prose still runs as a command.

## Fix

Write the prose to a file. Pass the path.

```bash
printf '%s' "$body" > /tmp/msg.md
notify.sh admin --file /tmp/msg.md
```

Give every script that takes a human-readable message a `--file` flag, and
reach for it by default rather than as a fallback for "messages that look
risky" — you cannot reliably eyeball which ones are risky.

Two further rules that come from the same root:

- **A long prompt belongs in its own file**, loaded at call time
  (`"$(cat prompts/nightly.md)"`), not inlined into a script as a quoted
  string or a heredoc. Command substitution output is never re-parsed for
  shell metacharacters, so that form is safe — but a file the prose lives
  in permanently is safer than one an editor keeps reopening.
- **Check what a tool's `--file` actually means before assuming.** In one
  fleet, `imessage-send.sh --file` scp'd the file and sent it as a Messages
  *attachment* rather than sending its text — so every watchdog alert
  landed on the operator's phone as an unnamed tmp file. The fix was
  `imessage-send.sh "$(cat path)"`, the command-substitution form. Same
  flag name, opposite semantics.

`recipe.py` demonstrates the failure and both safe forms, and is the
quickest way to convince yourself the double-quote reflex is not enough.
