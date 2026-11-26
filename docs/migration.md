# Existing Compose projects and migration

Do not enable a new deployment workflow against a live stack until its Compose project name, network names, volumes, bind mounts and runtime environment are understood.

## Preflight

1. On the server, run docker compose ls and inspect the existing stack with docker compose ps and docker inspect. Record com.docker.compose.project and the existing volume names.
2. Keep an independently tested database backup, the original Compose file, the runtime dotenv, and existing image IDs. Do not use down -v, prune, or change the project name. The action refuses a running unmanaged project with the same name; it does not import its history.
3. Select a dedicated deployment directory. Prepare shared/.env and any persistent paths with the existing values and permissions. Keep the original release available for a manual recovery.
4. Test a separate staging project first. If fixed container names or published ports are present, a staging Compose file must use different names/ports. If the deployment uses Traefik, do not attach the staging project to production routers.
5. For the first managed release, arrange an approved maintenance window, stop the original stack without removing volumes (and retain its original files), and remove the stopped containers using the original Compose definition. Confirm the external networks and existing named volumes still exist. Use the exact original project name. The first managed release has no action-managed predecessor; if it fails, use the original stack and backups to recover manually.

## Example: Hackathon application

The current parent Compose file uses project name hack-rnd, external network external, fixed container names and several named volumes. It also requires a production .env and recursively checked-out submodules. The default hash-based project name must be overridden to preserve the existing named volumes. The Action is not an automatic zero-downtime replacement for this stack.

Example inputs:

    compose-files: docker-compose.yml
    project-name: hack-rnd
    deploy-path: /opt/hack-rnd-managed
    build-mode: vps
    wait-timeout: 900
    health-url: https://api.hack-rnd.ru/up

Use the existing verified health endpoint, not a guessed endpoint. Configure checkout with recursive submodules and credentials that can read all of them (including SSH URLs). These values are an illustration only; do not run a live migration without checking the actual server state.

## Multiple servers

Use GitHub Actions matrix jobs with a different GitHub Environment per target. Each environment has its own server credentials. A matrix deploy is not an atomic distributed transaction; keep fail-fast disabled, review per-host outcomes, and use a rolling strategy or external orchestrator if service continuity is required.

## Blue/green and systemd

An arbitrary Compose stack cannot safely run two copies when container_name, fixed host ports, writable state, database migrations or identical Traefik routers are present. Zero-downtime deployments need a separate application contract with isolated slots, external data services and an atomic proxy switch. Do not claim zero downtime for regular Compose up. Systemd deployment is outside this Docker Compose action's contract.
