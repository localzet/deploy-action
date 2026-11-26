#!/usr/bin/env python3
"""Server-side release manager. Requires Linux, Python with data_filter, Compose v2."""
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request


STANDARD = ('compose.yaml', 'compose.yml', 'docker-compose.yaml', 'docker-compose.yml')


def run(args, cwd=None, capture=False, quiet=False, env=None):
    return subprocess.run([str(x) for x in args], cwd=cwd, check=True, text=True, env=env,
                          stdout=subprocess.PIPE if capture else (subprocess.DEVNULL if quiet else None),
                          stderr=subprocess.PIPE if quiet else None).stdout


def relative(value):
    p = PurePosixPath(value)
    if not value or p.is_absolute() or '..' in p.parts or p == PurePosixPath('.') or '\\' in value:
        raise ValueError(f'Invalid repository-relative path: {value!r}')
    return p


def safe_path(root, value):
    p = root / relative(value)
    if not p.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes release: {value!r}')
    return p


def discover(root, requested):
    if requested:
        files = [str(relative(x)) for x in requested]
        if len(set(files)) != len(files) or any(not safe_path(root, x).is_file() for x in files):
            raise ValueError('Explicit compose-files contain missing or duplicate files')
        return files
    standard = [x for x in STANDARD if (root/x).is_file()]
    if len(standard) == 1:
        return standard
    if len(standard) > 1:
        raise ValueError('Multiple standard Compose files; specify compose-files explicitly')
    candidates = sorted(str(p.relative_to(root)) for p in root.rglob('*')
                        if p.is_file() and not p.is_symlink() and 'compose' in p.stem.lower()
                        and p.suffix.lower() in ('.yaml', '.yml') and '.github' not in p.relative_to(root).parts)
    if len(candidates) != 1:
        raise ValueError(f'Compose discovery found {len(candidates)} candidates; specify compose-files explicitly: {candidates[:20]}')
    return candidates


def atomic_json(path, data):
    temp = path.with_suffix('.tmp')
    with temp.open('w') as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(data, f, indent=2); f.flush(); os.fsync(f.fileno())
    temp.replace(path)


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def image_tag(project, release, service):
    slug = re.sub(r'[^a-z0-9_-]', '-', service.lower()).strip('-_')[:35] or 'service'
    suffix = hashlib.sha256(service.encode()).hexdigest()[:10]
    return f'localzet-deploy/{project}:{release}-{slug}-{suffix}'


def active_services(model, profiles):
    return {name: svc for name, svc in model['services'].items()
            if not svc.get('profiles') or '*' in profiles or set(svc['profiles']) & set(profiles)}


def link_shared(release, shared, paths, files):
    requested = list(paths)
    if (shared/'.env').is_file():
        requested.append('.env')
    normalized = sorted(set(str(relative(x)) for x in requested))
    for i, a in enumerate(normalized):
        if any(PurePosixPath(a) in PurePosixPath(b).parents for b in normalized[i+1:]):
            raise ValueError('Overlapping shared-paths')
        target = safe_path(shared, a)
        if not target.exists():
            raise ValueError(f'Create shared/{a} on the server before deployment')
        dest = safe_path(release, a)
        if dest.exists() or dest.is_symlink():
            raise ValueError(f'Shared path {a} already exists in tracked source; remove it from Git')
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(target)
    if (shared/'.env').is_file():
        parent = (release / files[0]).parent
        if parent != release:
            dest = parent/'.env'
            if dest.exists() or dest.is_symlink():
                raise ValueError('Nested Compose directory already contains .env')
            dest.symlink_to(shared/'.env')


