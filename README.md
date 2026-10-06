# impact-check

Find out whether a branch is safe to merge before you merge it.

`impact-check` looks at a branch and answers four questions:

1. **What changed?** The functions, methods, classes and database columns modified on the branch.
2. **Who is affected?** The code that calls the changed functions, up to 2–3 levels up.
3. **Do the tests still pass?** It runs only the tests that reach the changed code, not the whole suite.
4. **How risky is it?** A high, medium or low rating with the reasons, saved as an HTML report.

It supports Java, Python, JavaScript/TypeScript and SQL migrations, in single repos and monorepos.

## How it works

There are two parts:

```
You ──► Copilot CLI agent (agent/impact-check.agent.md)
           │  reads code, judges each caller, writes missing tests, writes the summary
           │  runs `impact …` commands in the terminal
           ▼
        impact CLI ── git ── tree-sitter parser ── SQLite index ── your test runner
           │
           ▼
        impact-reports/<branch>-<time>.html
```

- **`impact` CLI** (this package). It gathers the facts: the diff, a call graph, test selection, file
  history and test runs. It contains **no LLM**, so it gives the same answer every time and costs
  nothing to run. Every command prints JSON.
- **The Copilot agent.** The reasoning happens here: is this a behaviour change, will this caller
  break, which test is missing. It runs on whatever model your GitHub Copilot plan gives you. No
  separate API key is needed.

You can also use the CLI on its own, without Copilot, to get the facts and test results.

## Install

You need Python 3.10–3.12 and git.

```bash
pipx install git+https://github.com/chiragb3101/impact-check
impact --version
```

From a local checkout:

```bash
pip install -e .
```

> On Python 3.14, `python -m venv` failed at `ensurepip` on macOS. Use 3.12, or
> `uv venv -p 3.12` if you have uv.

## Set up a repo

### 1. Add `impact.yaml`: required to run tests

`impact.yaml` goes in the repo root. It tells the tool which folders hold which language and **how
to run their tests**.

**Without `impact.yaml`:**

| Works | Doesn't work |
|---|---|
| `impact index`, `impact facts`: changed symbols, callers, which tests cover them, risk scores | `impact run-tests`: there is no test command, so every test comes back as `unresolved` |
| `impact callers`, `history`, `db-usage`, `report`, `rules` | Monorepos where each folder runs tests differently |

The tool treats the whole repo as one module and detects each file's language from its extension.
That is enough for analysis but not for running tests, so in practice **every repo needs an
`impact.yaml`**. Start from `impact.example.yaml`:

```yaml
modules:
  - path: api                      # folder, relative to the repo root ("" = whole repo)
    language: python               # python | java | javascript | typescript | sql
    test_command: python -m pytest -q {tests} --junitxml={junit}

  - path: billing-service
    language: java
    test_command: mvn -q test -Dtest={tests} -Dsurefire.failIfNoSpecifiedTests=false
    junit: target/surefire-reports # where the runner writes JUnit XML (relative to the module)

  - path: web
    language: typescript
    test_command: npx jest --ci {tests}
    env:
      JEST_JUNIT_OUTPUT_FILE: "{junit}"

  - path: db/migrations
    language: sql                  # SQL folders are scanned for schema changes; no tests

ignore: [generated, vendor]        # extra folder names to skip (node_modules, build, dist, target… are always skipped)
history_days: 180                  # how far back to look for churn and bug-fix commits
```

**Fields**

| Field | Required | Meaning |
|---|---|---|
| `modules[].path` | yes | Folder of the module. Test commands run with this folder as the working directory. |
| `modules[].language` | no | Defaults to `auto` (detected from the file extension). |
| `modules[].test_command` | to run tests | `{tests}` is replaced by the selected test ids; `{junit}` by a temporary JUnit XML path so results can be read per test. |
| `modules[].junit` | no | A JUnit XML file or folder the runner writes itself (Maven Surefire, Gradle). |
| `modules[].env` | no | Extra environment variables for the test command. `{junit}` works here too. |
| `ignore` | no | Folder names to skip during indexing. |
| `history_days` | no | Window for file history. Default 180. |

