import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app_processing.embeddings import clean, embed_texts
from app_processing.file_loader import clean_unicode, load_repo_files, mask_secrets
from app_processing.file_reader import read_file
from app_processing.chunker import chunk_text
from vector_store.store import VectorStore
from rag.metrics_extractor import extract_accuracy


class ContentTests(unittest.TestCase):
    def test_exact_fenced_code_audit_case(self):
        self.assertIn('return 42', clean('```python\ndef answer(): return 42\n```'))

    def test_fenced_languages_preserve_code_and_indentation(self):
        for language, code in [('python', 'print("hello")'), ('javascript', 'const x = `hello`;'),
                               ('sql', 'SELECT * FROM users;'), ('', '\tindented();')]:
            with self.subTest(language=language):
                self.assertEqual(clean(f'```{language}\n{code}\n\n```'), code)
        self.assertEqual(clean('~~~python\ndef f():\n    return 42\n~~~'), 'def f():\n    return 42')

    def test_fences_do_not_remove_prose_inline_or_unclosed_source(self):
        self.assertEqual(clean('Before\n```py\nx = 1\n```\nAfter'), 'Before\nx = 1\nAfter')
        for text in ['literal = "```hello```"', '```python\nprint("hello")', '    indented = True']:
            self.assertEqual(clean(text), text)
        self.assertEqual(clean('````md\n```python\nx = 1\n```\n````'), '```python\nx = 1\n```')

    def test_unicode_read_clean_chunk_embed_store_retrieve(self):
        words = ['नमस्ते', '中文', '日本語', 'café', '→', '✓', '🙂', 'e\u0301']
        content = 'message = "' + ' '.join(words) + '"\n'
        with tempfile.TemporaryDirectory(prefix='context_unicode_') as tmp:
            root = Path(tmp)
            (root/'source.py').write_text(content, encoding='utf-8')
            self.assertEqual(read_file(root/'source.py'), content)
            self.assertEqual(clean_unicode(content), content)
            docs = load_repo_files(str(root))
            chunks = [c for doc in docs for c in chunk_text(doc['text'], doc['metadata'])]
            texts = [c['text'] for c in chunks]
            with patch('app_processing.embeddings.ollama.embeddings', return_value={'embedding':[1., 0.]}) as embed:
                vectors = embed_texts(texts)
                prompt = ' '.join(call.kwargs['prompt'] for call in embed.call_args_list)
            with patch('vector_store.store.BASE_VECTOR_DIR', str(root/'vectors')):
                store = VectorStore(vectors, texts, [c['metadata'] for c in chunks])
                store.save('unicode')
                result = VectorStore.load('unicode').search([1., 0.], 'message', threshold=0)
            retrieved = ' '.join(hit['text'] for hit in result)
            for word in words:
                self.assertIn(word, prompt)
                self.assertIn(word, retrieved)

    def test_invalid_utf8_is_not_silently_discarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'broken.py'
            path.write_bytes(b'x="\xff"')
            with self.assertRaises(UnicodeDecodeError): read_file(path)

    def test_nested_json_secrets_preserve_unrelated_values(self):
        keys = ['password', 'passwd', 'token', 'api_key', 'apiKey', 'secret', 'access_token']
        data = {'nested':[{'credentials':{key:'fixture-'+key for key in keys}}],
                'description':'password: this describes a field', 'language':'中文',
                'normal':'authenticationConfiguration', 'enabled':True}
        result = json.loads(mask_secrets(json.dumps(data, ensure_ascii=False)))
        self.assertTrue(all(v == '<MASKED_SECRET>' for v in result['nested'][0]['credentials'].values()))
        for key in ['description', 'language', 'normal', 'enabled']:
            self.assertEqual(result[key], data[key])

    def test_config_assignments_redact_only_value(self):
        for text, expected in [('password = "abc"; enabled = True', 'password = "<MASKED_SECRET>"; enabled = True'),
                               ('apiKey: abc123', 'apiKey: <MASKED_SECRET>'),
                               ('access_token = "a\\\"b"', 'access_token = "<MASKED_SECRET>"')]:
            self.assertEqual(mask_secrets(text), expected)
        normal = 'authenticationConfiguration = True\npassword == provided\ntokenizer = "ordinary"'
        self.assertEqual(mask_secrets(normal), normal)

    def test_existing_assignment_secret_is_masked(self):
        self.assertNotIn('abcdefgh123456', mask_secrets('api_key = "abcdefgh123456"'))

    def test_json_still_masks_recognizable_secret_signatures(self):
        value = 'AKIA1234567890123456'
        self.assertNotIn(value, mask_secrets(json.dumps({'note':value})))

    def test_unicode_readme_profile(self):
        from repo_profiles.extractor import build_repo_profile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'README.md').write_text('# 中文 日本語\n\nनमस्ते café → ✓\n',encoding='utf-8')
            cwd=os.getcwd()
            try:
                os.chdir(root)
                profile=build_repo_profile('unicode',str(root),'https://example.invalid/repo')
            finally:
                os.chdir(cwd)
            self.assertEqual(profile['title'],'中文 日本語')
            self.assertEqual(profile['description'],'नमस्ते café → ✓')

    def test_accuracy_formats(self):
        for text, expected in [('Accuracy: 94.8 percent','94.8%'), ('Accuracy: 95%','95%'),
                               ('accuracy of 95%', '95%'), ('95 percent accuracy','95%'),
                               ('validation accuracy = 0.948','94.8%'), ('accuracy = 0.4','40.0%'),
                               ('accuracy: 0.5 percent','0.5%'), ('ACCURACY: 100 percent','100%')]:
            with self.subTest(text=text): self.assertEqual(extract_accuracy(text), expected)
        self.assertIsNone(extract_accuracy('accuracy: 195 percent'))
        self.assertIsNone(extract_accuracy('CPU usage: 95 percent'))
