# impact-check: production action plan

Status on 2026-10-06: `impact` v0.1.0 was tested on 8 real pull requests in 4 public repos (Python,
Java and JavaScript). It works well on Python and Java and found two real bugs. It does not work
on CommonJS-style JavaScript, and in two cases it gives misleading test results. Fix the P0 items
below before anyone relies on a report to block a merge.

## 1. What we tested

| Repo / PR | Lang | Tool verdict | After agent review | Real finding |
|---|---|---|---|---|
| spring-petclinic#2676 case-insensitive search | Java | medium 37 | **high** | Renamed repo method still used in 12 test call sites, so tests don't compile (confirmed with Maven) |
| spring-petclinic#2678 delete owner | Java | medium 34 | **medium** | New unauthenticated POST that cascade-deletes data; no CSRF. 5/5 tests pass |
| httpx#3766 base_url query fix | Python | medium 35 | **medium** | `users?x=1` gives `…/users?x=1?key=1`; base query dropped when `params=` is passed (generated test fails) |
| click#3884 show_envvar | Python | medium 48 | **low** | Safe; 65/65 pass on branch code |
| httpx#3777 async ByteStream | Python | low 28 | not reviewed | — |
| click#3880 PAGER/EDITOR split | Python | **high 81** | not reviewed | Score came from a *deleted test*, not production code (false high) |
| express#7508 res.redirect | JS | **low 0** | — | Tool saw no changed functions and no tests (blind spot) |
| express#7501 app.listen | JS | **low 0** | — | Same blind spot |

Reports: [docs/real-repo-runs/](docs/real-repo-runs/). Indexing took between 0.3 s and 4 s per
repo (50 to 141 files).

## 2. Gaps to fix

### P0: wrong answers (fix before rollout)

| # | Gap | Evidence | Fix |
|---|---|---|---|
| 1 | Sandbox tests import **base** code in `src/`-layout Python packages, because the editable install points at the main checkout | click#3884: 9 new tests "failed" against stable; with `PYTHONPATH=src` all 65 pass | In `run-tests --sandbox`, put the worktree's package roots first on `PYTHONPATH` (detect `src/`), and add a `sandbox_env` option in `impact.yaml` |
| 2 | The index reflects whatever is checked out, not the merge base. One unknown test id makes the whole batch fail | click: index built on `main`, base `stable`, so 21/21 reported "failed" with "not found" | Index from `merge_base` blobs (`git show`) or refuse when HEAD ≠ base. Drop test ids that don't exist in the sandbox and report them as `skipped` |
| 3 | JS blind spot: `obj.fn = function…`, `module.exports.x =` and `exports.x =` aren't symbols; mocha `test/*.js` files aren't tests | express: 141 files, only 127 symbols, 0 test units; two real behaviour changes scored 0/low | Treat `assignment_expression` with a function value as a definition; add `test_globs` to `impact.yaml`; detect `describe`/`it` |
| 4 | Deleted tests are scored like deleted production code | click#3880: a deleted test scored 81/high and set the overall risk | Leave `is_test` symbols out of `overall`; report "tests removed" separately |
| 5 | A deleted or renamed symbol that is still referenced gets only medium | petclinic#2676 scored 37, but it actually breaks the build | If a symbol is deleted or has a new signature and still has callers on the branch, add ≥ 40 points (this is a fact, not a judgment) |

### P1: precision and noise

