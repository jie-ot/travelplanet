# Docker deployment runbook

Use this runbook only after inspecting current local and server state. Substitute concrete release identifiers before presenting commands to the user; do not leave ambiguous placeholders in executable blocks.

## 1. Read-only server audit

```bash
whoami
cd /home/ubuntu
df -h /
sudo docker ps -a --format 'NAME={{.Names}} IMAGE={{.Image}} STATUS={{.Status}} PORTS={{.Ports}}'
sudo ss -lntp | grep ':8000' || true
curl -fsS http://127.0.0.1:8000/openapi.json | head -c 200

cid=$(sudo docker ps --filter publish=8000 -q | head -n 1)
echo "container=$cid"
sudo docker inspect "$cid" --format \
  'Name={{.Name}} Image={{.Config.Image}} Workdir={{.Config.WorkingDir}} Restart={{.HostConfig.RestartPolicy.Name}} Cmd={{json .Config.Cmd}}'
sudo docker inspect "$cid" --format \
  '{{range .Mounts}}{{println .Type .Source "->" .Destination}}{{else}}NO_MOUNTS{{end}}'
sudo docker inspect "$cid" --format '{{range .Config.Env}}{{println .}}{{end}}' |
  sed 's/=.*$/=<hidden>/'
sudo docker logs --tail=120 "$cid"
```

Inspect the running database, migration, files, business logs, and MCP without exposing secrets:

```bash
sudo docker exec "$cid" sh -lc '
cd /app
/app/.venv/bin/python --version
node --version
/app/.venv/bin/python -m alembic current
test -f /app/app/core/business_logging.py && echo business_logging=present
find /app/logs -maxdepth 1 -type f -name "*.log" 2>/dev/null | wc -l
find /app/static -type f 2>/dev/null | wc -l
for file in /proc/[0-9]*/cmdline; do
  command=$(tr "\0" " " < "$file" 2>/dev/null)
  case "$command" in *12306-mcp/build/index.js*) echo "$file: $command";; esac
done
'
```

Record the old image, container, directory, Alembic revision, database integrity/counts, static count, and log count before mutation.

## 2. Upload and extract the sensitive archive

The Codex agent should give an exact expected SHA-256 from the local packaging result.

```bash
cd /home/ubuntu
umask 077
sha256sum travelplanet_backend_deploy_RELEASE.tar.gz
chmod 600 travelplanet_backend_deploy_RELEASE.tar.gz
tar -tzf travelplanet_backend_deploy_RELEASE.tar.gz | head -n 30

release=RELEASE
release_dir="/home/ubuntu/travelplanet_backend_release_$release"
test ! -e "$release_dir" || { echo "release directory exists; stop"; exit 1; }
mkdir "$release_dir"
tar -xzf "travelplanet_backend_deploy_$release.tar.gz" -C "$release_dir"
chmod 600 "$release_dir/.env"
test -s "$release_dir/.env"
test -s "$release_dir/data/travelplanet.db"
mkdir -p "$release_dir/logs"
```

Compare dependency inputs with the current canonical directory. Matching hashes permit Docker layer reuse but do not eliminate the image rebuild:

```bash
sha256sum \
  /home/ubuntu/travelplanet_backend/pyproject.toml \
  "$release_dir/pyproject.toml" \
  /home/ubuntu/travelplanet_backend/uv.lock \
  "$release_dir/uv.lock"
```

## 3. Preserve the known-working 12306 MCP

Prefer the canonical vendored package. If absent there, copy it from the running container. Do not silently fetch a floating npm version.

```bash
mkdir -p "$release_dir/vendor"
if test -f /home/ubuntu/travelplanet_backend/vendor/12306-mcp/node_modules/12306-mcp/build/index.js; then
  cp -a /home/ubuntu/travelplanet_backend/vendor/12306-mcp \
    "$release_dir/vendor/12306-mcp"
elif sudo docker exec travelplanet-backend test -f \
  /app/vendor/12306-mcp/node_modules/12306-mcp/build/index.js; then
  sudo docker cp travelplanet-backend:/app/vendor/12306-mcp \
    "$release_dir/vendor/12306-mcp"
  sudo chown -R ubuntu:ubuntu "$release_dir/vendor"
else
  echo "No verified vendored 12306 MCP found; stop"
  exit 1
fi

sed -i \
  's#^RAIL_MCP_ENDPOINT=.*#RAIL_MCP_ENDPOINT=stdio:node /app/vendor/12306-mcp/node_modules/12306-mcp/build/index.js#' \
  "$release_dir/.env"
grep '^RAIL_MCP_ENDPOINT=' "$release_dir/.env"
```

## 4. Build and run the candidate

```bash
cd "$release_dir"
image="travelplanet-backend:$release"
sudo docker build -t "$image" .

sudo docker rm -f travelplanet-backend-candidate 2>/dev/null || true
sudo docker run -d \
  --name travelplanet-backend-candidate \
  --env-file "$release_dir/.env" \
  -p 127.0.0.1:8001:8000 \
  -v "$release_dir/data:/app/data" \
  -v "$release_dir/static:/app/static" \
  -v "$release_dir/logs:/app/logs" \
  "$image"

ready=0
for i in $(seq 1 120); do
  if curl -fsS http://127.0.0.1:8001/openapi.json >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
echo "ready=$ready"
if test "$ready" -ne 1; then
  sudo docker logs --tail=200 travelplanet-backend-candidate
  exit 1
fi
```

