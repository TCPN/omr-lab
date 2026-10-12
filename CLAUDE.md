@AGENTS.md

# Claude Code notes

AGENTS.md above is the shared rule set. This file only adds Claude-specific pointers; keep it short and link to sources of truth instead of copying them.

## Where the current state lives
- Open owner decisions: `gh issue list --label "decision: needed"`
- Recorded decisions: `docs/decisions/` (ADRs)
- Planned work: open GitHub Issues and milestones; `docs/roadmap.md`
- Downstream consumer: [TCPN/song_helper](https://github.com/TCPN/song_helper) (`dev` branch) stores scores as ABC and renders/transposes them with abcjs. Target scores are mostly single-melody lines with chord symbols and Chinese lyrics, sometimes several verses per staff line.

## Working agreements
- One issue per session and per PR. Read the issue and its linked decisions before starting.
- When the owner must decide something that blocks work or affects architecture, cost, licensing, privacy, or data use, open a decision issue (`.github/ISSUE_TEMPLATE/decision.yml`) instead of leaving the question only in chat. See `docs/governance/issue-and-pr-workflow.md`.
- Record durable conclusions in the repository (issue comments, ADRs, docs), not only in chat.

## Owner's local machine
These apply only when running on the owner's PC, not on GitHub Actions runners.
- The repository lives in WSL2 (`Ubuntu-24.04`) at `~/projects/omr-lab`; run tooling there. `uv` is in `~/.local/bin`; `gh` is authenticated.
- From a Windows PowerShell session, run commands as `wsl -d Ubuntu-24.04 --cd ~/projects/omr-lab -e bash -lc '<command>'`. PowerShell strips double quotes inside that argument, and an unquoted `#` then starts a bash comment; put anything non-trivial in a script file and run the file.
- GPU: RTX 5060 (compute capability 12.0) requires PyTorch wheels built for CUDA 12.8 or newer (e.g. the `cu128` index).
- `sudo` needs the owner's password: ask the owner to run `apt` commands.
