# Release Checklist

## Versioning
- Update `CHANGELOG.md` under `[Unreleased]` and cut a version section.
- Tag release in git (`vX.Y.Z`) after verification.

## Quality gates
- CI green (`pytest`, syntax checks).
- Manually verify:
  - `/portfolio` loads with filters and export
  - `/portfolio/growth` charts and timeline table
  - `/portfolio/agent` stream response
  - `/portfolio/setup` account edit/import flow

## Security
- Ensure `.env`, `accounts.json`, and `modules/portfolio/data/*` are not committed.
- Validate HTTP auth env vars for non-local deployments.

## Deployment
- Build image:
  - `docker build -t talk-to-my-portfolio:latest .`
- Run smoke:
  - `docker run --rm -p 8000:8000 --env-file .env talk-to-my-portfolio:latest`
- Verify `/health`.

## Post-release
- Publish release notes.
- Record known issues and follow-up tickets.
