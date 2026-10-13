# Roles and Collaboration

## ChatGPT — planning/review support
Clarify goals, design architecture, draft issues and acceptance criteria, suggest tests, explain failures, and identify risks. Durable decisions belong in this repository.

## Codex — implementation/verification
Read the repository rules and issue; implement only agreed scope; add tests; run lint/unit/integration checks available; report exact commands and results. Never claim tests passed if they were not run.

## Claude Code — implementation, local experiments, and review
Same rules as Codex (AGENTS.md, via CLAUDE.md). Opens decision issues for questions the owner must decide. Runs in three ways:
- **Owner's PC** (WSL2 with GPU, local engines and private data): engine integration, benchmarks, and experiments.
- **Automatic PR review** (`.github/workflows/claude-code-review.yml`): runs on every pull request when it is opened, updated, reopened, or marked ready for review; no mention needed.
- **On mention** (`.github/workflows/claude.yml`): runs when `@claude` appears in an issue title or body (on open/assign), an issue or PR comment, or a PR review; for code, docs, and review tasks that need no local resources.

## Human owner
Own priorities, scope approval, PR review/merge, licensing/privacy/cost decisions, data permissions, and releases.

## GitHub — source of truth
Issues define work; PRs record changes and evidence; docs record architecture and decisions; CI runs repeatable checks. Chat history is not a substitute for repository documentation.

## Handoff checklist
Planning → implementation: issue, objective, relevant docs, scope/non-goals, acceptance criteria, tests, risks. Implementation → review: summary, files, commands/results, tests not run, limitations, follow-up issues.