| # | Gap | Fix |
|---|---|---|
| 6 | Python: `Client(...)` doesn't reach `__init__`, and property setters aren't treated as calls, so "no tests reach this code" is false (httpx) | Map class-name calls to `__init__`; index `@property`/setter access |
| 7 | Java: adding a method marks the whole class "modified" (petclinic: class 34 > new method 9) | Don't score container symbols when a nested symbol covers the changed lines |
| 8 | Parametrised tests: 24 requested vs 31 passed; JUnit names don't map back to ids | Normalise JUnit `classname::name[param]` back to the requested id and roll results up |
| 9 | Compile errors appear as N "failed" tests with "See output_tail" | Add a `build_error` status that carries the compiler message |
| 10 | Templates, `.properties`, YAML and routes are listed but ignored (the petclinic delete button) | Flag them as `config_change`; later map templates to controller routes |
| 11 | Dunder and common names explode (`__init__`: 67 definitions; `__anext__`: 68 callers truncated) | Resolve dunders by enclosing class, or skip them |
| 12 | `--base main` fails on `master` and `stable` repos | Default to `origin/HEAD` |
| 13 | `.impact/` shows up as untracked when the repo doesn't ignore it | Write to `.git/info/exclude` automatically |

### P2: hardening for production

- **Security:** `run-tests` runs code from the PR branch with `shell=True`. In CI, run it in a
  throwaway container with no secrets and no network beyond package mirrors. Never run it on a
  shared runner against untrusted forks.
- **Tests for the CLI itself:** there are none. Add a pytest suite: the demo repo, plus these 8 PRs
  pinned by SHA as regression fixtures (expected symbols, tests and risk band).
- **Packaging:** pin `tree-sitter` and `tree-sitter-language-pack`, and run CI on Python
  3.10–3.13. (A Python 3.14 venv failed at `ensurepip` locally; installing with uv and 3.12 worked.)
- **Benchmark** on the largest Taiyo monorepo (index time, facts time, test selection ratio).
- **Agent portability:** Copilot tool names vary by version. Also ship a Claude Code / headless
  variant of `agent/impact-check.agent.md`.

## 3. Deploy steps

| Phase | Work | Done when |
|---|---|---|
| **0. Fix P0** | Items 1–5, plus regression tests built from the 8 PRs above | All 8 PRs give the expected verdicts in CI |
| **1. Package** | Tag `v0.2.0`; install with `pipx install git+https://github.com/chiragb3101/impact-check@v0.2.0` (or internal PyPI); repo CI = lint + pytest + demo smoke | One-line install works on a clean Mac and on a Linux runner |
| **2. Pilot** | 1–2 Taiyo repos: commit `impact.yaml`, `impact-rules.md` and `.github/agents/impact-check.agent.md`; 3–5 developers run it on their own MRs for 2 weeks | Developers rate ≥ 70% of reports useful; false "breaks" < 10% |
| **3. CI job** | A deterministic job on every MR: `impact index && impact facts` plus the selected tests in a container; post the risk and failing tests as an MR comment, and keep the HTML as an artifact | Runs in < 5 min on pilot repos |
| **4. Agent in CI (optional)** | Run the full agent headless on MRs labelled `impact-review` | Report attached to the MR automatically |
| **5. Roll out and measure** | Add the remaining repos; track the share of high-risk MRs that later had incidents | Monthly precision review; tune team rules |

## 4. Rules and memory (added in this change)

Each person can save natural-language rules that the agent follows when preparing reports:

```bash
impact rules add "Treat any change under billing/ as at least medium risk."            # personal, all repos
impact rules add "Ignore churn in generated/." --scope local                             # personal, this repo
impact rules add "Call out new delete endpoints and their auth." --scope team            # shared, commit impact-rules.md
impact rules            # list
impact rules remove <id>
```

`impact facts` returns the rules, the agent applies them, and the report shows a **Rules applied**
section with each rule's effect. Saying "remember…" or "from now on…" in chat makes the agent save
a rule. Follow-ups: let rules be scoped to paths (`billing/**`), and send team-rule changes through
MR review.

## 5. Decisions needed

1. **Where it lives:** keep it on GitHub (`chiragb3101/impact-check`) or move it to the Taiyo
   GitLab, where the target repos and CI are?
2. **Which agent in CI:** Copilot CLI, Claude Code headless, or the deterministic facts and tests
   only?
3. **Blocking:** should a `high` verdict block merges, or only post a comment during the pilot?
4. **Pilot repos:** which 1–2 repos, and who owns `impact.yaml` for each?
