import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch
import requests
from streamlit.testing.v1 import AppTest

UI=Path(__file__).resolve().parents[1]/'ui/streamlit_app.py'


class UITests(unittest.TestCase):
    def test_github_buttons_use_url_and_display_structured_errors(self):
        for label in ['Load Milestones','Analyze Risks']:
            at=AppTest.from_file(str(UI),default_timeout=15).run()
            for field in at.text_input:
                if field.label == 'GitHub Repo URL':
                    field.set_value('https://github.com/example-owner/example-repo.git')
            fake=Mock()
            fake.communicate.return_value=(json.dumps({'jsonrpc':'2.0','id':1,'error':{'code':-32000,'message':'Check GITHUB_TOKEN'}}),'')
            with patch('subprocess.Popen',return_value=fake):
                next(b for b in at.button if b.label==label).click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.error[0].value,'Check GITHUB_TOKEN')
            payload=json.loads(fake.communicate.call_args.args[0])
            self.assertEqual(payload['params'],{'repo_owner':'example-owner','repo_name':'example-repo'})

    def test_chat_api_failure_is_displayed_without_calling_mcp(self):
        at=AppTest.from_file(str(UI),default_timeout=15).run()
        with patch('requests.request',side_effect=requests.ConnectionError('offline')):
            at.chat_input[0].set_value('What does this project do?').run()
        self.assertFalse(at.exception)
        self.assertIn('Cannot reach',at.error[0].value)