**Single-repo examples**

```yaml
# Python package with a src/ layout. PYTHONPATH=src makes sandbox runs import the branch's code,
# not the copy installed from your checkout.
modules:
  - path: ""
    language: python
    test_command: PYTHONPATH=src python -m pytest -q {tests} --junitxml={junit}
```

```yaml
# Spring Boot with Maven
modules:
  - path: ""
    language: java
    test_command: ./mvnw -q test -Dtest={tests} -Dsurefire.failIfNoSpecifiedTests=false
    junit: target/surefire-reports
```

**Test id formats** (what `{tests}` receives)

| Language | Format | Example |
|---|---|---|
| Python | file path relative to the module, then `::` | `tests/test_checkout.py::test_total` |
| Java | `Class#method`, joined with commas | `OwnerControllerTests#processFindFormSuccess` |
| JS/TS | test file path relative to the module | `src/cart.test.ts` |

### 2. Ignore the tool's working files

Add to `.gitignore`:

```
.impact/
impact-reports/
```

`.impact/` holds the index, the sandbox worktree and personal rules. `impact-reports/` holds the
HTML reports.

### 3. Add the Copilot agent

Copy `agent/impact-check.agent.md` to `.github/agents/impact-check.agent.md` in the repo, or to
your Copilot CLI agents folder. Check that the `tools:` names in its frontmatter match your
Copilot version: the agent needs to read files, search, run terminal commands and edit files.

### 4. Optional: team rules

