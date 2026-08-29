# Release Checklist

## Versioning
- Update `CHANGELOG.md` under `[Unreleased]` and cut a version section.
- Tag release in git (`vX.Y.Z`) after verification.

## Quality gates
- CI green (`pip-audit`, `detect-secrets`, Bandit, coverage, `pytest`, compileall, Ruff correctness rules, every tracked portfolio JavaScript file, macOS/Linux shell syntax, and PowerShell parser).
- Manually verify:
  - `/portfolio` loads with filters and export
  - `/portfolio/growth` charts and timeline table
  - `/portfolio/agent` stream response
  - `/portfolio/setup` account edit/import flow

## Branch protection

- Protect `main` and require a pull request before merge.
- Require the GitHub Actions check named `test` from workflow `CI`.
- Require the branch to be up to date before merge.
- Require at least one approving review and disable force pushes/deletions.
- Restrict administrator bypass and require signed commits/tags for releases.
- Do not bypass the required check for milestone merges.
- Confirm the local full suite and the milestone's manual checklist before requesting review.

## Security
- Ensure `.env`, `accounts.json`, and `modules/portfolio/data/*` are not committed.
- Validate HTTP auth env vars for non-local deployments.
- Create and validate an encrypted pre-upgrade backup; verify System Health after startup.
- Confirm release artifacts exclude runtime data, backups, support bundles, and generated tax/portfolio workbooks.

## Deployment
- Build image:
  - `docker build -t talk-to-my-portfolio:latest .`
- Run smoke:
  - `docker run --rm -p 9000:9000 --env-file .env talk-to-my-portfolio:latest`
- Verify `/health`.

## Post-release
- Publish release notes.
- Record known issues and follow-up tickets.