class Manager:
    def __init__(self, cfg, incoming):
        self.cfg, self.incoming = cfg, incoming
        self.project = cfg['project']
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', self.project):
            raise ValueError('Invalid project')
        root = Path(cfg['root']) if cfg['root'] else Path.home()/'.local/share/localzet-deploy'/self.project
        if not root.is_absolute() or root == Path('/'):
            raise ValueError('deploy-path must be an absolute project-specific directory')
        self.root = root.resolve()
        if self.root == Path('/'):
            raise ValueError('deploy-path resolves to root')
        self.releases = self.root/'releases'
        self.shared = self.root/'shared'
        self.state_path = self.root/'state.json'
        self.pending_path = self.root/'pending.json'
        for p in (self.root, self.releases, self.shared):
            p.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state = None

    def directory(self, rid):
        if not re.fullmatch(r'[a-f0-9]{32}', rid):
            raise ValueError('Invalid release ID')
        return self.releases/rid

    def meta(self, rid):
        return read_json(self.directory(rid)/'release.json')

    def compose(self, meta, frozen=True):
        release = self.directory(meta['id'])/'source'
        args = ['docker', 'compose', '--project-name', self.project,
                '--project-directory', str((release/meta['files'][0]).parent)]
        if (self.shared/'.env').is_file():
            args += ['--env-file', str(self.shared/'.env')]
        for profile in meta['profiles']:
            args += ['--profile', profile]
        for f in meta['files']:
            args += ['-f', str(release/f)]
        if frozen:
            args += ['-f', str(self.directory(meta['id'])/'images.json')]
        return args, release

    def call(self, meta, *args, frozen=True, **kw):
        base, cwd = self.compose(meta, frozen)
        return run(base+list(args), cwd=cwd, env={**os.environ, "DEPLOY_SHARED_DIR": str(self.shared)}, **kw)

    def prepare(self, rid):
        dest = self.directory(rid)
        source = dest/'source'
        source.mkdir(parents=True, mode=0o700)
        with tarfile.open(self.incoming/'source.tar.gz') as archive:
            if not hasattr(tarfile, 'data_filter'):
                raise RuntimeError('Python tarfile.data_filter required (Python 3.12+ or security backport)')
            archive.extractall(source, filter='data')
        files = discover(source, self.cfg['files'])
        for f in files:
            safe_path(source, f)
        link_shared(source, self.shared, self.cfg['shared'], files)
        meta = dict(id=rid, files=files, profiles=self.cfg['profiles'], wait=self.cfg['wait'],
                    health_url=self.cfg['health_url'], health_wait=self.cfg['health_wait'], commit=self.cfg['commit'], tags=[])
        atomic_json(dest/'release.json', meta)
        model = json.loads(self.call(meta, 'config', '--format', 'json', frozen=False, capture=True))
        services = active_services(model, meta['profiles'])
        if not services:
            raise ValueError('No active Compose services')
        tags = {name: image_tag(self.project, rid, name) for name in services}
        meta['tags'] = list(tags.values())
        atomic_json(dest/'release.json', meta)
        override = {'services': {name: {'image': tag, 'pull_policy': 'never'} for name, tag in tags.items()}}
        atomic_json(dest/'images.json', override)
        builds = [name for name, svc in services.items() if svc.get('build')]
        external = [name for name in services if name not in builds]
        if external and self.cfg['pull_policy'] != 'never':
            self.call(meta, 'pull', '--policy', self.cfg['pull_policy'], *external, frozen=False)
        for name in external:
            original = services[name].get('image')
            if not original:
                raise ValueError(f'No image or build for {name}')
            run(['docker', 'image', 'tag', original, tags[name]])
        if builds:
            self.call(meta, 'build', *builds)
        for tag in tags.values():
            run(['docker', 'image', 'inspect', tag], quiet=True)
        return meta

    def health(self, meta):
        url = meta['health_url']
        if not url:
            return
        if not url.startswith(('http://', 'https://')):
            raise ValueError('health-url must use HTTP(S)')
        deadline = time.monotonic()+meta['health_wait']
        while True:
            try:
                remaining = deadline-time.monotonic()
                with urllib.request.urlopen(url, timeout=max(0.1, min(5, remaining))) as response:
                    if 200 <= response.status < 300:
                        return
            except (OSError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('HTTP readiness check failed')
            time.sleep(min(2, max(0, deadline-time.monotonic())))

    def activate(self, rid):
        meta = self.meta(rid)
        for tag in meta['tags']:
            run(['docker', 'image', 'inspect', tag], quiet=True)
        self.call(meta, 'up', '-d', '--no-build', '--pull', 'never', '--remove-orphans',
                  '--wait', '--wait-timeout', str(meta['wait']))
        self.health(meta)

    def remove_release(self, rid):
        dest = self.directory(rid)
        meta = self.meta(rid) or {}
        for tag in meta.get('tags', []):
            try:
                run(['docker', 'image', 'rm', tag], quiet=True)
            except subprocess.CalledProcessError:
                pass
        if dest.exists():
            shutil.rmtree(dest)

    def recover(self):
        pending = read_json(self.pending_path)
        if not pending:
            return
        candidate, previous = pending['candidate'], pending['previous']
        if self.state['current'] == candidate:
            self.pending_path.unlink()
            return
        print('Recovering interrupted or failed deployment', flush=True)
        if previous:
            self.activate(previous)
        else:
            self.call(self.meta(candidate), 'down', '--remove-orphans')
        self.pending_path.unlink()

    def switch(self, candidate, order):
        previous = self.state['current']
        atomic_json(self.pending_path, {'candidate': candidate, 'previous': previous})
        try:
            self.activate(candidate)
            self.state = {'project': self.project, 'current': candidate, 'history': order}
            atomic_json(self.state_path, self.state)
        except BaseException:
            self.state = read_json(self.state_path, {'project': self.project, 'current': previous, 'history': []})
            self.recover()
            raise
        self.pending_path.unlink()

    def execute(self):
        with (self.root/'deploy.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.state = read_json(self.state_path, {'project': self.project, 'current': None, 'history': []})
            if self.state['project'] != self.project:
                raise ValueError('deploy-path already belongs to another Compose project')
            run(['docker', 'compose', 'version'])
            self.recover()
            history = self.state['history']
            if self.cfg['operation'] == 'rollback':
                if len(history) < 2:
                    raise ValueError('No previous successful release')
                candidate = history[1]
                order = [candidate, history[0]] + history[2:]
                self.switch(candidate, order)
            else:
                if not self.state['current']:
                    existing = run(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={self.project}'], capture=True)
                    if existing.strip():
                        raise ValueError('Existing unmanaged Compose project; choose a new name or migrate manually')
                candidate = self.cfg['release']
                try:
                    self.prepare(candidate)
                    self.switch(candidate, [candidate]+history)
                except BaseException:
                    if not self.pending_path.exists() and self.state['current'] != candidate:
                        self.remove_release(candidate)
                    raise
            self.state['history'] = self.state['history'][:self.cfg['keep']]
            atomic_json(self.state_path, self.state)
            for path in self.releases.iterdir():
                if path.is_dir() and re.fullmatch(r'[a-f0-9]{32}', path.name) and path.name not in self.state['history']:
                    try:
                        self.remove_release(path.name)
                    except OSError:
                        print('Warning: old release cleanup failed', flush=True)
            atomic_json(self.incoming/'result.json', {'release': candidate, 'project': self.project})
            print(f'Active release: {candidate}', flush=True)


def main():
    os.umask(0o077)
    for key in ('COMPOSE_FILE', 'COMPOSE_PROJECT_NAME', 'COMPOSE_PROFILES', 'COMPOSE_ENV_FILES', 'COMPOSE_DISABLE_ENV_FILE'):
        os.environ.pop(key, None)
    incoming = Path(sys.argv[1])
    Manager(read_json(incoming/'config.json'), incoming).execute()


if __name__ == '__main__':
    main()
