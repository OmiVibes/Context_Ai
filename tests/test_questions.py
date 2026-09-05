import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
import app
from utils.session_store import SessionStore
import rag.core as core
import rag.repo_detector as detector
from rag.router import RouterAgent
from rag.repo_structure import infer_architecture


class QuestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='context_questions_')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for repo in ['shop-frontend','shop-backend','calculator']:
            path=self.root/'indices_store'/repo;path.mkdir(parents=True)
            (path/'indices.json').write_text(json.dumps({'indexed_files':['main.py']}))
        p=patch.object(app,'BASE_DIR',str(self.root));p.start();self.addCleanup(p.stop)
        p=patch.object(app,'SESSION_STORE',SessionStore(self.root/'sessions.sqlite3'));p.start();self.addCleanup(p.stop)
        app._SESSIONS.clear();self.addCleanup(app._SESSIONS.clear)
        self.c=TestClient(app.app)
        self.answer=patch.object(app,'rag_answer',side_effect=lambda **kw:{'answer':kw['question'],'repo':kw['repo_id']})
        self.answer.start();self.addCleanup(self.answer.stop)

    def ask(self,q,session='one'):
        return self.c.post('/ask',json={'session_id':session,'user':q}).json()

    def test_greetings_without_repositories(self):
        with patch('rag.repo_detector.get_all_available_repos',return_value=[]):
            for q in ['hi','hello!','who are you?']:
                self.assertIn('answer',self.ask(q))
                self.assertEqual(RouterAgent().route(question=q,repo_id=None,params={})['agent'],'IdentityAgent')
        self.assertFalse(app.is_generic_question('history of authentication'))
        self.assertFalse(app.is_generic_question('hello, explain calculator'))

    def test_pending_selection_cleared_and_active_repo_switchable(self):
        self.assertIn('available_repos',self.ask('What does it do?'))
        result=self.ask('calculator')
        self.assertEqual(result,{'answer':'What does it do?','repo':'calculator'})
        self.assertNotIn('question',app._SESSIONS['one'])
        self.assertEqual(self.ask('How does it work?')['repo'],'calculator')
        self.assertEqual(self.ask('Explain shop-frontend')['repo'],'shop-frontend')
        self.assertIn('available_repos',self.ask('What does it do?',session='two'))

    def test_explicit_selection_phrase_preserves_pending_question(self):
        self.ask('What does it do?')
        self.assertEqual(self.ask('I want calculator')['answer'],'What does it do?')

    def test_new_query_replaces_pending_without_using_stale_question(self):
        self.ask('Old question')
        result=self.ask('Explain calculator')
        self.assertEqual(result['answer'],'Explain calculator')
        self.ask('Explain unknown.py')
        self.ask('New unrelated question')
        self.assertEqual(self.ask('calculator')['answer'],'New unrelated question')

    def test_greeting_clears_pending(self):
        self.ask('Old question');self.ask('hello')
        self.assertNotIn('question',app._SESSIONS['one'])
        self.assertNotEqual(self.ask('calculator')['answer'],'Old question')

    def test_exact_repo_precedes_group(self):
        self.assertEqual(detector.detect_repo_from_question('Explain shop-frontend',str(self.root))['repo_id'],'shop-frontend')
        self.assertEqual(detector.detect_repo_from_question('Explain shop',str(self.root))['status'],'project_group')

    def test_architecture_isolation_and_no_model_invention(self):
        a=self.root/'calc';a.mkdir();(a/'calculator.py').write_text('def add(a,b): return a+b')
        b=self.root/'web';b.mkdir();(b/'routes.js').write_text('export const route = "/";')
        empty=self.root/'empty';empty.mkdir()
        for repo,path,own,other in [('a',a,'calculator.py','routes.js'),('b',b,'routes.js','calculator.py')]:
            with patch.dict(core._VECTOR_STORES,{repo:Mock()}),patch.dict(core._REPO_PATHS,{repo:str(path)}),patch.object(core,'load_repo_profile',return_value=None),patch.object(core,'generate_answer') as generate:
                result=core.rag_answer(question='Explain architecture',repo_id=repo)['answer']
                self.assertIn(own,result);self.assertNotIn(other,result)
                self.assertNotIn('tumor',result.lower());self.assertNotIn('cnn',result.lower())
                generate.assert_not_called()
        self.assertIn('sufficient',infer_architecture(empty))
