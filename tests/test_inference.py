import os
import unittest
from unittest.mock import Mock, patch
import requests
from fastapi.testclient import TestClient
import app
import rag.core as rag
import rag.local_llm as client
import llm_service.core as core
import llm_service.server as server
from llm_service.engines.ollama import OllamaEngine, installed_local_models
from utils.errors import InferenceError


class InferenceTests(unittest.TestCase):
    def test_engine_http_errors(self):
        for status, body, expected in [(404,'not found',503),(500,'requires more system memory',503),
                                        (503,'busy',503),(504,'timeout',504),(500,'internal secret trace',500)]:
            with self.subTest(status=status,body=body), patch('llm_service.engines.ollama.requests.post',return_value=Mock(status_code=status,json=lambda:{'error':body})):
                with self.assertRaises(InferenceError) as result: OllamaEngine().generate('test')
                self.assertEqual(result.exception.status_code,expected)
                self.assertNotIn('secret trace',str(result.exception))

    def test_transport_errors(self):
        for error,status in [(requests.Timeout(),504),(requests.ConnectionError(),503)]:
            for module,fn in [('llm_service.engines.ollama',OllamaEngine().generate),('rag.local_llm',client.generate_answer)]:
                with self.subTest(module=module,status=status),patch(module+'.requests.post',side_effect=error):
                    with self.assertRaises(InferenceError) as result: fn('test')
                    self.assertEqual(result.exception.status_code,status)

    def test_generate_endpoint_error_status(self):
        c=TestClient(server.app,raise_server_exceptions=False)
        for error,status in [(InferenceError('unavailable','Unavailable',503),503),
                             (InferenceError('timeout','Timed out',504),504),(RuntimeError('secret stack'),500)]:
            with patch.object(server,'run_inference',side_effect=error):
                response=c.post('/generate',json={'prompt':'test'})
                self.assertEqual(response.status_code,status)
                self.assertIn('code',response.json()['detail'])
                self.assertNotIn('secret stack',response.text)

    def test_client_preserves_http_status(self):
        for status in [500,503,504]:
            with patch.object(client.requests,'post',return_value=Mock(status_code=status,json=lambda:{'detail':{'code':'insufficient_memory','message':'private trace'}})):
                with self.assertRaises(InferenceError) as result: client.generate_answer('test')
                self.assertEqual(result.exception.status_code,status)
                self.assertNotIn('private trace',str(result.exception))

    def test_default_and_explicit_model(self):
        with patch.dict(os.environ,{'LLM_DEFAULT_MODEL':'configured-model'}),patch.object(core,'get_engine') as engine:
            engine.return_value.generate.return_value='answer'
            self.assertEqual(core.run_inference(prompt='test'),'answer')
            engine.assert_called_with(engine='ollama',model='configured-model')
            core.run_inference(prompt='test',model='explicit')
            engine.assert_called_with(engine='ollama',model='explicit')

    def test_fallback_only_for_configured_installed_model(self):
        first=Mock();first.generate.side_effect=InferenceError('insufficient_memory','Out of memory',503)
        second=Mock();second.generate.return_value='fallback answer'
        with patch.dict(os.environ,{'LLM_DEFAULT_MODEL':'large','LLM_FALLBACK_MODEL':'small'}),patch.object(core,'installed_local_models',return_value=['small:latest']),patch.object(core,'get_engine',side_effect=[first,second]) as engines:
            self.assertEqual(core.run_inference(prompt='test'),'fallback answer')
            self.assertEqual(engines.call_count,2)

    def test_no_fallback_for_explicit_model_or_timeout(self):
        for model,code in [('explicit','model_unavailable'),(None,'inference_timeout')]:
            with patch.dict(os.environ,{'LLM_FALLBACK_MODEL':'small'}),patch.object(core,'get_engine') as engines,patch.object(core,'installed_local_models') as installed:
                engines.return_value.generate.side_effect=InferenceError(code,'failure',503)
                with self.assertRaises(InferenceError): core.run_inference(prompt='test',model=model)
                self.assertEqual(engines.call_count,1);installed.assert_not_called()

    def test_uninstalled_fallback_rejected(self):
        with patch.dict(os.environ,{'LLM_FALLBACK_MODEL':'small'}),patch.object(core,'get_engine') as engines,patch.object(core,'installed_local_models',return_value=[]):
            engines.return_value.generate.side_effect=InferenceError('model_unavailable','failure',503)
            with self.assertRaisesRegex(InferenceError,'not installed locally'): core.run_inference(prompt='test')
            self.assertEqual(engines.call_count,1)

    def test_cloud_model_not_eligible_for_fallback(self):
        response=Mock();response.json.return_value={'models':[{'name':'local'},{'name':'cloud','remote_host':'https://example.invalid'}]}
        with patch('llm_service.engines.ollama.requests.get',return_value=response):
            self.assertEqual(installed_local_models(),['local'])

    def test_api_propagates_rag_failure(self):
        c=TestClient(app.app,raise_server_exceptions=False)
        for status in [503,504,500]:
            with patch('rag.repo_detector.get_all_available_repos',return_value=['demo']),patch('rag.repo_detector.detect_repo_from_question',return_value={'status':'unique_match','repo_id':'demo'}),patch.object(app,'rag_answer',side_effect=InferenceError('failure','Failed',status)):
                self.assertEqual(c.post('/ask',json={'session_id':'failure','user':'explain demo'}).status_code,status)

    def test_rag_does_not_hide_embedding_or_generation_failure(self):
        store=Mock();store.search.return_value=[{'text':'fixture','score':0.8,'metadata':{}}]
        with patch.dict(rag._VECTOR_STORES,{'failure-demo':store}),patch.object(rag,'load_repo_profile',return_value=None):
            with patch.object(rag,'embed_query',side_effect=RuntimeError('offline')):
                with self.assertRaises(InferenceError): rag.rag_answer(question='test',repo_id='failure-demo')
            with patch.object(rag,'embed_query',return_value=[1.,0.]),patch.object(rag,'generate_answer',side_effect=InferenceError('timeout','timeout',504)):
                with self.assertRaises(InferenceError): rag.rag_answer(question='test',repo_id='failure-demo')
                with self.assertRaises(InferenceError): rag.rag_answer_multi_repo(question='test',repo_ids=['failure-demo'])

    def test_embedding_engine_failure_status(self):
        from app_processing.embeddings import embed_texts
        import httpx
        for error,status in [(ConnectionError(),503),(httpx.ReadTimeout('timeout'),504)]:
            with patch('app_processing.embeddings.ollama.embeddings',side_effect=error):
                with self.assertRaises(InferenceError) as result: embed_texts(['fixture'])
                self.assertEqual(result.exception.status_code,status)