See [Rules](#rules-your-own-memory-for-reports). A repo works without any rules.

## Use it

### With Copilot (recommended)

```bash
cd your-repo
copilot
```

Select the `impact-check` agent, then ask:

```
check feature/refund-limits
check feature/refund-limits against develop
```

The agent will:

1. Run `impact index` and `impact facts`.
2. Read each changed function and decide what kind of change it is: behaviour change, refactor,
   signature change, new or deleted.
3. Read each direct caller and mark it `breaks`, `safe` or `unclear`, with a reason.
4. Create a sandbox (a separate git worktree under `.impact/worktree/`) and run the selected tests
   on the branch code.
5. Write one focused test in the sandbox for any risky path that has no tests, and run it.
6. Write `.impact/findings.json` and render the report.
7. Remove the sandbox and reply with the risk level, the main problems and the report path.

Your own checkout is never changed. The agent may not commit, push or switch branches.

To allow the agent's commands without approving each one, allow `impact` and read-only `git`
commands (`diff`, `log`, `show`) in your Copilot tool permissions.

### CLI only

```bash
impact index                                    # build or refresh .impact/index.db
impact facts --branch feature/x --base main     # JSON: changes, callers, tests, risk
impact sandbox create --branch feature/x
impact run-tests <ids from facts.next_steps.run_tests> --sandbox
impact sandbox cleanup
```

`--base` defaults to `main`. Pass `--base master` or `--base develop` if your repo uses another
name. The base branch must exist locally.

### Commands

| Command | What it does |
|---|---|
| `impact index [--full]` | Builds or refreshes `.impact/index.db`. Only changed files are re-parsed. |
| `impact facts --branch B [--base main] [--depth 2]` | Changed symbols, callers, reachable tests, schema changes, file history, risk scores and your rules. |
| `impact callers SYMBOL [--depth 3] [--branch B]` | Follows the call chain further for one symbol. |
| `impact sandbox create --branch B` | Creates a git worktree of the branch at `.impact/worktree/`. |
| `impact sandbox status` / `cleanup` | Lists files written in the sandbox / removes it. |
| `impact run-tests ID... [--sandbox] [--module PATH] [--timeout 900]` | Runs test ids with each module's `test_command`; returns pass/fail and messages. |
| `impact db-usage table[.column]` | Code lines that reference a table or column. |
| `impact history FILE [--days 180]` | Commits and bug-fix commits for a file. |
| `impact schema` | Prints the findings JSON schema. |
| `impact report FINDINGS.json [--out-dir DIR]` | Validates findings and renders the HTML report. |
| `impact rules [list \| add TEXT \| remove ID \| path] [--scope user\|local\|team]` | Manages the natural-language rules the agent follows. |

Global options: `--root PATH` (default: the git top level) and `--config PATH` (default:
`<root>/impact.yaml`).

## Rules: your own memory for reports

Rules are **optional**. Each person can keep rules in plain language that the agent reads before
every report. The agent follows them when judging risk and writing the report, and lists the ones
it used under **Rules applied**, with the effect each one had. Rules can change emphasis and
severity. They never override test results or what the code does.

```bash
impact rules add "Treat any change under billing/ as at least medium risk."                 # personal, every repo
impact rules add "Ignore churn in generated/ when scoring." --scope local                     # personal, this repo
impact rules add "Call out new endpoints that delete data, with their auth." --scope team     # shared
impact rules                  # list all rules with ids
impact rules remove u-1a2b    # forget one
```

| Scope | File | Shared? |
|---|---|---|
| `user` (default) | `~/.config/impact/rules.md` (or `$IMPACT_HOME/rules.md`) | No |
| `local` | `<repo>/.impact/rules.md` | No |
| `team` | `<repo>/impact-rules.md` | Yes, commit it |

The files are ordinary Markdown with one rule per bullet, so you can also edit them by hand. When
rules conflict, `local` beats `user`, and `user` beats `team`. In Copilot you can just say "from now
on, always…" or "remember that…", and the agent saves the rule for you.

## Using it across many repos

| Who | Once | Every time |
|---|---|---|
| Each developer | `pipx install …` | `copilot` → `impact-check` agent → `check <branch>` |
| Each repo (tech lead) | Commit `impact.yaml`, the agent file, `.gitignore` entries and, optionally, `impact-rules.md` | Update `impact.yaml` when modules or test commands change |

The same CLI and agent work in every repo. Only `impact.yaml` and the team rules differ. Personal
rules follow a developer from repo to repo.

## Try the demo

```bash
scripts/make_demo_repo.sh /tmp/impact-demo
cd /tmp/impact-demo
impact index
impact facts --branch feature/discount-rules
impact sandbox create --branch feature/discount-rules
impact run-tests tests/test_checkout.py::test_total_with_expired_coupon --sandbox
impact report /path/to/impact-check/examples/findings.example.json
```

The demo branch changes `calculate_discount` to raise on expired coupons, which breaks checkout and
renewals, and renames a column that a query still uses. `examples/report.example.html` shows the
report an agent run produces. Reports from real open-source PRs are in `docs/real-repo-runs/`.

## Known limits (v0.1.0)

From testing on 8 real PRs. See [NEXT_STEPS.md](NEXT_STEPS.md) for the fix plan.

- **`impact.yaml` is needed to run tests.** The tool does not detect test commands yet.
- **Callers are matched by name, not by type.** Spring dependency injection, reflection and dynamic
  dispatch can hide callers, and common names add false ones. `definitions_with_same_name` flags
  this, and the agent checks by reading the code.
- **JavaScript written as `obj.fn = function…` or `exports.x = …`**, and mocha tests in a plain
  `test/` folder, are not recognised. Only `*.test.*`, `*.spec.*` and `__tests__/` count as tests.
- **The index reflects whatever branch is checked out** when `impact index` last ran. Run it on the
  base branch. If one test id is missing, the whole test batch can fail.
- **`src/`-layout Python packages** need `PYTHONPATH=src` in `test_command`. Without it, sandbox
  runs test your installed checkout instead of the branch.
- **Risk scoring:** deleted tests count as production risk, and a deleted method that is still
  called only scores medium. The agent corrects both in its review.
- **Python:** calls to `ClassName(...)` are not linked to `__init__`, and property access is not
  treated as a call.
- **Only `.java`, `.py`, `.js`/`.ts` and `.sql` files are analysed.** Templates, `.properties` and
  YAML changes are listed but not analysed.
- **SQL detection** is regex-based and covers common DDL (create, drop, alter, rename, index).
- **One repo at a time.** Callers in other repositories (for example other microservices) are not
  found.
- **Test execution runs branch code on your machine.** In CI, run it in an isolated container with
  no secrets.
