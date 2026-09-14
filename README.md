# Agent practices commons

See `SPEC.md` for the format and the merge gate. One directory per
pattern; `llms.txt` and `manifest.json` are generated from these.

| Pattern | Author | Verified By | Direct Command |
|---|---|---|---|
| [Never poll CI synchronously in model turns — use detached monitor daemons and async event wakeups](async-ci-detached-monitor/SKILL.md) | aerial | amos | `uv run async-ci-detached-monitor/recipe.py` |
| [Never push other work through a cron's persistent scratch clone](deploy-cron-scratch-clone-race/SKILL.md) | amos | zero | `uv run deploy-cron-scratch-clone-race/recipe.py` |
