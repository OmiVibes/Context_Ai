import hashlib
import hmac
import importlib
import os
import unittest
from unittest.mock import Mock, patch
from fastapi import HTTPException

import github.api as api
from rag.milestones import list_milestones
from rag.risk import detect_risks


class GitHubEdgeTests(unittest.TestCase):
    def test_open_risks_exclude_closed_issues_and_pull_requests(self):
        issues=[{'title':'Open bug','state':'open','body':'broken','html_url':'https://example.invalid/open'},
                {'title':'Fixed bug','state':'closed','html_url':'https://example.invalid/closed'},
                {'title':'Fix bug','state':'open','pull_request':{},'html_url':'https://example.invalid/pr'}]
        with patch('rag.risk.fetch_issues',return_value=issues):
            result=detect_risks('owner','repo')
        self.assertEqual(result,[{'title':'Open bug','url':'https://example.invalid/open'}])

    def test_mocked_authenticated_success_drives_milestones_and_risks(self):
        issues=[{'title':'Phase 1 bug','state':'open','body':'broken','labels':[{'name':'phase-1'}],
                 'html_url':'https://example.invalid/open'}]
        response=Mock(status_code=200);response.json.return_value=issues
        with patch.dict(os.environ,{'GITHUB_TOKEN':'fixture-only'}),patch.object(api.requests,'get',return_value=response) as get:
            self.assertEqual(list_milestones('owner','repo')[0]['open_items'],1)
            self.assertEqual(detect_risks('owner','repo')[0]['title'],'Phase 1 bug')
            self.assertEqual(get.call_count,2)

    def test_webhook_valid_wrong_missing_and_malformed(self):
        with patch.dict(os.environ,{'GITHUB_WEBHOOK_SECRET':'fixture-secret'}):
            module=importlib.import_module('github.webhook')
        with patch.object(module,'GITHUB_WEBHOOK_SECRET','fixture-secret'):
            valid='sha256='+hmac.new(b'fixture-secret',b'{}',hashlib.sha256).hexdigest()
            module.verify_github_signature(b'{}',valid)
            for signature in [None,'','malformed','sha256=abc','sha1='+'a'*64,'sha256='+'z'*64,'sha256='+'a'*64+'=extra']:
                with self.subTest(signature=signature), self.assertRaises(HTTPException) as error:
                    module.verify_github_signature(b'{}',signature)
                self.assertEqual(error.exception.status_code,400)
            with self.assertRaises(HTTPException) as error:
                module.verify_github_signature(b'tampered',valid)
            self.assertEqual(error.exception.status_code,401)
