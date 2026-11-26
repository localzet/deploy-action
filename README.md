# Localzet Deploy

[Русская документация](README.ru.md)

Universal Docker Compose deployment over SSH. The runner transfers a Git commit snapshot; the server manages releases; images can be built on the server or runner. No framework-specific adapters or mandatory registry.

**Preview:** test in staging before production. The action is not a zero-downtime orchestrator.

## Usage

Copy [examples/deploy.yml](examples/deploy.yml) to your project's `.github/workflows/deploy.yml`. Pin the action to a reviewed commit SHA for production. The example uses `@main` until a stable release is published.

```yaml
- uses: actions/checkout@v4
  with:
    persist-credentials: false
- uses: localzet/deploy-action@main
  env:
    DEPLOY_HOST: ${{ secrets.SERVER_HOST }}
    DEPLOY_USER: ${{ secrets.SERVER_USER }}
    DEPLOY_SSH_KEY: ${{ secrets.SERVER_SSH_KEY }}
    DEPLOY_KNOWN_HOSTS: ${{ secrets.SERVER_KNOWN_HOSTS }}
```

Use `DEPLOY_PASSWORD` instead of the key if necessary. If both are set, the key wins. `DEPLOY_PORT` defaults to 22. `DEPLOY_ADDRESS`, `DEPLOY_IP` and the equivalent `SERVER_*` aliases are supported. GitHub secrets must be explicitly mapped to environment variables.

Set `DEPLOY_KNOWN_HOSTS` to a trusted OpenSSH known_hosts entry verified against the server through an independent channel. For nonstandard ports use `[host]:port`. Do not blindly trust ssh-keyscan output.

## Inputs

| Input | Default | Purpose |
|---|---|---|
| `compose-files` | automatic / empty | Newline-separated Compose paths; autodetect if empty. |
| `project-name` | automatic / empty | Stable Compose project name. |
| `deploy-path` | automatic / empty | Absolute dedicated remote directory. |
| `shared-paths` | automatic / empty | Newline-separated paths linked to existing shared data. |
| `profiles` | automatic / empty | Newline-separated Compose profiles. |
| `pull-policy` | `always` | Pull policy for registry images: always, missing, never. |
| `wait-timeout` | `120` | Compose readiness timeout in seconds. |
| `health-url` | automatic / empty | Optional HTTP(S) readiness URL checked on server. |
| `health-timeout` | `60` | HTTP readiness timeout in seconds. |
| `keep-releases` | `3` | Number of retained successful releases. |
| `operation` | `deploy` | deploy or rollback. |

| `build-mode` | `vps` | Build location: vps, runner or registry. |
| `registry-prefix` | empty | Fully qualified image repository for registry mode. |
| `backup` | empty | Commands in old running services, before migration. One SERVICE COMMAND per line. |
| `pre-deploy` | empty | One-off commands in new images before activation. One SERVICE COMMAND per line. |
| `post-deploy` | empty | Commands in new running services after readiness. One SERVICE COMMAND per line. |

Outputs: `release`, `project`.

Compose files are autodetected when there is a single standard file in the root or a unique YAML file with `compose` in its name. Specify multiple files in order explicitly. Overrides are not automatically included.

### Git submodules

For repositories with submodules, configure checkout with `submodules: recursive` and appropriate credentials. The archive recursively includes the pinned commits of initialized submodules, never their `.git` metadata or untracked files. A mismatching or missing checkout fails before SSH. Git LFS objects are not materialized by git archive.

## Server requirements and persistence

Linux, Python with `tarfile.data_filter` (3.12+ or patched older versions), current Docker Compose v2, and an SSH account with Docker access and permission to write the deployment directory. The Linux runner needs Python 3, Git, OpenSSH; password auth uses sshpass. Registry credentials must be configured separately on the server.

Default directory: `$HOME/.local/share/localzet-deploy/PROJECT`. Each release has a source tree and frozen local image tags. The Compose project name is stable. Prepare `shared/.env` on the server if needed. Configure named volumes for databases and use `${DEPLOY_SHARED_DIR}` in Compose for persistent bind mounts. `shared-paths` links existing shared entries into the source tree; tracked files are never overwritten. Keep secrets out of Git and the Docker build context.

An existing unmanaged Compose project with the same name is rejected. Migrate it manually after backups; do not change project names casually because this changes default named volumes and networks.

Deployment prepares images first, then activates Compose with `--wait`. Optional HTTP check requires a 2xx response. If activation fails, the previous release is reactivated. Manual rollback: set `operation: rollback` with the same project and deployment path. Rollback restores application images, not database migrations, shared files or external services. No volume deletion or global prune is performed.

Limitations: Git LFS objects are not bundled; no zero-downtime guarantee; no automatic framework migrations; no adoption of an existing unmanaged Compose project. Use only trusted branches and reviewed Compose definitions; Docker access can grant root-equivalent privileges.

## Build modes and registry

- `vps` (default): build on the target. No registry is required for application images.
- `runner`: build the committed snapshot on the runner, transfer images via SSH using docker save/load. Requires Docker Compose on the runner and matching target architecture or explicit compatible platform.
- `registry`: build on the runner and push unique release tags to `registry-prefix` (e.g. `ghcr.io/owner/project`). The server pulls and checks image IDs before creating local release tags. Both sides must authenticate to the registry before deployment. See [GHCR example](examples/ghcr.yml). Registry retention is independent of server release retention.

Runner modes do not copy production .env to the runner. Supply only build-time interpolation values through the optional `DEPLOY_BUILD_ENV` secret (dotenv format, outside the build context). Runtime service env files are not resolved on the runner; a build-only model is generated from the Compose configuration. For mandatory runtime interpolation (for example `${DB_HOST:?required}`), supply non-sensitive placeholder values in `DEPLOY_BUILD_ENV` unless the variable actually affects the build. The build and server Compose service sets must match. Do not use server-only bind mounts, absolute build paths or runtime secrets as build inputs. External registry images are pulled on the target in all modes. Multi-platform manifest builds are not provided.

## Hooks and backups

Each line is a service name followed by a command and shell-style quoted arguments. A shell is not implied; use explicit `sh -c` when needed.

```yaml
with:
  backup: |
    db /usr/local/bin/backup
  pre-deploy: |
    app php artisan migrate --force
  post-deploy: |
    app php artisan queue:restart
```

The backup hook executes in the current running service (skipped on first deployment). The pre-hook uses the new image in a one-off container with no dependency startup, so a migration requiring a database needs an already reachable database. Post-hooks run in new running services after readiness; failure triggers application rollback. Hooks run only for deploy, not recovery or manual rollback. Backups must be implemented by the application and saved to persistent storage. Data mutations cannot be undone by image rollback.

## SSH jump hosts

Optional `DEPLOY_JUMP_HOST`, `DEPLOY_JUMP_USER`, `DEPLOY_JUMP_PORT` (22), `DEPLOY_JUMP_SSH_KEY`, `DEPLOY_JUMP_KNOWN_HOSTS`. The jump uses public-key authentication and its own verified host key; if no separate key or known_hosts is supplied, the target values are reused. Target authentication can use password or key.

## Existing projects and multiple servers

Read [migration and staging guidance](docs/migration.md) before deploying an existing stack. For sequential deployment to separate servers, see [matrix example](examples/multi-server.yml). This does not provide atomic multi-host rollback.

## Tests

`python3 -m unittest discover -s tests -v`

`python3 tests/e2e.py` (requires Docker; disposable Linux runner).

`bash tests/ssh-e2e.sh` (disposable runner only: creates a local test account and SSH daemon; checks key and password authentication, host key rejection and rollback).

MIT license.
