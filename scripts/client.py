#!/usr/bin/env python3
"""Runner-side SSH transport."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import tarfile
import uuid

from runner import build_images, registry_prefix


def env(*names):
    return next((os.environ[n] for n in names if os.environ.get(n)), "")


def project_name(repository):
    slug = re.sub(r'[^a-z0-9_-]+', '-', repository.lower()).strip('-_')[:42] or 'project'
    return f'{slug}-{hashlib.sha256(repository.encode()).hexdigest()[:8]}'


def lines(name):
    return [x.strip() for x in env(name).splitlines() if x.strip()]


def integer(name, default, minimum, maximum):
    value = int(env(name) or default)
    if not minimum <= value <= maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}')
    return value


def configuration():
    project = env('ACTION_PROJECT_NAME') or project_name(env('GITHUB_REPOSITORY'))
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', project):
        raise ValueError('Invalid project-name')
    operation = env('ACTION_OPERATION') or 'deploy'
    policy = env('ACTION_PULL_POLICY') or 'always'
    if operation not in ('deploy', 'rollback') or policy not in ('always', 'missing', 'never'):
        raise ValueError('Invalid operation or pull-policy')
    mode = env('ACTION_BUILD_MODE') or 'vps'
    if mode not in ('vps', 'runner', 'registry'):
        raise ValueError('build-mode must be vps, runner or registry')
    prefix = env('ACTION_REGISTRY_PREFIX')
    if operation == 'deploy' and mode == 'registry':
        registry_prefix(prefix)
    return dict(project=project, operation=operation, pull_policy=policy,
                build_mode=mode, registry_prefix=prefix, manifest={},
                backup=lines('ACTION_BACKUP'), pre=lines('ACTION_PRE_DEPLOY'), post=lines('ACTION_POST_DEPLOY'),
                root=env('ACTION_DEPLOY_PATH'), files=lines('ACTION_COMPOSE_FILES'),
                shared=lines('ACTION_SHARED_PATHS'), profiles=lines('ACTION_PROFILES'),
                wait=integer('ACTION_WAIT_TIMEOUT', 120, 1, 3600),
                health_url=env('ACTION_HEALTH_URL'), health_wait=integer('ACTION_HEALTH_TIMEOUT', 60, 1, 3600),
                keep=integer('ACTION_KEEP_RELEASES', 3, 2, 100),
                release=uuid.uuid4().hex, commit='')


def ssh_setup(tmp):
    host = env('DEPLOY_HOST', 'DEPLOY_ADDRESS', 'DEPLOY_IP', 'SERVER_HOST', 'SERVER_ADDRESS', 'SERVER_IP')
    user = env('DEPLOY_USER', 'SERVER_USER')
    port = int(env('DEPLOY_PORT', 'SERVER_PORT') or '22')
    key = env('DEPLOY_SSH_KEY', 'SERVER_SSH_KEY')
    password = env('DEPLOY_PASSWORD', 'SERVER_PASSWORD')
    known = env('DEPLOY_KNOWN_HOSTS', 'SERVER_KNOWN_HOSTS')
    if not re.fullmatch(r'[A-Za-z0-9:][A-Za-z0-9.:%_-]*', host) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', user):
        raise ValueError('Valid DEPLOY_HOST and DEPLOY_USER required')
    if not 1 <= port <= 65535 or not known or not (key or password):
        raise ValueError('Valid port, DEPLOY_KNOWN_HOSTS and SSH key or password required')
    known_file = tmp / 'known_hosts'
    known_file.write_text(known.rstrip() + '\n'); known_file.chmod(0o600)
    args = ['ssh', '-F', '/dev/null', '-T', '-p', str(port), '-l', user,
            '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={known_file}',
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=15',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '-o', 'ForwardAgent=no']
    child_env = os.environ.copy()
    for n in list(child_env):
        if n.startswith(('DEPLOY_', 'SERVER_')) or n == 'SSHPASS':
            child_env.pop(n, None)
    if key:
        key_file = tmp / 'id'; key_file.write_text(key.rstrip() + '\n'); key_file.chmod(0o600)
        args += ['-i', str(key_file), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'PreferredAuthentications=publickey']
    else:
        if not shutil.which('sshpass'):
            subprocess.run(['sudo', 'apt-get', 'update', '-qq'], check=True, env=child_env)
            subprocess.run(['sudo', 'apt-get', 'install', '-y', 'sshpass'], check=True, env=child_env)
        child_env['SSHPASS'] = password
        args = ['sshpass', '-e'] + args + ['-o', 'PreferredAuthentications=password,keyboard-interactive', '-o', 'PubkeyAuthentication=no', '-o', 'NumberOfPasswordPrompts=1']
    jump = env('DEPLOY_JUMP_HOST')
    if jump:
        ju = env('DEPLOY_JUMP_USER') or user
        jp = int(env('DEPLOY_JUMP_PORT') or '22')
        jk = env('DEPLOY_JUMP_SSH_KEY') or key
        jknown = env('DEPLOY_JUMP_KNOWN_HOSTS') or known
        if not re.fullmatch(r'[A-Za-z0-9:][A-Za-z0-9.:%_-]*', jump) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', ju) or not 1 <= jp <= 65535 or not jk:
            raise ValueError('Jump host requires a valid address, user, port and SSH key')
        jkey = tmp/'jump_key'; jkey.write_text(jk.rstrip()+'\n'); jkey.chmod(0o600)
        jhosts = tmp/'jump_hosts'; jhosts.write_text(jknown.rstrip()+'\n'); jhosts.chmod(0o600)
        proxy = ['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes',
                 '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
                 '-o', 'GlobalKnownHostsFile=/dev/null', '-o', f'UserKnownHostsFile={jhosts}',
                 '-o', 'ConnectTimeout=15', '-o', 'ForwardAgent=no',
                 '-i', str(jkey), '-p', str(jp), '-l', ju, '-W', '[%h]:%p', jump]
        args += ['-o', 'ProxyCommand='+shlex.join(proxy)]
    return args + [host], child_env



def archived_file(output, source, member, repo, prefix):
    """Hydrate only verified Git LFS objects, never arbitrary checkout content."""
    stream = source.extractfile(member)
    if member.size > 1024:
        output.addfile(member, stream)
        return
    contents = stream.read()
    if not contents.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
        output.addfile(member, io.BytesIO(contents))
        return
    oid = re.search(rb'^oid sha256:([a-f0-9]{64})$', contents, re.MULTILINE)
    size = re.search(rb'^size ([0-9]+)$', contents, re.MULTILINE)
    if not oid or not size:
        raise ValueError(f'Invalid Git LFS pointer: {member.name}')
    file = repo/member.name[len(prefix):]
    if file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(repo):
        raise ValueError(f'Git LFS object is missing: {member.name}; configure checkout lfs: true')
    with file.open('rb') as content:
        if os.fstat(content.fileno()).st_size != int(size[1]) or hashlib.file_digest(content, 'sha256').hexdigest().encode() != oid[1]:
            raise ValueError(f'Git LFS object does not match committed pointer: {member.name}; configure checkout lfs: true')
        content.seek(0)
        member.size = int(size[1])
        output.addfile(member, content)


def archive_source(workspace, commit, destination):
    """Archive committed files and recursively checked-out, pinned Git submodules."""
    workspace = Path(workspace).resolve()
    with tarfile.open(destination, 'w:gz', format=tarfile.PAX_FORMAT) as output:
        def visit(repo, oid, prefix=''):
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args])
            if Path(os.fsdecode(git('rev-parse', '--show-toplevel').rstrip(b'\n'))).resolve() != repo:
                raise ValueError(f'Submodule not initialized: {prefix}')
            if git('rev-parse', 'HEAD').decode().strip() != oid:
                raise ValueError(f'Checkout does not match pinned commit: {prefix or "."}')
            with tempfile.TemporaryFile() as archive:
                subprocess.run(['git', '-C', str(repo), 'archive', '--format=tar',
                                '--prefix='+prefix, oid], stdout=archive, check=True)
                archive.seek(0)
                with tarfile.open(fileobj=archive, mode='r:') as source:
                    included = set()
                    for member in source:
                        included.add(member.name.rstrip('/'))
                        if member.isfile():
                            archived_file(output, source, member, repo, prefix)
                        else:
                            output.addfile(member)
            for entry in git('ls-tree', '-r', '-z', oid).split(b'\0'):
                if not entry.startswith(b'160000 '):
                    continue
                metadata, path = entry.split(b'\t', 1)
                name = os.fsdecode(path)
                full = prefix + name
                if full not in included:
                    continue  # export-ignore
                child = (repo/name).resolve()
                if not child.is_relative_to(repo) or not child.is_dir() or not (child/'.git').exists():
                    raise ValueError(f'Initialize submodule before deploy: {full}')
                visit(child, metadata.split()[2].decode(), full+'/')
        visit(workspace, commit)


def main():
    cfg = configuration()
    with tempfile.TemporaryDirectory(prefix='localzet-deploy-') as d:
        tmp = Path(d)
        if cfg['operation'] == 'deploy':
            workspace = Path(env('GITHUB_WORKSPACE') or os.getcwd()).resolve()
            selected = (workspace/(env('ACTION_SOURCE_PATH') or '.')).resolve()
            if not selected.is_relative_to(workspace) or not selected.is_dir():
                raise ValueError('source-path must be an existing directory inside the workspace')
            workspace = str(selected)
            def git(*args):
                return subprocess.check_output(['git', '-C', workspace, *args])
            cfg['commit'] = git('rev-parse', 'HEAD').decode().strip()
            archive_source(workspace, cfg['commit'], tmp/'source.tar.gz')
        args, child_env = ssh_setup(tmp)
        if cfg['operation'] == 'deploy' and cfg['build_mode'] != 'vps':
            cfg['manifest'] = build_images(cfg, tmp, env('DEPLOY_BUILD_ENV'))
        def ssh(command, **kwargs):
            return subprocess.run(args + [shlex.join(command)], env=child_env, check=True, **kwargs)
        remote = ssh(['mktemp', '-d', '/tmp/localzet-deploy.XXXXXXXXXX'], stdout=subprocess.PIPE, text=True).stdout.strip()
        if not re.fullmatch(r'/tmp/localzet-deploy\.[A-Za-z0-9]+', remote):
            raise ValueError('Unexpected remote temporary directory')
        try:
            def upload(path, name):
                with path.open('rb') as f:
                    ssh(['sh', '-c', 'umask 077; cat > "$1"', 'sh', remote+'/'+name], stdin=f)
            config_file = tmp / 'config.json'; config_file.write_text(json.dumps(cfg))
            upload(config_file, 'config.json')
            upload(Path(__file__).with_name('remote.py'), 'remote.py')
            if cfg['operation'] == 'deploy':
                upload(tmp/'source.tar.gz', 'source.tar.gz')
                if (tmp/'images.tar').is_file():
                    upload(tmp/'images.tar', 'images.tar')
            ssh(['python3', '-u', remote+'/remote.py', remote])
            result = json.loads(ssh(['cat', remote+'/result.json'], stdout=subprocess.PIPE, text=True).stdout)
            if env('GITHUB_OUTPUT'):
                with open(env('GITHUB_OUTPUT'), 'a') as f:
                    for name in ('release', 'project'):
                        f.write(f'{name}={result[name]}\n')
        finally:
            try:
                ssh(['rm', '-rf', '--', remote])
            except subprocess.CalledProcessError:
                print('Warning: remote temporary directory cleanup failed')


if __name__ == '__main__':
    main()
