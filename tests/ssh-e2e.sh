#!/usr/bin/env bash
# Disposable Linux CI runner only: creates an isolated loopback SSH daemon.
set -euo pipefail
action_dir="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
test_user=localzet-ci-deploy
test_password="$(openssl rand -hex 24)"
port=22222
cleanup() {
  docker rm -f localzet-ci-registry >/dev/null 2>&1 || true
  if [[ -f "$work/sshd.pid" ]]; then sudo kill "$(cat "$work/sshd.pid")" || true; fi
  sudo userdel -r "$test_user" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
sudo apt-get update -qq
sudo apt-get install -y -qq openssh-server sshpass
sudo useradd -m -s /bin/bash -G docker "$test_user"
printf '%s:%s\n' "$test_user" "$test_password" | sudo chpasswd
ssh-keygen -q -t ed25519 -N '' -f "$work/host_key"
ssh-keygen -q -t ed25519 -N '' -f "$work/client_key"
sudo install -d -m 700 -o "$test_user" "/home/$test_user/.ssh"
sudo install -m 600 -o "$test_user" "$work/client_key.pub" "/home/$test_user/.ssh/authorized_keys"
cat > "$work/sshd_config" <<EOF
Port $port
ListenAddress 127.0.0.1
HostKey $work/host_key
PidFile $work/sshd.pid
AuthorizedKeysFile .ssh/authorized_keys
PasswordAuthentication yes
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
AllowUsers $test_user
UsePAM yes
EOF
sudo mkdir -p /run/sshd
sudo /usr/sbin/sshd -f "$work/sshd_config" -E "$work/sshd.log"
mkdir "$work/repository"
cd "$work/repository"
git init -q
git config user.name CI
git config user.email ci@example.invalid
cat > compose.yml <<'EOF'
services:
  app:
    build: .
    healthcheck:
      test: [CMD-SHELL, 'test -f /healthy']
      interval: 1s
      timeout: 1s
      retries: 3
EOF
write_version() {
  printf 'FROM alpine:3.22\nRUN echo %s >/version && touch /healthy\nCMD ["tail","-f","/dev/null"]\n' "$1" > Dockerfile
  git add Dockerfile compose.yml
  git commit -qm "Version $1"
}
export GITHUB_WORKSPACE="$PWD"
export GITHUB_REPOSITORY=localzet/ssh-smoke-test
export DEPLOY_HOST=127.0.0.1 DEPLOY_PORT="$port" DEPLOY_USER="$test_user"
export DEPLOY_KNOWN_HOSTS="[127.0.0.1]:$port $(cat "$work/host_key.pub")"
export ACTION_PROJECT_NAME=localzet-ssh-ci
export ACTION_WAIT_TIMEOUT=30
export ACTION_OPERATION=deploy
# Isolate outputs from the surrounding CI step.
export GITHUB_OUTPUT="$work/outputs"
export DEPLOY_SSH_KEY="$(cat "$work/client_key")"
unset DEPLOY_PASSWORD
write_version 1
python3 "$action_dir/scripts/client.py"
cid() { docker ps -q --filter label=com.docker.compose.project=localzet-ssh-ci --filter label=com.docker.compose.service=app; }
[[ "$(docker exec "$(cid)" cat /version)" == 1 ]]
unset DEPLOY_SSH_KEY
export DEPLOY_PASSWORD="$test_password"
export ACTION_BUILD_MODE=runner
write_version 2
python3 "$action_dir/scripts/client.py"
[[ "$(docker exec "$(cid)" cat /version)" == 2 ]]
# Registry mode with a disposable loopback registry; verify SSH jump key as well.
docker run -d --name localzet-ci-registry -p 127.0.0.1:25000:5000 registry:2
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:25000/v2/ >/dev/null; then break; fi
  sleep 1
done
export ACTION_BUILD_MODE=registry ACTION_REGISTRY_PREFIX=127.0.0.1:25000/localzet/test
export DEPLOY_JUMP_HOST=127.0.0.1 DEPLOY_JUMP_PORT="$port" DEPLOY_JUMP_USER="$test_user"
export DEPLOY_JUMP_SSH_KEY="$(cat "$work/client_key")"
export ACTION_PRE_DEPLOY='app sh -c "test -f /healthy"'
export ACTION_POST_DEPLOY='app sh -c "test -f /healthy"'
write_version 3
python3 "$action_dir/scripts/client.py"
[[ "$(docker exec "$(cid)" cat /version)" == 3 ]]
unset DEPLOY_JUMP_HOST DEPLOY_JUMP_PORT DEPLOY_JUMP_USER DEPLOY_JUMP_SSH_KEY
unset ACTION_PRE_DEPLOY ACTION_POST_DEPLOY
export ACTION_OPERATION=rollback
python3 "$action_dir/scripts/client.py"
[[ "$(docker exec "$(cid)" cat /version)" == 2 ]]
# Wrong trusted host key must fail before executing remote commands.
export DEPLOY_KNOWN_HOSTS="[127.0.0.1]:$port $(cat "$work/client_key.pub")"
if python3 "$action_dir/scripts/client.py"; then
  echo "Unexpectedly accepted a wrong host key" >&2
  exit 1
fi
[[ "$(docker exec "$(cid)" cat /version)" == 2 ]]
docker rm -f "$(cid)"
echo "SSH key, password, host verification and rollback smoke test passed"
