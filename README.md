# impact-check

**Find out what your branch will break before you merge it, using the GitHub Copilot you already have.**

You ask Copilot "check feature/refund-limits". A few minutes later you get back:

- the functions you changed and the code that calls them
- a verdict for each caller (`breaks`, `safe` or `unclear`) and the reason
- results from running **only the tests that reach your change**, not the whole suite
- a new test written for any risky path that has none
- a **high / medium / low** risk rating, saved as an HTML report

It supports Java, Python, JavaScript/TypeScript and SQL migrations, in single repos and monorepos.

---

## How it fits with Copilot Enterprise

```
Developer ──► Copilot CLI  (your Copilot Enterprise seat and models)
                  │  custom agent: impact-check.agent.md
                  │  reads code, judges callers, writes tests, writes the summary
                  ▼
              impact CLI   (this package, runs locally, contains no AI)
                  │  git diff · call graph · test selection · test runs · history
                  ▼
              impact-reports/<branch>-<time>.html
```

| Part | What it is | AI? |
|---|---|---|
| **Copilot CLI + `impact-check` agent** | Does the reasoning. It uses whichever model your enterprise allows. | Yes, through your Copilot Enterprise plan |
| **`impact` CLI** | Gathers the facts: the diff, who calls what, which tests to run, file history. Every command prints JSON. | No. It gives the same answer every time and makes no network calls |

You need **no extra AI subscription and no API key**. All AI use goes through Copilot, so your
enterprise's Copilot policies, model choices and audit logs apply.

---

## What you need

| | Requirement | Check |
|---|---|---|
| ☐ | A **Copilot Enterprise or Business seat** | github.com → Settings → Copilot |
| ☐ | **Copilot CLI is enabled** by your org or enterprise admin | Run `copilot`; if it's blocked, ask your admin (step 0) |
| ☐ | **Node.js 22+** (for the npm install of Copilot CLI) | `node --version` |
| ☐ | **Python 3.10–3.12** | `python3 --version` |
| ☐ | **git**, plus the repo's own test tools (Maven/Gradle, pytest, npm) working locally | Can you run the repo's tests today? |

---

## Setup, end to end

### Step 0 · Admin, once per organization (about 10 min)

An org or enterprise owner does this on GitHub.com.

1. **Enable Copilot CLI:** Organization settings → Copilot → Policies → **Copilot CLI** → *Enabled*.
2. **Enable the models** you want people to use: Copilot → Policies → Models. A strong
   code-reasoning model gives noticeably better caller verdicts.
3. **Optional: publish the agent to everyone at once.** Create a repo named `.github-private` in the
   org and commit `agent/impact-check.agent.md` as `agents/impact-check.agent.md`. Every developer
   in the org then sees the `impact-check` agent without copying it into each repo.
4. **Check content exclusions.** If Copilot content exclusions cover parts of a repo, the agent
   can't read those files, and its verdicts there will say `unclear`.

> Every prompt to the agent uses Copilot **premium requests**, according to the model's multiplier.
> A typical check is one conversation; budget for it like any other agent use.

### Step 1 · Developer, once per machine (about 5 min)

```bash
# 1. Copilot CLI
npm install -g @github/copilot          # or: brew install --cask copilot-cli   /   winget install GitHub.Copilot

# 2. Sign in with your enterprise GitHub account
copilot                                 # inside the session type: /login   then follow the prompts

# 3. The impact CLI
pipx install git+https://github.com/chiragb3101/impact-check
impact --version                        # should print: impact 0.1.0
```

> If `pipx` is missing: `brew install pipx` (macOS) or `python3 -m pip install --user pipx`.
> Python 3.14 failed to create virtual environments on macOS in our testing, so use 3.12.

**Optional: personal agent.** If your admin didn't publish the agent (step 0.3) and the repo
doesn't have it (step 2.3), copy it to your user folder:

```bash
mkdir -p ~/.copilot/agents
curl -fsSL https://raw.githubusercontent.com/chiragb3101/impact-check/main/agent/impact-check.agent.md \
  -o ~/.copilot/agents/impact-check.agent.md
```

### Step 2 · Tech lead, once per repo (about 15 min)

Commit these files through a normal MR or PR.

#### 2.1 `impact.yaml`: required

`impact.yaml` goes in the repo root. It tells the tool which folders hold which language and **how
to run their tests**.

> **Without `impact.yaml`** the analysis still works, but **tests don't run**. Changed functions,
> callers and risk scores are reported, and every selected test comes back as `unresolved`, because
> the tool doesn't know your test command yet.

Pick the closest example:

