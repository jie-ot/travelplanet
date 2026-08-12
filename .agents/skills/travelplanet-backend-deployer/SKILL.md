---
name: travelplanet-backend-deployer
description: Package and safely redeploy the TravelPlanet FastAPI/SQLite backend to the Tencent Cloud lightweight server with current secrets, database, static media, Docker candidate validation, Alembic migration, fixed 12306 MCP runtime, persistent business logs, production cutover, verification, and rollback. Use when Codex is asked to upload, update, rebuild, repair, verify, or roll back the TravelPlanet backend on 114.132.201.196, especially after backend code, prompts, dependencies, migrations, data, keys, MCP configuration, or logging behavior changes.
---

# Deploy the TravelPlanet backend

Work from the repository root containing `后端/`. Treat every deployment as a new audit: server state, container names, image tags, migrations, secrets, data counts, MCP package state, and public reachability can change.

## Invariants

- Default server: `114.132.201.196`; host root: `/home/ubuntu`; public port: `8000`.
- Expected production container: `travelplanet-backend`; candidate port: `127.0.0.1:8001`.
- Preserve current `.env`, `data/travelplanet.db`, and `static/` in the full sensitive release unless the user explicitly requests a sanitized or code-only deployment.
- Back up the running cloud directory, database, media, image identity, container inspection output, and logs before cutover. Never merge or overwrite cloud data silently.
- Keep the old container, old image, old directory, and uploaded archive until the user confirms the new release. Never run Alembic downgrade as the first rollback action.
- Bind host `data`, `static`, and `logs` into `/app/data`, `/app/static`, and `/app/logs`. Business JSON-lines logs are not the same as `docker logs`.
- Never print secret values. Inspect environment variable names only, keep `.env` and the archive mode `600`, and state that the archive is a sensitive backup.
- Never claim deployment completion without candidate checks, production checks, and a public check from outside the server.

## Workflow

1. Inspect `git status`, relevant diffs, `pyproject.toml`, `uv.lock`, `.python-version`, Alembic heads/current, `.env` variable names, database integrity/counts, static counts, tests, and startup smoke. Preserve unrelated user work.
2. Inspect the live server before giving mutation commands. Determine Docker versus systemd, the real container/image, ports, restart policy, command, mounts, environment names, database revision/integrity/counts, static count, `/app/logs`, and MCP process/package. Use small command blocks and evaluate their output before continuing.
3. Build a full sensitive archive locally with the bundled script:

   ```powershell
   & "<skill-directory>\scripts\prepare-release.ps1" `
     -RepoRoot (Get-Location).Path `
     -FullSnapshot
   ```

   Report its absolute path, SHA-256, size, database size, and static-file count. Do not upload automatically unless the user authorizes or performs the transfer.
4. Read [references/docker-deploy.md](references/docker-deploy.md) before issuing server commands. Follow its sequence: checksum, isolated release directory, vendor recovery, image build, candidate on 8001, verification, production cutover, public verification, then rollback/retention.
5. If `pyproject.toml` and `uv.lock` match the running release, still rebuild the image for changed source; dependency layers should cache. Do not assume a host `uv sync` environment is used when production is Docker.
6. Pin the working railway MCP. Prefer the vendored `12306-mcp` copied from the running release/container; never replace it with an unverified floating `npx -y 12306-mcp`. Set the endpoint to the vendored Node entry point and verify initialization plus at least one tool call.
7. Run the candidate against only the candidate release's `data`, `static`, and `logs`. Verify OpenAPI, Alembic head/current, SQLite `integrity_check`, table/row counts, static files, business-log creation, MCP readiness, and an actual read endpoint before touching port 8000.
8. During cutover, stop and rename rather than delete. Record exact rollback names. Start production from the verified image with canonical host mounts and `--restart unless-stopped`; then re-run every check through both `127.0.0.1:8000` and the public IPv4.
9. Report evidence separately: local package validation, candidate validation, production-local validation, public validation, and any unverified user-facing flow. A successful build alone is not a successful deployment.

## Failure shields

- If SSH authentication is unavailable, prepare artifacts and commands only; explicitly mark cloud mutation and verification incomplete.
- If the candidate cannot start, inspect its logs and environment names before changing code or dependencies. Do not stop production.
- If `npx -y 12306-mcp` hangs but the old container works, copy and vendor the known working package, then use `stdio:node /app/vendor/12306-mcp/node_modules/12306-mcp/build/index.js`.
- If host `logs/` is absent, inspect `/app/logs`, deployed `business_logging.py`, endpoint wrappers, access logs, and mounts before rebuilding. Persist logs with a bind mount.
- If migration changes exist, migrate only the candidate database first. Keep the pre-migration database backup for rollback.
- If public probes intermittently return an empty response, compare local server probes, Docker logs, firewall state, and repeated bounded requests; do not mislabel an unstable network as a successful release.

## Cleanup

Keep the verified archive, canonical release, rollback directory/container/image, persistent data/media/logs, and evidence hashes. Remove only proven temporary candidate containers or failed staging directories after explicit confirmation. Never delete a backup, database, media tree, archive, vendor cache, or unknown file to make the workspace look clean.
