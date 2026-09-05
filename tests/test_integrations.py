import asyncio
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import requests
from fastapi.testclient import TestClient
import app
import github.api as github
import mcp.server as mcp
from utils.errors import ServiceError

ROOT=Path(__file__).resolve().parents[1]


class GitHubTests(unittest.TestCase):
    def test_missing_token_is_configuration_error(self):
        with patch.dict(os.environ,{'GITHUB_TOKEN':''}),patch.object(github.requests,'get') as get:
            with self.assertRaises(ServiceError) as error: github.fetch_issues('owner','repo')
            self.assertEqual(error.exception.code,'github_not_configured')
            get.assert_not_called()

    def test_auth_failure_is_clear_and_does_not_retry_anonymously(self):
        for status,code in [(401,'github_auth_failed'),(403,'github_access_denied'),(404,'github_repository_unavailable')]:
            with patch.dict(os.environ,{'GITHUB_TOKEN':'fixture-secret'}),patch.object(github.requests,'get',return_value=Mock(status_code=status)) as get:
                with self.assertRaises(ServiceError) as error: github.fetch_issues('owner','repo')
                self.assertEqual(error.exception.code,code)
                self.assertNotIn('fixture-secret',str(error.exception));self.assertEqual(get.call_count,1)
                self.assertEqual(get.call_args.kwargs['timeout'],15)

    def test_timeout_status(self):
        with patch.dict(os.environ,{'GITHUB_TOKEN':'fixture'}),patch.object(github.requests,'get',side_effect=requests.Timeout()):
            with self.assertRaises(ServiceError) as error: github.fetch_issues('owner','repo')
            self.assertEqual(error.exception.status_code,504)

    def test_configuration_health_never_claims_auth_verified(self):
        with patch.dict(os.environ,{'GITHUB_TOKEN':'fixture-secret'}):
            result=TestClient(app.app).get('/health/github')
            self.assertEqual(result.json(),{'configured':True,'authentication_verified':False})
            self.assertNotIn('fixture-secret',result.text)


class MCPTests(unittest.TestCase):
    def rpc(self,method,params=None,id=7):
        return asyncio.run(mcp.process_message(json.dumps({'jsonrpc':'2.0','id':id,'method':method,'params':params or {}})))

    def test_initialize_negotiates_capabilities_and_version(self):
        for version in ['2024-11-05','2025-11-25','unknown']:
            result=self.rpc('initialize',{'protocolVersion':version,'capabilities':{},'clientInfo':{'name':'test','version':'1'}})
            self.assertEqual(result['id'],7)
            self.assertEqual(result['result']['protocolVersion'],version if version in mcp.SUPPORTED_VERSIONS else mcp.SUPPORTED_VERSIONS[-1])
            self.assertIn('tools',result['result']['capabilities'])

    def test_tool_discovery_has_schemas(self):
        result=self.rpc('tools/list')['result']['tools']
        self.assertEqual(len(result),4)
        for tool in result:
            self.assertEqual(tool['inputSchema']['type'],'object')
            self.assertIn('description',tool)

    def test_standard_and_custom_greetings(self):
        args={'question':'hello'}
        standard=self.rpc('tools/call',{'name':'ask_project','arguments':args})['result']
        custom=self.rpc('call/ask_project',args)['result']
        self.assertFalse(standard['isError'])
        self.assertEqual(json.loads(standard['content'][0]['text']),custom)

    def test_protocol_errors_and_ids(self):
        self.assertEqual(self.rpc('unknown')['error']['code'],-32601)
        self.assertEqual(self.rpc('tools/call',{'name':'unknown'})['error']['code'],-32602)
        self.assertEqual(self.rpc('call/ask_project',{})['error']['code'],-32602)
        self.assertEqual(self.rpc('initialize',{})['error']['code'],-32602)
        self.assertEqual(asyncio.run(mcp.process_message('{'))['error']['code'],-32700)
        self.assertEqual(asyncio.run(mcp.process_message('[]'))['error']['code'],-32600)
        self.assertEqual(self.rpc('unknown',id='request-123')['id'],'request-123')

    def test_notifications_have_no_reply_or_side_effect(self):
        with patch.object(mcp,'index_agent') as rebuild:
            for method in ['notifications/initialized','call/rebuild_index','unknown']:
                self.assertIsNone(asyncio.run(mcp.process_message(json.dumps({'jsonrpc':'2.0','method':method}))))
            rebuild.assert_not_called()

    def test_runtime_errors_use_tool_error_content_or_custom_error(self):
        with patch.object(mcp,'list_milestones',side_effect=ServiceError('github_auth_failed','Check GITHUB_TOKEN',503)):
            args={'repo_owner':'owner','repo_name':'repo'}
            self.assertTrue(self.rpc('tools/call',{'name':'list_milestones','arguments':args})['result']['isError'])
            error=self.rpc('call/list_milestones',args)
            self.assertEqual(error['id'],7);self.assertEqual(error['error']['data']['status'],503)

    def test_rebuild_rejects_traversal_without_sync(self):
        with patch.object(mcp,'sync_repo') as sync:
            for repo in ['../outside','..\\outside','%2e%2e%2foutside','C:\\outside']:
                result=self.rpc('call/rebuild_index',{'repo_id':repo,'repo_url':'https://github.com/owner/repo'})
                self.assertEqual(result['error']['code'],-32602)
                self.assertEqual(self.rpc('call/ask_project',{'question':'hello','repo_id':repo})['error']['code'],-32602)
            sync.assert_not_called()

    def test_stdio_responds_without_waiting_for_eof(self):
        # communicate timeout bounds the whole test; the child probe waits for a
        # response while keeping server stdin open, then sends another request.
        probe='''import subprocess,sys,json
p=subprocess.Popen([sys.executable,"mcp/server.py"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
try:
 for i in [1,2]:
  p.stdin.write(json.dumps({"jsonrpc":"2.0","id":i,"method":"ping"})+"\\n");p.stdin.flush()
  assert json.loads(p.stdout.readline())["id"]==i
finally:
 p.terminate();p.communicate(timeout=5)
'''
        p=subprocess.run([sys.executable,'-c',probe],cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr[-1000:])

    def test_one_shot_compatibility(self):
        p=subprocess.run([sys.executable,'mcp/server.py'],cwd=ROOT,input=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/list'}),capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr[-1000:])
        self.assertEqual(len(json.loads(p.stdout)['result']['tools']),4)
