import os
from pathlib import Path
import pickle
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import app
from app_processing.file_loader import iter_repo_files
from utils.project_fingerprint import compute_project_fingerprint
from vector_store.store import VectorStore
from utils.errors import InferenceError


class IndexRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='context_recovery_')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.repo = self.base/'workspace'/'demo'
        self.repo.mkdir(parents=True)
        self.source = self.repo/'source.py'
        self.source.write_text('def add(a,b): return a+b\n', encoding='utf-8')
        (self.repo/'README.md').write_bytes(b'# Preserve\r\n')
        self.store_root = self.base/'vectors'
        settings = {'BASE_DIR':self.base/'tool', 'WORKSPACE_ROOT':self.base/'workspace',
                    'PROFILE_DIR':self.base/'profiles', 'CHUNK_STORE_DIR':self.base/'chunks',
                    'INDICES_STORE_DIR':self.base/'indices'}
        for name, value in settings.items():
            p=patch.object(app, name, str(value));p.start();self.addCleanup(p.stop)
        p=patch('vector_store.store.BASE_VECTOR_DIR', str(self.store_root));p.start();self.addCleanup(p.stop)
        p=patch.object(app, 'embed_texts', side_effect=lambda texts:[[1.,0.] for _ in texts])
        self.embed=p.start();self.addCleanup(p.stop)
        self.client=TestClient(app.app, raise_server_exceptions=False)
        self.addCleanup(app._VECTOR_STORES.clear)

    def index(self):
        result=self.client.post('/index', json={'repo_id':'demo'})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_unchanged_skips_changed_content_rebuilds(self):
        self.assertEqual(self.index()['status'], 'indexed')
        self.embed.reset_mock()
        self.assertEqual(self.index()['action'], 'skipped')
        self.embed.assert_not_called()
        old=self.source.stat()
        self.source.write_text('def add(a,b): return a-b\n', encoding='utf-8')
        os.utime(self.source, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.assertEqual(self.index()['status'], 'indexed')
        self.embed.assert_called_once()

    def test_missing_and_corrupt_artifacts_rebuild_and_are_queryable(self):
        self.index()
        for artifact, mutation in [('index.faiss','missing'), ('metadata.pkl','missing'),
                                   ('index.faiss','corrupt'), ('metadata.pkl','corrupt'),
                                   ('metadata.pkl','inconsistent')]:
            with self.subTest(artifact=artifact, mutation=mutation):
                path=self.store_root/'demo'/artifact
                if mutation == 'missing': path.unlink()
                elif mutation == 'corrupt': path.write_bytes(b'not a valid artifact')
                else: path.write_bytes(pickle.dumps({'embeddings':[[1,0]], 'documents':[], 'metadatas':[]}))
                self.assertIsNone(VectorStore.load('demo'))
                self.assertEqual(self.index()['status'], 'indexed')
                loaded=VectorStore.load('demo')
                hits=loaded.search([1.,0.], 'add', threshold=0)
                self.assertIn('return a+b', hits[0]['text'])
                self.assertEqual((self.repo/'README.md').read_bytes(), b'# Preserve\r\n')

    def test_failed_rebuild_reports_failure_and_can_retry(self):
        self.index()
        (self.store_root/'demo'/'index.faiss').unlink()
        with patch.object(app, 'embed_texts', side_effect=InferenceError('unavailable','offline',503)):
            response=self.client.post('/index',json={'repo_id':'demo'})
        self.assertEqual(response.status_code,503)
        self.assertNotIn('demo',app._VECTOR_STORES)
        self.assertEqual(self.index()['status'],'indexed')

    def test_unreadable_metadata_rebuilds_without_false_skip(self):
        self.index()
        with patch('vector_store.store.pickle.load',side_effect=PermissionError('unreadable')):
            self.assertEqual(self.index()['status'],'indexed')
        self.assertIsNotNone(VectorStore.load('demo'))

    def test_same_content_different_time_and_location_same_fingerprint(self):
        before=compute_project_fingerprint(self.repo)
        os.utime(self.source, (1,1))
        self.assertEqual(compute_project_fingerprint(self.repo),before)
        second=self.base/'copy';second.mkdir()
        (second/'source.py').write_bytes(self.source.read_bytes())
        self.assertEqual(compute_project_fingerprint(second),before)

    def test_add_delete_and_rename_change_fingerprint(self):
        original=compute_project_fingerprint(self.repo)
        extra=self.repo/'extra.py';extra.write_text('value = 2')
        added=compute_project_fingerprint(self.repo)
        self.assertNotEqual(added,original)
        extra.rename(self.repo/'renamed.py')
        self.assertNotEqual(compute_project_fingerprint(self.repo),added)
        (self.repo/'renamed.py').unlink()
        self.assertEqual(compute_project_fingerprint(self.repo),original)

    def test_generated_and_gitignored_content_does_not_affect_fingerprint(self):
        subprocess.run(['git','init',str(self.repo)],check=True,capture_output=True)
        (self.repo/'.gitignore').write_text('ignored.py\nignored-folder/\n')
        before=compute_project_fingerprint(self.repo)
        for name in ['ignored.py','ignored-folder/cache.py','venv/cache.py','.git/cache.py',
                     'chunk_store/chunks.json','indices_store/index.json','vector_store/repos/demo/cache.json',
                     '.pytest_cache/data.py','repo_profiles/generated.json','node_modules/dependency.js']:
            path=self.repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('value = 9')
        self.assertEqual(compute_project_fingerprint(self.repo),before)
        self.assertEqual([p.name for p in iter_repo_files(self.repo)],['source.py'])

    def test_index_uses_percent_word_parser(self):
        self.source.write_text('# Accuracy: 94.8 percent\ndef add(a,b): return a+b\n')
        self.assertEqual(self.index()['accuracy'],'94.8%')

    def test_archive_ignore_rules_without_initializing_source(self):
        (self.repo/'.gitignore').write_text('*.generated.py\n!keep.generated.py\n')
        before=compute_project_fingerprint(self.repo)
        (self.repo/'skip.generated.py').write_text('ignored = True')
        self.assertEqual(compute_project_fingerprint(self.repo), before)
        (self.repo/'keep.generated.py').write_text('included = True')
        self.assertNotEqual(compute_project_fingerprint(self.repo), before)
        self.assertFalse((self.repo/'.git').exists())