```yaml
# Spring Boot (Maven)
modules:
  - path: ""
    language: java
    test_command: ./mvnw -q test -Dtest={tests} -Dsurefire.failIfNoSpecifiedTests=false
    junit: target/surefire-reports
```

```yaml
# Python package with a src/ layout
# PYTHONPATH=src makes sandbox runs import the branch's code, not the copy installed from your checkout.
modules:
  - path: ""
    language: python
    test_command: PYTHONPATH=src python -m pytest -q {tests} --junitxml={junit}
```

```yaml
# Monorepo with several languages
modules:
  - path: api
    language: python
    test_command: python -m pytest -q {tests} --junitxml={junit}
  - path: billing-service
    language: java
    test_command: mvn -q test -Dtest={tests} -Dsurefire.failIfNoSpecifiedTests=false
    junit: target/surefire-reports
  - path: web
    language: typescript
    test_command: npx jest --ci {tests}
    env:
      JEST_JUNIT_OUTPUT_FILE: "{junit}"
  - path: db/migrations
    language: sql                     # scanned for schema changes; no tests
ignore: [generated, vendor]
history_days: 180
```

| Field | Required | Meaning |
|---|---|---|
| `modules[].path` | yes | Module folder relative to the repo root (`""` = whole repo). Tests run with this folder as the working directory. |
| `modules[].language` | no | `python`, `java`, `javascript`, `typescript` or `sql`. Default: detected from the file extension. |
| `modules[].test_command` | **to run tests** | `{tests}` is replaced by the selected test ids; `{junit}` by a temporary JUnit XML path so results are read per test. |
| `modules[].junit` | no | A JUnit XML file or folder the runner writes itself (Maven Surefire, Gradle). |
| `modules[].env` | no | Extra environment variables for the test command. `{junit}` works here too. |
| `ignore` | no | Folder names to skip. `node_modules`, `build`, `dist`, `target` and `.venv` are always skipped. |
| `history_days` | no | How far back to look for churn and bug-fix commits. Default 180. |

**What `{tests}` receives**

| Language | Format | Example |
|---|---|---|
| Python | file path relative to the module, then `::` | `tests/test_checkout.py::test_total` |
| Java | `Class#method`, joined with commas | `OwnerControllerTests#processFindFormSuccess` |
| JS/TS | test file path relative to the module | `src/cart.test.ts` |

**Check the config** before you commit it:

```bash
impact index
impact facts --branch <some-recent-branch> --base main      # look at "selected_tests"
impact run-tests <one id from selected_tests>               # should report passed or failed, not unresolved
```

#### 2.2 `.gitignore`

```
.impact/
impact-reports/
```

`.impact/` holds the index, the sandbox worktree and personal per-repo rules. `impact-reports/`
holds the HTML reports.

#### 2.3 The agent: skip this if your admin published it org-wide

```bash
mkdir -p .github/agents
cp <impact-check>/agent/impact-check.agent.md .github/agents/
```

#### 2.4 Team rules: optional

Team rules are shared expectations the agent applies to every report in this repo:

```bash
impact rules add "Any change under payments/ is at least medium risk." --scope team
impact rules add "Call out new endpoints that delete data, and say what protects them." --scope team
git add impact-rules.md
```

### Step 3 · Developer, every check (2–5 min)

```bash
cd your-repo
git fetch origin                        # the base and the branch must be available locally
git switch main && git pull             # run from the base branch; the index is built from what's checked out
copilot
```

The first time, Copilot asks whether you trust the folder. Choose **"Yes, and remember this
folder"**. Then:

```
/agent                                  → choose impact-check
check feature/refund-limits
```

You can also name the base branch, or ask a follow-up:

```
check feature/refund-limits against develop
why is OrderService.total marked unclear?
```

