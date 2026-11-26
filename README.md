# Localzet Deploy

Universal Docker Compose deployment over SSH. The runner transfers a Git commit snapshot; the server builds images and manages releases. No framework-specific adapters or mandatory registry.

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

Outputs: `release`, `project`.

Compose files are autodetected when there is a single standard file in the root or a unique YAML file with `compose` in its name. Specify multiple files in order explicitly. Overrides are not automatically included.

## Server requirements and persistence

Linux, Python with `tarfile.data_filter` (3.12+ or patched older versions), current Docker Compose v2, and an SSH account with Docker access and permission to write the deployment directory. The Linux runner needs Python 3, Git, OpenSSH; password auth uses sshpass. Registry credentials must be configured separately on the server.

Default directory: `$HOME/.local/share/localzet-deploy/PROJECT`. Each release has a source tree and frozen local image tags. The Compose project name is stable. Prepare `shared/.env` on the server if needed. Configure named volumes for databases and use `${DEPLOY_SHARED_DIR}` in Compose for persistent bind mounts. `shared-paths` links existing shared entries into the source tree; tracked files are never overwritten. Keep secrets out of Git and the Docker build context.

An existing unmanaged Compose project with the same name is rejected. Migrate it manually after backups; do not change project names casually because this changes default named volumes and networks.

Deployment prepares images first, then activates Compose with `--wait`. Optional HTTP check requires a 2xx response. If activation fails, the previous release is reactivated. Manual rollback: set `operation: rollback` with the same project and deployment path. Rollback restores application images, not database migrations, shared files or external services. No volume deletion or global prune is performed.

Limitations: Git submodules and LFS objects are not bundled; no zero-downtime guarantee; builds run on the VPS; no automatic framework migrations; no adoption of an existing unmanaged Compose project. Use only trusted branches and reviewed Compose definitions; Docker access can grant root-equivalent privileges.

## Tests

`python3 -m unittest discover -s tests -v`

`python3 tests/e2e.py` (requires Docker; disposable Linux runner).

MIT license.