## 5. Validate the candidate

```bash
curl -fsS http://127.0.0.1:8001/openapi.json |
  python3 -c 'import json,sys; j=json.load(sys.stdin); print(j["info"], "paths="+str(len(j["paths"])))'

sudo docker exec travelplanet-backend-candidate sh -lc '
cd /app
/app/.venv/bin/python -m alembic current
/app/.venv/bin/python /app/deploy/probe_12306_mcp.py
'

sudo docker exec travelplanet-backend-candidate /app/.venv/bin/python -c '
import sqlite3
c=sqlite3.connect("/app/data/travelplanet.db")
tables=[r[0] for r in c.execute("select name from sqlite_master where type=? and name not like ?",("table","sqlite_%"))]
rows=sum(c.execute("select count(*) from \""+t.replace(chr(34),chr(34)*2)+"\"").fetchone()[0] for t in tables)
print("integrity="+c.execute("pragma integrity_check").fetchone()[0])
print("tables="+str(len(tables)))
print("rows="+str(rows))
c.close()'

sudo docker exec travelplanet-backend-candidate /app/.venv/bin/python -c '
from app.core.business_logging import BusinessLog
log=BusinessLog("deploy_probe")
log.write("probe", status="success")
print(log.path)'

find "$release_dir/logs" -maxdepth 1 -type f -name '*.log' -printf '%TY-%Tm-%Td %TH:%TM:%TS %p\n' | sort -r | head
find "$release_dir/static" -type f | wc -l
curl -fsS http://127.0.0.1:8001/api/reports | head -c 300
sudo docker logs --tail=150 travelplanet-backend-candidate
```

Compare migration, table/row counts, and static counts to the intended local snapshot. When MCP is enabled, require `server=12306-mcp`, a tool list, and `current-date=...` from the probe.

## 6. Cut over safely

Only proceed after candidate verification. Re-declare concrete variables if the shell session changed.

```bash
release=RELEASE
release_dir="/home/ubuntu/travelplanet_backend_release_$release"
image="travelplanet-backend:$release"
backup_dir="/home/ubuntu/travelplanet_backend_backup_$release"
old_container="travelplanet-backend-old-$release"

sudo docker stop travelplanet-backend-candidate
sudo docker stop travelplanet-backend
sudo docker rename travelplanet-backend "$old_container"

test ! -e "$backup_dir" || { echo "backup directory exists; stop"; exit 1; }
mv /home/ubuntu/travelplanet_backend "$backup_dir"
mv "$release_dir" /home/ubuntu/travelplanet_backend

sudo docker run -d \
  --name travelplanet-backend \
  --restart unless-stopped \
  --env-file /home/ubuntu/travelplanet_backend/.env \
  -p 8000:8000 \
  -v /home/ubuntu/travelplanet_backend/data:/app/data \
  -v /home/ubuntu/travelplanet_backend/static:/app/static \
  -v /home/ubuntu/travelplanet_backend/logs:/app/logs \
  "$image"
```

## 7. Verify production and public access

```bash
ready=0
for i in $(seq 1 120); do
  if curl -fsS http://127.0.0.1:8000/openapi.json >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
echo "ready=$ready"

sudo docker ps -a --filter 'name=travelplanet-backend' \
  --format 'NAME={{.Names}} IMAGE={{.Image}} STATUS={{.Status}} PORTS={{.Ports}}'
sudo docker logs --tail=150 travelplanet-backend
curl -fsS http://127.0.0.1:8000/openapi.json >/dev/null && echo 'local backend OK'
curl -fsS http://114.132.201.196:8000/openapi.json >/dev/null && echo 'public backend OK'

sudo docker exec travelplanet-backend sh -lc '
cd /app
/app/.venv/bin/python -m alembic current
/app/.venv/bin/python /app/deploy/probe_12306_mcp.py
test -d /app/logs && echo logs=mounted
'
```

Also probe the public endpoint from the release computer. Do not call deployment complete if only the server-local request succeeds.

## 8. Roll back if production verification fails

Use the exact recorded names. Restore the directory before restarting the old container so its bind mounts resolve to the old data/media.

```bash
release=RELEASE
backup_dir="/home/ubuntu/travelplanet_backend_backup_$release"
old_container="travelplanet-backend-old-$release"

sudo docker rm -f travelplanet-backend
mv /home/ubuntu/travelplanet_backend \
  "/home/ubuntu/travelplanet_backend_failed_$release"
mv "$backup_dir" /home/ubuntu/travelplanet_backend
sudo docker rename "$old_container" travelplanet-backend
sudo docker start travelplanet-backend
curl -fsS http://127.0.0.1:8000/openapi.json >/dev/null && echo 'rollback backend OK'
```

Do not delete the failed release, backup, old image, or uploaded archive until the incident is understood and the user explicitly approves cleanup.
