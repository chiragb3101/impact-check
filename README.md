# impact

Change impact analysis for a mixed-language monorepo (Java, Python, Node/TypeScript, SQL).
Given a branch, it finds the changed functions, who calls them, which tests reach them, how risky
the files have been historically, and which database columns are affected. A Copilot custom agent
uses it to investigate the branch and produce an HTML risk report.

There is no LLM inside this tool. All reasoning happens in Copilot; `impact` supplies reliable facts
and runs tests. Every command prints JSON.

```
Copilot agent (impact-check.agent.md)
   │  runs commands in the terminal, reads code, writes findings
   ▼
impact CLI ── git (diff, history) ── tree-sitter (symbols, calls) ── SQLite index ── test runners
   │
   ▼
impact-reports/<branch>-<time>.html
```

## Install

Requires Python 3.10+ and git.

```bash
pip install -e .            # from this folder; puts `impact` on your PATH
impact --version
```

## Configure

Copy `impact.example.yaml` to your repo root as `impact.yaml` and adjust the module paths and test
commands. Test commands run with the module folder as working directory. `{tests}` is replaced by
the selected test ids, `{junit}` by a temporary JUnit XML path so results can be parsed.

Add to `.gitignore`:

```
.impact/
impact-reports/
```

## Commands

| Command | What it does |
|---|---|
| `impact index [--full]` | Builds or refreshes `.impact/index.db`. Only changed files are re-parsed. |
| `impact facts --branch B [--base main]` | Changed symbols, callers (2 levels), reachable tests, schema changes, file history, risk scores. |
| `impact callers SYMBOL [--depth 3] [--branch B]` | Follows the call chain further for one symbol. |
| `impact sandbox create --branch B` | Creates a git worktree of the branch at `.impact/worktree/`. |
| `impact run-tests ID... [--sandbox] [--module PATH]` | Runs test ids with the right command per module, returns pass/fail and messages. |
| `impact db-usage table[.column]` | Code lines that reference a table or column. |
| `impact history FILE [--days 180]` | Commits and bug-fix commits for a file. |
| `impact schema` | Prints the findings JSON schema. |
| `impact report FINDINGS.json` | Validates findings and renders the HTML report. |
| `impact sandbox status` / `cleanup` | Lists files written in the sandbox / removes it. |

Test id formats: Python `tests/test_x.py::test_name` (relative to the module), Java `ClassTest#method`,
JS/TS the test file path relative to the module.

## Use with Copilot

1. Copy `agent/impact-check.agent.md` to `.github/agents/impact-check.agent.md` in your repo
   (or your Copilot CLI agents folder).
2. Check the `tools:` names in the frontmatter against your Copilot version.
3. Select the `impact-check` agent and ask: `check feature/discount-rules`.

## Try the demo

```bash
scripts/make_demo_repo.sh /tmp/impact-demo
cd /tmp/impact-demo
impact index
impact facts --branch feature/discount-rules
impact sandbox create --branch feature/discount-rules
impact run-tests tests/test_checkout.py::test_total_with_expired_coupon --sandbox
impact report /path/to/impact-cli/examples/findings.example.json
```

The demo branch changes `calculate_discount` to raise on expired coupons (breaking checkout and
renewals) and renames a column still used in a query. `examples/report.example.html` shows the
report an agent run produces.

## Known limits

- Callers are matched by name, not resolved types. Spring dependency injection, reflection and
  dynamic dispatch can hide callers; common names can add false ones. `definitions_with_same_name`
  flags this, and the agent verifies by reading code. Language servers (jdtls, Pyright, tsserver)
  can replace the parser later behind the same interface for exact references.
- Test mapping is static (tests that call the changed code within 3 hops). Coverage-based mapping
  is a good next step for modules where it is easy to collect.
- SQL detection is regex-based and covers common DDL (create, drop, alter, rename, index).
- The index reflects the working tree when `impact index` last ran; changed files on the branch are
  re-read from git automatically.