**What the agent does** (you'll see each step in the session)

1. `impact index`, then `impact facts`: what changed, who calls it, which tests reach it, your rules.
2. Reads each changed function and labels it: behaviour change, refactor, signature change, new or
   deleted.
3. Opens each direct caller and decides `breaks`, `safe` or `unclear`.
4. Creates a sandbox, a separate git worktree under `.impact/worktree/`, and runs the selected
   tests on the branch code.
5. Writes one focused test in the sandbox for a risky path that has no tests, and runs it.
6. Writes `.impact/findings.json`, renders the report and removes the sandbox.
7. Replies with the risk level, the one or two main problems and the report path.

**Your own checkout is never changed.** The agent is told not to commit, push, merge, rebase,
reset or switch branches.

#### Stop approving every command

Copilot asks before running each shell command. For this agent you can pre-approve the safe ones
and block the risky ones:

```bash
copilot --agent=impact-check \
  --allow-tool='shell(impact:*)' \
  --allow-tool='shell(git:*)' \
  --deny-tool='shell(git push)' --deny-tool='shell(git commit)' \
  --deny-tool='shell(git checkout)' --deny-tool='shell(git switch)' \
  --deny-tool='shell(git reset)' --deny-tool='shell(git rebase)' --deny-tool='shell(git merge)'
```

Deny rules always win over allow rules. Save this as a shell alias, for example
`alias impact-check='copilot --agent=impact-check …'`. Test commands such as `mvn` or `pytest` run
inside `impact run-tests`, so they need no separate approval.

#### One-shot, no chat

```bash
copilot --agent=impact-check --prompt "check feature/refund-limits" \
  --allow-tool='shell(impact:*)' --allow-tool='shell(git:*)' --deny-tool='shell(git push)'
```

### Using the VS Code chat sidebar instead of the CLI

The same agent works in Copilot Chat in VS Code:

1. Make sure the agent file is at `.github/agents/impact-check.agent.md` in the open workspace,
   then reload VS Code (Command Palette → *Developer: Reload Window*).
2. Open Copilot Chat and pick **Agent** mode. *Ask* and *Edit* modes can't run commands. Then pick
   **impact-check** in the agent dropdown at the bottom of the chat box.
3. Click the **tools** icon in the chat box and make sure the **terminal / run commands** tool is
   ticked.
4. Check that VS Code's terminal can find the CLI: open a terminal (`` Ctrl+` ``) and run
   `impact --version`. If it can't, run `pipx ensurepath` and **quit and reopen VS Code**, not just
   the terminal.
5. Prompt `check feature/refund-limits`. VS Code asks before each terminal command; click
   **Allow**. You can allow `impact` commands for the session.

If chat says *"impact command tool is not available in this session"*, the agent has no terminal
tool. Go through steps 2–3, and make sure your copy of the agent file has **no `tools:` line**.
Older copies had `tools: ['read', 'search', 'execute', 'edit']`, which VS Code may not recognise.
Without a `tools:` line the agent gets every tool.

### Step 4 · Read the report

Open `impact-reports/<branch>-<time>.html` in a browser. It contains:

| Section | What to look at |
|---|---|
| **Verdict** | Risk level, score and a two-to-three sentence answer to "is this safe to merge?" |
| **What changed** | Each changed function and what kind of change it is |
| **Who is affected** | Each caller with `breaks`, `safe` or `unclear`, and the reason |
| **Tests** | Selected tests with pass/fail and failure messages |
| **Missing coverage** | Tests the agent wrote, and whether they confirmed the risk |
| **Rules applied** | Which of your rules changed the report, and how |
| **How the agent got here** | Each step it took (collapsed) |

Attach the report to your PR or MR, or paste the verdict into the description. Examples from real
open-source PRs are in `docs/real-repo-runs/`.

---

## Rules: teach it your preferences

Rules are **optional** plain-language instructions that the agent reads before every report. It
follows them when judging risk and writing, and lists the ones it applied. Rules can change emphasis
and severity. They never override test results or what the code does.

**From the Copilot chat** (easiest):

```
remember: always check Kafka consumers when an event class changes
from now on, don't flag churn in generated/ folders
```

The agent saves the rule and confirms it. If it isn't sure whether you mean just you or the whole
team, it asks.

**From the terminal:**

```bash
impact rules add "Treat any change under billing/ as at least medium risk."              # personal, every repo
impact rules add "Ignore whitespace-only template changes." --scope local                 # personal, this repo
impact rules add "Call out new delete endpoints and their auth." --scope team             # shared, commit impact-rules.md
impact rules                    # list rules with ids
impact rules remove u-1a2b      # forget one
```

| Scope | Stored in | Who sees it |
|---|---|---|
| `user` (default) | `~/.config/impact/rules.md` | Only you, in every repo |
| `local` | `<repo>/.impact/rules.md` | Only you, in this repo |
| `team` | `<repo>/impact-rules.md` | Everyone, once committed |

When rules conflict, `local` beats `user`, and `user` beats `team`. The files are plain Markdown,
one rule per bullet, so you can edit them by hand.

---

## Rolling it out across many repos

| Who | Once | Ongoing |
|---|---|---|
| Org admin | Enable Copilot CLI and models; publish the agent in `.github-private/agents/` | Watch premium-request usage |
| Each developer | Install Copilot CLI and `impact`; `/login` | `impact-check` → `check <branch>` before opening a PR |
| Each repo's tech lead | Commit `impact.yaml`, `.gitignore` entries and, optionally, `impact-rules.md` | Update `impact.yaml` when modules or test commands change; review team-rule changes in PRs |

The same CLI and agent work in every repo. Only `impact.yaml` and the team rules differ, and
personal rules follow each developer from repo to repo.

**Suggested pilot:** 1–2 repos and 3–5 developers for two weeks. Ask after each check whether the
report was right and useful. See `NEXT_STEPS.md` for the full plan.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `copilot: command not found` | Not installed, or npm's global bin isn't on PATH | Re-run step 1; check `npm prefix -g` |
| Copilot says CLI access is disabled | Policy is off | Ask your admin (step 0.1) |
| VS Code chat: "impact command tool is not available" | Agent has no terminal tool (not in Agent mode, terminal tool unticked, or an old agent file with a `tools:` line) | See "Using the VS Code chat sidebar"; update the agent file |
| `/agent` doesn't list `impact-check` | Agent file not found | It must be in `.github/agents/`, `~/.copilot/agents/` or the org's `.github-private/agents/`; restart `copilot` |
| Agent says `impact: command not found` | The pipx bin folder isn't on PATH | Run `pipx ensurepath` and open a new terminal |
| `No index found` | First run in this repo | `impact index` (the agent normally does this) |
| `git merge-base … failed` | Base branch isn't available locally | `git fetch origin main:main`, or pass `--base origin/main` |
| Every test is `unresolved` | No `impact.yaml`, or no `test_command` for that folder | Step 2.1 |
| All tests fail with "not found" | Index built on a different branch than the base | `git switch <base>`, then `impact index` |
| New tests fail on code the branch clearly fixed | Python `src/` layout imports your installed checkout | Add `PYTHONPATH=src` to `test_command` |
| Java tests all "failed" with "See output_tail" | Usually a compile error | Read `output_tail`; it often means something was renamed and is still in use |
| Caller verdicts are mostly `unclear` | Content exclusions, or the model can't see enough | Check exclusions; try a stronger model |

---

## Command reference

| Command | What it does |
|---|---|
| `impact index [--full]` | Builds or refreshes `.impact/index.db`. Only changed files are re-parsed. |
| `impact facts --branch B [--base main] [--depth 2]` | Changed symbols, callers, reachable tests, schema changes, history, risk scores and your rules. |
| `impact callers SYMBOL [--depth 3] [--branch B]` | Follows the call chain further for one symbol. |
| `impact sandbox create --branch B` / `status` / `cleanup` | Manages the temporary worktree at `.impact/worktree/`. |
| `impact run-tests ID... [--sandbox] [--module PATH] [--timeout 900]` | Runs test ids with each module's `test_command`. |
| `impact db-usage table[.column]` | Code lines that reference a table or column. |
| `impact history FILE [--days 180]` | Commits and bug-fix commits for a file. |
| `impact schema` | Prints the findings JSON schema. |
| `impact report FINDINGS.json [--out-dir DIR]` | Validates findings and renders the HTML report. |
| `impact rules [list \| add TEXT \| remove ID \| path] [--scope user\|local\|team]` | Manages rules. |

Global options: `--root PATH` (default: the git top level) and `--config PATH` (default:
`<root>/impact.yaml`).

### Try it without a real repo

```bash
scripts/make_demo_repo.sh /tmp/impact-demo
cd /tmp/impact-demo && copilot     # /agent → impact-check → check feature/discount-rules
```

The demo branch makes coupon expiry raise an error, which breaks checkout and renewals, and renames
a database column that a query still uses.

---

## Known limits (v0.1.0)

Found by testing on 8 real open-source PRs. Fixes are planned in `NEXT_STEPS.md`.

- **`impact.yaml` is needed to run tests.** Test commands are not detected yet.
- **Callers are matched by name, not by type.** Spring dependency injection, reflection and dynamic
  dispatch can hide callers, and common names add false ones. The agent checks by reading the code.
- **JavaScript written as `obj.fn = function` or `exports.x = …`**, and mocha tests in a plain
  `test/` folder, are not recognised. Only `*.test.*`, `*.spec.*` and `__tests__/` count as tests.
- **Run checks from the base branch.** The index reflects whatever is checked out when
  `impact index` runs.
- **Risk scores are a starting point.** Deleted tests inflate the score, and a deleted method that
  is still called scores only medium. The agent corrects both in the final verdict.
- **Python:** calls to `ClassName(...)` are not linked to `__init__`, and property access is not
  treated as a call.
- **Only code files are analysed.** Templates, `.properties` and YAML changes are listed but not
  analysed.
- **One repo at a time.** Callers in other repositories (other microservices) are not found.
- **Tests run the branch's code on your machine.** Check branches you would be willing to run
  locally. For CI, use an isolated container with no secrets.
