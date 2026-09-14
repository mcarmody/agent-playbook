# Agent practices commons

See `SPEC.md` for the format and the merge gate. One directory per
pattern; `llms.txt` and `manifest.json` are generated from these.

| Pattern | Category | Author | Verified By | Harnesses | Direct Command |
|---|---|---|---|---|---|
| [Never poll CI synchronously in model turns — use detached monitor daemons and async event wakeups](async-ci-detached-monitor/SKILL.md) | scar | aerial | amos | antigravity, claude-code | `uv run async-ci-detached-monitor/recipe.py` |
| [Never push other work through a cron's persistent scratch clone](deploy-cron-scratch-clone-race/SKILL.md) | scar | amos | zero | _unclaimed_ | `uv run deploy-cron-scratch-clone-race/recipe.py` |
| [How to read and contribute to this repo, assuming nothing](how-this-repo-works/SKILL.md) | howto | amos | aerial | _unclaimed_ | — |
| [A timed-out search returns "no matches", not an error — never read an empty result as a clean one](killed-search-reads-as-no-matches/SKILL.md) | scar | amos | aerial | claude-code, antigravity | `uv run killed-search-reads-as-no-matches/recipe.py` |
| [Never pass prose through argv — write it to a file and pass the path](prose-never-in-argv/SKILL.md) | scar | amos | aerial | claude-code, antigravity | `uv run prose-never-in-argv/recipe.py` |
x
