# One instance per host

## Symptom

`docker ps -a` shows **two containers from the same image**. One is `Up (healthy)`, the other is
`Exited (0)`. Later they swap: when the running one stops, the exited one takes over, and vice
versa. `docker logs` on the loser shows the app failing to bind its port (or the container being
stopped) and then coming back.

## Why it happens

Two containers publishing the same host port cannot both run. Each one carries a restart policy, so
the loser restarts, fails to bind, exits, and restarts again. Whichever wins the race holds the
port; when it is restarted or stopped, the other one grabs the port first. The result looks like a
flapping deployment even though each container is doing the same job correctly.

This repository contains more than one way to start the same service, which is how a host ends up
with two:

| Start path | Container name | Host port | Restart policy |
|---|---|---|---|
| `docker compose up -d` (`compose.yaml`) | `sentinelzone` (was `<directory>-sentinel-1` before pinning) | `8000` | `unless-stopped` |
| README quick start | `sentinelzone` | `127.0.0.1:18080` | none (`--rm`) |
| Media-complete image recipe in `PRODUCTION.md` | `sentinelzone` | `127.0.0.1:18080` | none |
| `scripts/docker_*_smoke.py` | created through the Docker API, unnamed | ephemeral | none |
| `deploy/sentinelzone-edge.service` | not a container (runs Python directly) | — | `Restart=always` |

**Rule: exactly one supervisor per host.** Use Compose *or* a manual `docker run` *or* the systemd
unit — never two of them for the same image and port.

## Diagnose

```bash
python scripts/docker_instance_audit.py          # read-only; exits 1 when duplicates exist
```

The same information without this repository:

```bash
docker ps -a --filter ancestor=sentinelzone-ai:production \
  --format 'table {{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'

for c in $(docker ps -aq --filter ancestor=sentinelzone-ai:production); do
  docker inspect --format \
    '{{.Id}} name={{.Name}} project={{index .Config.Labels "com.docker.compose.project"}} service={{index .Config.Labels "com.docker.compose.service"}} dir={{index .Config.Labels "com.docker.compose.project.working_dir"}} restart={{.HostConfig.RestartPolicy.Name}} ports={{json .HostConfig.PortBindings}}' \
    "$c"
done
```

The `project`/`dir` labels tell you where a Compose container came from. A container with **no**
`com.docker.compose.project` label was started by `docker run` (or the Docker API) and has a
supervisor somewhere else: a systemd unit, cron, a CI job, or a shell session.

On the host, confirm only one process owns the port:

```bash
sudo ss -ltnp '( sport = :8000 )'
```

## Fix

1. **Pick the keeper** — normally the Compose stack, because it owns the persistent volume and the
   documented upgrade path. In the observed case that is `camera-project-sentinel-1` (`Up (healthy)`).
2. **Neutralise the duplicate before removing it.** Removing a container that has
   `restart: always`/`unless-stopped` while its supervisor is still active just recreates it:

   ```bash
   docker update --restart=no <duplicate-id>
   docker rm -f <duplicate-id>
   ```
3. **Remove the second start path** — this is the half that actually fixes the problem:
   - Compose duplicate: `docker compose -p <project> --project-directory <dir> -f <config> down`,
     then delete or stop the other checkout.
   - No Compose labels: `systemctl list-units --type=service --all | grep -i sentinel`,
     `crontab -l | grep -i sentinel`, and check for a script or CI job that runs `docker run`.
   - A leftover container from an interrupted `scripts/docker_*_smoke.py` run is safe to remove; the
     scripts delete their containers in a `finally` block, so a leftover means the run was killed.
4. **Verify**: the audit exits `0`, `docker ps -a` lists exactly one container from the image,
   `curl -fsS http://127.0.0.1:8000/health` returns 200, and `ss -ltnp` shows one listener on 8000.

## Prevent

Applied in `compose.yaml` on this branch:

- `name: sentinelzone` pins the Compose **project** name, so `docker compose up -d` from a different
  directory, a second clone, or with `-p <other>` still addresses the same stack instead of creating
  a second one.
- `container_name: sentinelzone` makes a second instance fail loudly at create time
  (`container name "/sentinelzone" is already in use`) instead of quietly starting a duplicate.
- The data volume is pinned to `sentinelzone-state`, so the persisted camera setup, surveys and
  manifests survive a checkout directory rename or move.

`container_name` disables `docker compose up --scale`; that is intentional — the application
documents a single worker, since extra workers would open duplicate camera connections.

Repeatable check: `python scripts/docker_instance_audit.py` (exit `0` = exactly one instance) and
the owned suite `python scripts/run_project_tests.py deployment-runtime`.

## One-time migration for a host that already runs the old names

The pinned names differ from what an existing deployment is running
(`<directory>-sentinel-1` and volume `<directory>_sentinel-data`), so migrate once, in this order:

```bash
cd <checkout>

# 1. preserve the persisted state (camera setup draft, surveys, manifests) if you need it
docker volume create sentinelzone-state
docker run --rm --user root \
  -v camera-project_sentinel-data:/from:ro \
  -v sentinelzone-state:/to \
  --entrypoint sh sentinelzone-ai:production \
  -c 'cp -a /from/. /to/ && chown -R 10001:10001 /to'

# 2. remove the old project's container and its restart path
docker rm -f camera-project-sentinel-1

# 3. start the single pinned stack
docker compose up -d --build

# 4. verify
python scripts/docker_instance_audit.py
docker ps -a --filter ancestor=sentinelzone-ai:production
curl -fsS http://127.0.0.1:8000/health
```

`docker volume create` before the copy matters: an existing volume is never re-initialised by
Compose, so the copied state is the state the service starts with. `--user root` is required to
write into a freshly created volume; the `chown` restores ownership for the runtime user (UID 10001).
