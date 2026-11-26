import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import remote
import client
import runner


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.incoming = self.base/'incoming'; self.incoming.mkdir()
        self.root = self.base/'deployment'
        self.calls = []
        self.images = {'postgres:16'}
        self.fail = None
        self.model = {'services': {'app': {'build': {'context': '.'}}, 'db': {'image': 'postgres:16'}, 'debug': {'image':'debug:latest','profiles':['debug']}}}
        self.patch = patch.object(remote, 'run', side_effect=self.docker)
        self.patch.start(); self.addCleanup(self.patch.stop)

    def docker(self, args, **kwargs):
        args = list(map(str, args)); self.calls.append(args)
        if args[:3] == ['docker','ps','-aq']: return ''
        if args[:3] == ['docker','image','tag']:
            self.images.add(args[-1]); return
        if args[:3] == ['docker','image','inspect']:
            if args[-1] not in self.images: raise subprocess.CalledProcessError(1,args)
            return
        if args[:3] == ['docker','image','rm']:
            self.images.discard(args[-1]); return
        if 'config' in args: return json.dumps(self.model)
        if 'build' in args:
            files = [args[i+1] for i,x in enumerate(args[:-1]) if x=='-f']
            self.images.update(s['image'] for s in json.loads(Path(files[-1]).read_text())['services'].values())
        if 'up' in args and self.fail and any(self.fail in arg for arg in args):
            raise subprocess.CalledProcessError(1,args)

    def cfg(self, n, operation='deploy', keep=3):
        return dict(project='demo', root=str(self.root), release=f'{n:032x}', operation=operation,
                    files=[], shared=[], profiles=[], wait=5, health_url='', health_wait=5,
                    keep=keep, pull_policy='always', commit='abcdef')

    def bundle(self):
        with tarfile.open(self.incoming/'source.tar.gz','w:gz') as f:
            data=b'services: {}\n'; info=tarfile.TarInfo('compose.prod.yml'); info.size=len(data)
            f.addfile(info,io.BytesIO(data))

    def deploy(self,n,**kw):
        self.bundle(); remote.Manager(self.cfg(n,**kw),self.incoming).execute()
        return json.loads((self.root/'state.json').read_text())

    def test_release_and_manual_rollback(self):
        first=self.deploy(1)['current']; second=self.deploy(2)['current']
        self.assertNotEqual(first,second)
        self.assertEqual(self.deploy(3,operation='rollback')['current'],first)
        self.assertEqual(self.deploy(4,operation='rollback')['current'],second)
        self.assertFalse(any('debug' in ' '.join(c) for c in self.calls))
        self.assertTrue(any('--no-build' in c and '--wait' in c for c in self.calls))

    def test_failed_release_restores_previous(self):
        first=self.deploy(1)['current']; self.fail=f'{2:032x}'
        with self.assertRaises(subprocess.CalledProcessError): self.deploy(2)
        self.assertEqual(json.loads((self.root/'state.json').read_text())['current'],first)
        self.assertFalse((self.root/'pending.json').exists())
        self.assertFalse((self.root/'releases'/self.fail).exists())
        self.assertIn(first, ' '.join(self.calls[-3] + self.calls[-4] + self.calls[-5]))

    def test_failed_first_release_never_deletes_volumes(self):
        self.fail=f'{1:032x}'
        with self.assertRaises(subprocess.CalledProcessError): self.deploy(1)
        down=next(c for c in self.calls if 'down' in c)
        self.assertNotIn('-v',down); self.assertNotIn('--volumes',down)

    def test_retention(self):
        for n in range(1,4): self.deploy(n,keep=2)
        self.assertFalse((self.root/'releases'/f'{1:032x}').exists())
        self.assertFalse(any(f'{1:032x}' in tag for tag in self.images))
        self.assertEqual(len(json.loads((self.root/'state.json').read_text())['history']),2)

    def test_recover_pending(self):
        first=self.deploy(1)['current']; second=self.deploy(2)['current']
        state={'project':'demo','current':first,'history':[first]}
        remote.atomic_json(self.root/'state.json',state)
        remote.atomic_json(self.root/'pending.json',{'candidate':second,'previous':first})
        self.deploy(3)
        self.assertFalse((self.root/'pending.json').exists())

    def test_shared_and_ambiguous_discovery(self):
        src=self.base/'src'; src.mkdir(); shared=self.base/'shared'; shared.mkdir()
        (src/'compose.yml').touch(); (src/'docker-compose.yml').touch()
        with self.assertRaises(ValueError): remote.discover(src,[])
        self.assertEqual(remote.discover(src,['compose.yml']),['compose.yml'])
        (shared/'.env').write_text('SECRET=1'); (shared/'uploads').mkdir()
        remote.link_shared(src,shared,['uploads'],['compose.yml'])
        self.assertEqual((src/'.env').read_text(),'SECRET=1')
        self.assertTrue((src/'uploads').is_symlink())
        with self.assertRaises(ValueError): remote.relative('../other')

    def test_project_names_and_tags(self):
        self.assertNotEqual(client.project_name('a/b-c'),client.project_name('a-b/c'))
        self.assertNotEqual(remote.image_tag('demo','a','A'),remote.image_tag('demo','a','a'))

    def test_archive_escape_rejected(self):
        with tarfile.open(self.incoming/'source.tar.gz','w:gz') as f:
            i=tarfile.TarInfo('../../evil'); i.size=1; f.addfile(i,io.BytesIO(b'x'))
        with self.assertRaises(tarfile.FilterError): remote.Manager(self.cfg(1),self.incoming).execute()


    def test_nested_submodules_are_pinned(self):
        root = self.base/'git'
        root.mkdir()
        child = root/'module'; child.mkdir()
        nested = child/'nested'; nested.mkdir()
        def git(p, *args):
            return subprocess.check_output(['git', '-C', str(p), *args],
                                           stderr=subprocess.DEVNULL).decode().strip()
        for p in (nested, child, root):
            git(p, 'init'); git(p, 'config', 'user.name', 'Test')
            git(p, 'config', 'user.email', 'test@example.com')
            (p/'test.txt').write_text(p.name)
            git(p, 'add', 'test.txt'); git(p, 'commit', '-m', 'fixture')
        for p, name in ((child, 'nested'), (root, 'module')):
            git(p, 'add', name); git(p, 'commit', '-m', 'submodule')
        target = self.base/'out.tar.gz'
        commit = git(root, 'rev-parse', 'HEAD')
        (root/'secret-untracked').write_text('not in archive')
        client.archive_source(root, commit, target)
        with tarfile.open(target) as archive:
            self.assertEqual(archive.extractfile('module/nested/test.txt').read(), b'nested')
            self.assertNotIn('secret-untracked', archive.getnames())
            self.assertFalse(any('.git/' in n for n in archive.getnames()))
        (nested/'test.txt').write_text('changed')
        git(nested, 'add', 'test.txt'); git(nested, 'commit', '-m', 'changed')
        with self.assertRaisesRegex(ValueError, 'pinned commit'):
            client.archive_source(root, commit, target)


    def test_build_model_omits_runtime_secrets_and_escapes_dollars(self):
        model = {'services': {'app': {'build': {'context': '/tmp/src', 'args': {'VALUE': '$literal'}},
                                     'env_file': ['.env'], 'environment': {'PASSWORD': 'secret'},
                                     'volumes': ['data:/data'], 'platform': 'linux/amd64'}},
                 'secrets': {'runtime': {'file': '/tmp/runtime-secret'}}}
        result = runner.build_model(model, model['services'], {'app': 'localzet-deploy/demo:one'})
        self.assertEqual(result, {'services': {'app': {'build': {'context': '/tmp/src', 'args': {'VALUE': '$$literal'}},
                                                      'platform': 'linux/amd64', 'image': 'localzet-deploy/demo:one'}}})

    def test_registry_validation(self):
        for value in ('ghcr.io/owner/repo', '127.0.0.1:25000/owner/repo'):
            self.assertEqual(runner.registry_prefix(value), value)
        for value in ('owner/repo', 'ghcr.io/OWNER/repo', 'ghcr.io/owner/repo:latest', 'ghcr.io/owner/repo;echo'):
            with self.assertRaises(ValueError):
                runner.registry_prefix(value)

    def test_hooks_run_in_order_and_rollback_on_post_failure(self):
        first = self.deploy(1)['current']
        self.bundle()
        cfg = self.cfg(2)
        cfg.update(backup=['app backup'], pre=['app migrate --force'], post=['app bad-hook'])
        original = self.docker
        def fake(args, **kw):
            result = original(args, **kw)
            if 'bad-hook' in args:
                raise subprocess.CalledProcessError(1, args)
            return result
        with patch.object(remote, 'run', side_effect=fake):
            with self.assertRaises(subprocess.CalledProcessError):
                remote.Manager(cfg,self.incoming).execute()
        self.assertEqual(json.loads((self.root/'state.json').read_text())['current'], first)
        backup = next(i for i,c in enumerate(self.calls) if 'backup' in c)
        migrate = next(i for i,c in enumerate(self.calls) if 'migrate' in c)
        failure = next(i for i,c in enumerate(self.calls) if 'bad-hook' in c)
        self.assertLess(backup,migrate); self.assertLess(migrate,failure)
        self.assertTrue(any('up' in c and any(first in x for x in c) for c in self.calls[failure+1:]))

if __name__ == '__main__': unittest.main()
