"""Build committed Compose sources on a Linux runner, without SSH credentials."""
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

from remote import discover, safe_path, active_services, image_tag


def registry_prefix(value):
    # A fully qualified registry and repository, without tag/digest.
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*(?::[0-9]{1,5})?/[a-z0-9]+(?:[._/-][a-z0-9]+)*', value):
        raise ValueError('registry-prefix must be a lowercase registry/repository without a tag')
    host = value.split('/')[0]
    if '.' not in host and ':' not in host and host != 'localhost':
        raise ValueError('registry-prefix must identify an explicit registry host')
    return value


def docker_environment():
    allowed = {'PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'DOCKER_CONFIG',
               'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
               'SSH_AUTH_SOCK', 'XDG_CONFIG_HOME', 'XDG_RUNTIME_DIR', 'LANG', 'LC_ALL',
               'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy'}
    return {k: v for k, v in os.environ.items() if k in allowed}


def build_images(cfg, tmp, build_env):
    source = tmp/'build-source'
    source.mkdir()
    with tarfile.open(tmp/'source.tar.gz') as archive:
        archive.extractall(source, filter='data')
    files = discover(source, cfg['files'])
    for f in files:
        safe_path(source, f)
    env_file = tmp/'build.env'
    env_file.write_text(build_env + '\n')
    env_file.chmod(0o600)
    env = docker_environment()
    def call(args, capture=False):
        return subprocess.run([str(x) for x in args], cwd=source, env=env, check=True, text=True,
                              stdout=subprocess.PIPE if capture else None).stdout
    base = ['docker', 'compose', '--project-name', cfg['project'],
            '--project-directory', str((source/files[0]).parent), '--env-file', str(env_file)]
    for p in cfg['profiles']:
        base += ['--profile', p]
    for f in files:
        base += ['-f', str(source/f)]
    model = json.loads(call(base+['config', '--format', 'json'], capture=True))
    services = active_services(model, cfg['profiles'])
    tags = {name: image_tag(cfg['project'], cfg['release'], name)
            for name, svc in services.items() if svc.get('build')}
    if not tags:
        return {}
    override = tmp/'build-images.json'
    override.write_text(json.dumps({'services': {n: {'image': t} for n, t in tags.items()}}))
    registry_tags = []
    manifest = {}
    try:
        call(base+['-f', str(override), 'build', *tags])
        for name, tag in tags.items():
            ident = call(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], capture=True).strip()
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', ident):
                raise ValueError('Unexpected Docker image ID')
            entry = {'id': ident}
            if cfg['build_mode'] == 'registry':
                ref = registry_prefix(cfg['registry_prefix'])+':'+tag.rsplit(':', 1)[1]
                registry_tags.append(ref)
                call(['docker', 'image', 'tag', tag, ref])
                call(['docker', 'push', ref])
                entry['ref'] = ref
            manifest[name] = entry
        if cfg['build_mode'] == 'runner':
            call(['docker', 'image', 'save', '-o', str(tmp/'images.tar'), *tags.values()])
        return manifest
    finally:
        for tag in [*tags.values(), *registry_tags]:
            subprocess.run(['docker', 'image', 'rm', tag], env=env, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
