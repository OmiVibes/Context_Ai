import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import app
import github.repo_sync as sync
from utils.repo_paths import InvalidRepoId, repo_path
from vector_store.store import VectorStore
from app_processing.file_loader import load_repo_files


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="context_safety_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.repo = self.workspace / 'demo'
        self.repo.mkdir()
        for key, value in {'BASE_DIR':self.root/'tool', 'WORKSPACE_ROOT':self.workspace,
                           'PROFILE_DIR':self.root/'profiles','CHUNK_STORE_DIR':self.root/'chunks',
                           'INDICES_STORE_DIR':self.root/'indices'}.items():
            p=patch.object(app,key,str(value));p.start();self.addCleanup(p.stop)
        p=patch.object(app,'embed_texts',side_effect=lambda xs:[[1.,0.] for _ in xs])
        p.start();self.addCleanup(p.stop)
        self.client=TestClient(app.app,raise_server_exceptions=False)

    def test_index_preserves_document_bytes(self):
        (self.repo/'main.py').write_text('def add(a,b): return a+b')
        docs={'README.md':b'# Example\r\n', 'README.txt':b'Docs', 'LICENSE':b'License', 'docs/guide.md':b'Guide'}
        for name,data in docs.items():
            p=self.repo/name;p.parent.mkdir(exist_ok=True);p.write_bytes(data)
        self.assertEqual(self.client.post('/index',json={'repo_id':'demo'}).status_code,200)
        for name,data in docs.items(): self.assertEqual((self.repo/name).read_bytes(),data)

    def test_invalid_ids_rejected_before_index_or_storage(self):
        for name in ['../other','..\\other','/tmp/repo','C:\\repo','C:repo','%2e%2e%2fother','%252e%252e%255cother','repo/file','repo:stream','..','CON','foo.']:
            with self.subTest(name=name):
                self.assertEqual(self.client.post('/index',json={'repo_id':name}).status_code,400)
                with self.assertRaises(InvalidRepoId): VectorStore.load(name)
                with self.assertRaises(InvalidRepoId): sync.sync_repo('unused',name)

    def test_canonical_escape(self):
        outside=self.root/'outside';outside.mkdir()
        link=self.workspace/'escape'
        try: link.symlink_to(outside,target_is_directory=True)
        except OSError:
            if os.name != 'nt': raise
            # Directory junction creation does not need Windows symlink privileges.
            subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],check=True,capture_output=True)
        self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
        with self.assertRaises(InvalidRepoId): repo_path(self.workspace,'escape')
        self.assertEqual(self.client.post('/index',json={'repo_id':'escape'}).status_code,400)

    def test_loader_skips_external_directory_junction(self):
        outside=self.root/'private';outside.mkdir()
        (outside/'secret.py').write_text('private = True')
        link=self.repo/'external'
        try: link.symlink_to(outside,target_is_directory=True)
        except OSError:
            if os.name != 'nt': raise
            subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],check=True,capture_output=True)
        self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
        self.assertEqual(load_repo_files(str(self.repo)),[])

    def test_storage_rejects_external_directory_junction(self):
        outside=self.root/'private';outside.mkdir()
        root=self.root/'vector_store'/'repos';root.mkdir(parents=True)
        link=root/'escape'
        try: link.symlink_to(outside,target_is_directory=True)
        except OSError:
            if os.name != 'nt': raise
            subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],check=True,capture_output=True)
        self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
        with self.assertRaises(InvalidRepoId): VectorStore.load('escape')

    def test_legacy_sync_keeps_existing_non_git_directory(self):
        import backup
        (self.repo/'README.md').write_text('keep this user content')
        with patch.object(backup,'REPO_PATH',str(self.repo)),patch.object(backup,'GITHUB_REPO_URL','https://example.invalid/repo'):
            with self.assertRaises(sync.RepositorySyncError): backup.sync_repo()
        self.assertEqual((self.repo/'README.md').read_text(),'keep this user content')

    def test_git_preserves_docs_and_dirty_work(self):
        def git(*args):
            return subprocess.run(['git',*map(str,args)],check=True,capture_output=True,text=True)
        git('init',self.repo)
        (self.repo/'README.md').write_text('# Original')
        (self.repo/'LICENSE').write_text('Original license')
        (self.repo/'main.py').write_text('x=1')
        git('-C',self.repo,'add','.')
        git('-C',self.repo,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','fixture')
        with patch.object(sync,'BASE_REPO_DIR',str(self.root/'clones')),patch.object(sync,'GITHUB_TOKEN',None):
            clone=Path(sync.sync_repo(str(self.repo),'demo'))
            self.assertEqual((clone/'README.md').read_text(),'# Original')
            self.assertEqual((clone/'LICENSE').read_text(),'Original license')
            sync.sync_repo(str(self.repo),'demo')
            for staged in [False,True]:
                (clone/'main.py').write_text('x=2')
                (clone/'untracked.txt').write_text('keep')
                if staged: git('-C',clone,'add','main.py')
                with self.assertRaisesRegex(sync.RepositorySyncError,'local changes'):
                    sync.sync_repo(str(self.repo),'demo')
                self.assertEqual((clone/'main.py').read_text(),'x=2')
                self.assertEqual((clone/'untracked.txt').read_text(),'keep')

    def test_git_fast_forward_then_refuse_upstream_deletion(self):
        def git(*args):
            return subprocess.run(['git',*map(str,args)],check=True,capture_output=True,text=True).stdout
        def commit(path):
            git('-C',path,'add','-A')
            git('-C',path,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','fixture')
        git('init',self.repo)
        (self.repo/'main.py').write_text('x=1');(self.repo/'README.md').write_text('keep');commit(self.repo)
        with patch.object(sync,'BASE_REPO_DIR',str(self.root/'clones')),patch.object(sync,'GITHUB_TOKEN',None):
            clone=Path(sync.sync_repo(str(self.repo),'demo'))
            (self.repo/'main.py').write_text('x=2');commit(self.repo)
            sync.sync_repo(str(self.repo),'demo')
            self.assertEqual((clone/'main.py').read_text(),'x=2')
            (self.repo/'README.md').unlink();commit(self.repo)
            with self.assertRaisesRegex(sync.RepositorySyncError,'removes repository files'):
                sync.sync_repo(str(self.repo),'demo')
            self.assertEqual((clone/'README.md').read_text(),'keep')


if __name__ == '__main__': unittest.main()
