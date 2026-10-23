# ADR 0002: MIT License and AGPL Engines as External Processes

- Status: Accepted
- Date: 2026-10-23
- Decided by: the owner, in issue #30 (option A)

## Context
omr-lab had no license (`pyproject.toml` said "License to be decided"), so by default nobody but the owner could use, copy or contribute to the code. Its first engine, Audiveris, is licensed under AGPL-3.0, whose obligations extend to software offered to users over a network (section 13). Other engine candidates (homr, oemer, OCR models, Verovio) have their own licenses, to be checked in their issues.

## Decision
1. omr-lab is licensed under the **MIT License** (`LICENSE`).
2. **AGPL engines** (currently Audiveris) are used only as **separately installed, unmodified external programs**: omr-lab locates the installed launcher and runs it as a process with command-line arguments (`run_command`), reading the files it writes. omr-lab does not link, import, vendor, bundle or modify them.
3. **Before any distribution or hosted service** that includes or runs an AGPL engine (for example a Docker image or installer, OMR-014, or the cloud phase), the licenses are reviewed again and the result is recorded in a new ADR.
4. **New dependencies** that omr-lab imports must have licenses compatible with distributing omr-lab under MIT. A GPL or AGPL library is not added as an import dependency without a new decision; such tools may only be invoked as external programs under rule 2.

## Consequences
- Anyone may use, modify and redistribute omr-lab under MIT, including in closed-source software, if they keep the copyright and license notice.
- Local use of an unmodified Audiveris, as in the integration tests, is the low-obligation case. Distributing Audiveris or offering a modified Audiveris over a network would bring AGPL obligations for Audiveris itself; rule 3 makes that a reviewed step.
- While the owner is the only copyright holder, the license of future versions can still be changed; versions already distributed keep the license they were received under. Outside contributions are accepted under MIT.
- Test fixtures keep their own recorded licenses (the synthetic fixtures and the end-to-end sample are CC0 1.0; see their READMEs).

This record is not legal advice.
