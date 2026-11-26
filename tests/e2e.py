"""Real Docker Compose smoke test on disposable runner."""
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from remote import Manager

project = 'deploy-test-'+uuid.uuid4().hex[:12]
with tempfile.TemporaryDirectory() as temp:
    base=Path(temp); incoming=base/'incoming'; incoming.mkdir(); root=base/'deployment'
    def deploy(version, bad=False, operation='deploy'):
        source = {
            'compose.prod.yml': '''services:
  app:
    build: .
    healthcheck:
      test: [CMD-SHELL, 'test -f /healthy']
      interval: 1s
      timeout: 1s
      retries: 3
    volumes:
      - data:/data
volumes:
  data:
''',
            'Dockerfile': 'FROM alpine:3.22\nRUN echo '+str(version)+' >/version\n'+('' if bad else 'RUN touch /healthy\n')+'CMD ["tail", "-f", "/dev/null"]\n',
        }
        with tarfile.open(incoming/'source.tar.gz','w:gz') as tar:
            for name, value in source.items():
                raw=value.encode(); info=tarfile.TarInfo(name); info.size=len(raw); tar.addfile(info,io.BytesIO(raw))
        cfg=dict(project=project,root=str(root),release=uuid.uuid4().hex,operation=operation,files=[],shared=[],profiles=[],wait=20,health_url='',health_wait=5,keep=3,pull_policy='always',commit=str(version))
        Manager(cfg,incoming).execute()
    def app(*cmd):
        cid=subprocess.check_output(['docker','ps','-q','--filter',f'label=com.docker.compose.project={project}','--filter','label=com.docker.compose.service=app'],text=True).strip()
        return subprocess.check_output(['docker','exec',cid,*cmd],text=True).strip()
    try:
        deploy(1); assert app('cat','/version')=='1'
        app('sh','-c','echo persistent >/data/probe')
        deploy(2); assert app('cat','/version')=='2'
        try:
            deploy(3,bad=True)
        except subprocess.CalledProcessError:
            pass
        else:
            raise AssertionError('Unhealthy deployment unexpectedly succeeded')
        assert app('cat','/version')=='2'
        assert app('cat','/data/probe')=='persistent'
        deploy(0,operation='rollback'); assert app('cat','/version')=='1'
        assert app('cat','/data/probe')=='persistent'
        print('Docker Compose end-to-end smoke test passed')
    finally:
        for kind, list_args, remove_args in (
            ('containers',['ps','-aq'],['rm','-f']),
            ('volumes',['volume','ls','-q'],['volume','rm']),
            ('networks',['network','ls','-q'],['network','rm']),
        ):
            ids=subprocess.check_output(['docker',*list_args,'--filter',f'label=com.docker.compose.project={project}'],text=True).split()
            if ids: subprocess.run(['docker',*remove_args,*ids],check=False)
        tags=subprocess.check_output(['docker','image','ls','--format','{{.Repository}}:{{.Tag}}',f'localzet-deploy/{project}'],text=True).split()
        if tags: subprocess.run(['docker','image','rm',*tags],check=False)
