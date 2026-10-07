# AGENTS.md

A directory of reusable agent practices (failures with fixes, techniques,
basic walkthroughs). Start with `how-this-repo-works/SKILL.md`; the format and
merge gate are in `SPEC.md`.

- One kebab-case directory per entry: `SKILL.md` (frontmatter + prose),
  optionally `recipe.py` (PEP 723 header, `uv run <name>/recipe.py`) and any
  supporting files and tests.
- Check your work from the repo root: `python3 validate.py`. It requires
  `verified_by` set to someone other than `author`, so a new entry is red until
  a peer has run it. Leave it `null`; do not set it yourself.
- Never edit or commit `README.md`, `llms.txt` or `manifest.json`. CI
  regenerates them on merge to `main`, and CI fails any PR that touches them.
  `python3 generate_catalog.py` is safe to run locally to preview; revert its
  output before committing.
- Use only the standard library in recipes. Strip environment-specific paths,
  channel ids and names from anything you add.
- Default branch is `main`; open PRs against it.
