import os
import unittest
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient
import app
import rag.core as rag
from rag.grounded import prepare_context, INSUFFICIENT
from vector_store.store import VectorStore


def hit(text='def add(a, b): return a + b', score=.8, file='calculator.py', **metadata):
    return {'text': text, 'score': score, 'metadata': {'file_path': file, **metadata}}


class GroundedTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.search.return_value = [hit()]
        self.http = Mock(status_code=200)
        self.http.json.return_value = {'answer': 'The add function returns a + b.'}
        patches = [patch.dict(rag._VECTOR_STORES, {'demo': self.store}, clear=True),
                   patch.dict(rag._REPO_PROFILES, {}, clear=True),
                   patch.dict(app._SESSIONS, {}, clear=True),
                   patch.object(rag, 'load_repo_profile', return_value=None),
                   patch.object(rag, 'embed_query', return_value=[1., 0.]),
                   patch('rag.local_llm.requests.post', return_value=self.http),
                   patch('rag.repo_detector.get_all_available_repos', return_value=['demo']),
                   patch('rag.repo_detector.detect_repo_from_question', return_value={'status': 'unique_match', 'repo_id': 'demo'}),
                   patch.dict(os.environ, {'RAG_TOP_K': '5', 'RAG_MAX_CONTEXT_CHARS': '3000'})]
        self.mocks = [p.start() for p in patches]
        for p in patches: self.addCleanup(p.stop)
        self.post = self.mocks[5]
        self.client = TestClient(app.app, raise_server_exceptions=False)

    def ask(self, question='How does add work?'):
        return self.client.post('/ask', json={'session_id': 'grounded', 'user': question})

    def test_full_api_http_prompt_and_sources(self):
        response = self.ask()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['repository'], 'demo')
        self.assertEqual(data['answer'], self.http.json()['answer'])
        self.assertEqual(len(data['sources']), 1)
        source = data['sources'][0]
        self.assertEqual(source['file'], 'calculator.py')
        self.assertTrue(source['chunk_id'])
        self.assertNotIn('start_line', source)
        prompt = self.post.call_args.kwargs['json']['prompt']
        self.assertIn('return a + b', prompt)
        self.assertIn(source['chunk_id'], prompt)
        self.assertIn('Use only', prompt)
        self.assertLess(prompt.index('return a + b'), prompt.index('How does add work?'))
        self.assertNotIn('CNN', prompt)
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(self.store.search.call_count, 4)

    def test_real_vector_retrieval_excludes_unrelated_content(self):
        rag._VECTOR_STORES['demo'] = VectorStore([[1., 0.], [-1., 0.]],
            ['calculator add returns a sum', 'UNRELATED_REPOSITORY_SECRET_TEXT'],
            [{'file_path': 'add.py'}, {'file_path': 'other.py'}])
        result = self.ask().json()
        self.assertEqual([s['file'] for s in result['sources']], ['add.py'])
        self.assertNotIn('UNRELATED_REPOSITORY_SECRET_TEXT', self.post.call_args.kwargs['json']['prompt'])

    def test_no_weak_or_unusable_evidence_bypasses_inference(self):
        for results in [[], [hit(score=.01)], [hit(text='   ')], [hit(score=float('nan'))]]:
            with self.subTest(results=results):
                self.store.search.return_value = results
                data = self.ask().json()
                self.assertEqual(data['answer'], INSUFFICIENT)
                self.assertEqual(data['sources'], [])
        self.post.assert_not_called()

    def test_transport_failures_reach_api(self):
        for error, status in [(requests.Timeout(), 504), (requests.ConnectionError(), 503)]:
            self.post.side_effect = error
            response = self.ask()
            self.assertEqual(response.status_code, status)
            self.assertNotIn('answer', response.json())

    def test_model_and_service_failures_reach_api(self):
        for status in [500, 503, 504]:
            self.http.status_code = status
            self.http.json.return_value = {'detail': {'code': 'model_unavailable'}}
            self.assertEqual(self.ask().status_code, status)

    def test_chunk_boundary_limit_and_ranked_citations(self):
        results = [hit('LOWER' * 80, .5, 'lower.py'), hit('HIGHER' * 70, .9, 'higher.py')]
        with patch.dict(os.environ, {'RAG_MAX_CONTEXT_CHARS': '700'}):
            context, sources, _ = prepare_context(results, ['demo'])
        self.assertLessEqual(len(context), 700)
        self.assertEqual([s['file'] for s in sources], ['higher.py'])
        self.assertNotIn('LOWER', context)

    def test_large_first_chunk_keeps_intact_metadata(self):
        with patch.dict(os.environ, {'RAG_MAX_CONTEXT_CHARS': '300'}):
            context, sources, _ = prepare_context([hit('x' * 4000)], ['demo'])
        self.assertLessEqual(len(context), 300)
        self.assertIn(sources[0]['chunk_id'], context)
        self.assertTrue(context.endswith('[excerpt truncated]'))

    def test_top_k_and_dedup_preserve_distinct_sources(self):
        rows = [hit(score=.5), hit(score=.9), hit(file='second.py', score=.7), hit(file='third.py', score=.6)]
        with patch.dict(os.environ, {'RAG_TOP_K': '2'}):
            _, sources, _ = prepare_context(rows, ['demo'])
        self.assertEqual([s['file'] for s in sources], ['calculator.py', 'second.py'])
        self.assertEqual(sources[0]['score'], .9)

    def test_existing_line_metadata_is_carried_without_invention(self):
        _, sources, _ = prepare_context([hit(start_line=10, end_line=20), hit(file='other.py')], ['demo'])
        self.assertEqual(sources[0]['start_line'], 10)
        self.assertEqual(sources[0]['end_line'], 20)
        self.assertNotIn('start_line', sources[1])

    def test_distinct_line_ranges_remain_distinct_citations(self):
        _, sources, _ = prepare_context([hit(start_line=1, end_line=3), hit(start_line=20, end_line=22)], ['demo'])
        self.assertEqual(len(sources), 2)
        self.assertNotEqual(sources[0]['chunk_id'], sources[1]['chunk_id'])

    def test_api_never_cites_context_excluded_by_budget(self):
        self.store.search.return_value = [hit('FIRST' * 70, .9), hit('OMITTED' * 70, .7, 'omitted.py')]
        with patch.dict(os.environ, {'RAG_MAX_CONTEXT_CHARS': '650'}):
            data = self.ask().json()
        self.assertEqual(len(data['sources']), 1)
        self.assertNotIn('OMITTED', self.post.call_args.kwargs['json']['prompt'])

    def test_multi_repository_uses_same_bound_and_keeps_provenance(self):
        rag._VECTOR_STORES['second'] = self.store
        with patch.dict(os.environ, {'RAG_TOP_K': '2'}):
            data = rag.rag_answer_multi_repo(question='Explain add', repo_ids=['demo', 'second'])
        self.assertEqual({s['repository'] for s in data['sources']}, {'demo', 'second'})
        self.assertIn('Use only', self.post.call_args.kwargs['json']['prompt'])

    def test_generic_questions_still_bypass_retrieval(self):
        for query in ['hi', 'hello', 'who are you']:
            self.assertIn('answer', self.ask(query).json())
        self.post.assert_not_called()
        self.store.search.assert_not_called()

    def test_configured_inference_url_preserves_service_model_selection(self):
        with patch.dict(os.environ, {'LLM_API_URL': 'http://inference.test/generate'}):
            self.ask()
        self.assertEqual(self.post.call_args.args[0], 'http://inference.test/generate')
        self.assertNotIn('model', self.post.call_args.kwargs['json'])

    def test_logs_exclude_prompt_and_content(self):
        self.store.search.return_value = [hit('SENSITIVE_FIXTURE_CONTENT')]
        with self.assertLogs('rag.grounded', level='INFO') as logs:
            self.ask('PRIVATE_QUESTION')
        text = '\n'.join(logs.output)
        self.assertIn('context_chars=', text)
        self.assertNotIn('SENSITIVE_FIXTURE_CONTENT', text)
        self.assertNotIn('PRIVATE_QUESTION', text)
