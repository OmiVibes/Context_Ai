"""Repeatable project audit. Destructive indexing is confined to temporary fixtures.
Run from project root with venv/Scripts/python.exe audit/run_audit.py.
Requires the local API (8000), inference service (9001), UI (8501), and Ollama.
No existing repositories are indexed, rebuilt, pulled, or modified.
"""
import asyncio
import ast
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import requests

RESULTS = []


def check(name, fn, mode="deterministic"):
    start = time.monotonic()
    try:
        detail = fn()
        status = "PASS"
    except Exception as exc:
        status = "BLOCKED" if mode == "live read-only GitHub" and getattr(exc, "code", None) in {"github_auth_failed", "github_not_configured"} else "FAIL"
        detail = f"{type(exc).__name__}: {exc}"
    item = dict(name=name, status=status, mode=mode,
                seconds=round(time.monotonic()-start, 2), detail=str(detail or "OK"))
    RESULTS.append(item)
    print(json.dumps(item, ensure_ascii=True), flush=True)
    (ROOT / "audit/results.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def http_get(url):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return f"HTTP {r.status_code}"


def syntax():
    paths = [p for p in ROOT.rglob("*.py") if not any(x in p.relative_to(ROOT).parts for x in ("venv", "repos", ".git", "__pycache__"))]
    for p in paths:
        ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p))
    return f"{len(paths)} Python files parsed"


check("Python syntax", syntax)
for label, url in [("API health", "http://127.0.0.1:8000/health"), ("API OpenAPI", "http://127.0.0.1:8000/openapi.json"), ("LLM docs", "http://127.0.0.1:9001/docs"), ("UI HTTP", "http://127.0.0.1:8501"), ("UI health", "http://127.0.0.1:8501/_stcore/health")]:
    check(label, lambda u=url: http_get(u), "live HTTP")


def documented_llm():
    p = subprocess.run([sys.executable, "-c", "import llm_service.server"], cwd=ROOT, capture_output=True, text=True, timeout=20)
    require(p.returncode == 0, p.stderr[-1000:])


check("README LLM module import", documented_llm)
import app
import rag.core as core
import rag.repo_detector as detector
from app_processing.file_loader import load_repo_files, mask_secrets, clean_unicode
from app_processing.chunker import chunk_text
from app_processing.embeddings import embed_texts, embed_query, clean
from utils.project_fingerprint import compute_project_fingerprint
from vector_store.store import VectorStore
from rag.metrics_extractor import extract_accuracy
from rag.repo_structure import infer_architecture
from fastapi.testclient import TestClient
from utils.errors import InferenceError

client = TestClient(app.app, raise_server_exceptions=False)  # no startup scan


def api_case(method, path, payload, status):
    r = client.request(method, path, json=payload)
    require(r.status_code == status, f"expected {status}, got {r.status_code}: {r.text[:300]}")
    return r.text[:200]


for label, method, path, payload, status in [
    ("ask greeting", "POST", "/ask", {"session_id":"audit", "user":"hello"}, 200),
    ("ask empty", "POST", "/ask", {"session_id":"audit", "user":" "}, 400),
    ("ask missing fields", "POST", "/ask", {}, 422),
    ("index empty", "POST", "/index", {"repo_id":" "}, 400),
    ("index missing repo", "POST", "/index", {"repo_id":"__audit_nonexistent__"}, 404),
    ("index missing fields", "POST", "/index", {}, 422),
    ("unknown endpoint", "GET", "/missing", None, 404),
]:
    check(label, lambda m=method,p=path,b=payload,s=status: api_case(m,p,b,s), "ASGI actual handlers")


check("technical question not mistaken for greeting", lambda: require(not app.is_generic_question("history of authentication changes"), "history starts with hi and returns canned greeting"))
check("fenced source survives embedding cleanup", lambda: require("return 42" in clean("```python\ndef answer(): return 42\n```"), "all fenced source removed"))
check("Unicode code preserved", lambda: require("中文" in clean_unicode("name = '中文'"), "Chinese characters removed"))
check("assignment secret masking", lambda: require("abcdefgh123456" not in mask_secrets('api_key = "abcdefgh123456"'), "secret retained"))
check("JSON password masking", lambda: require("test-password" not in mask_secrets('{"password": "test-password"}'), "JSON password retained"))
check("chunk length bound", lambda: require(all(len(c['text'].split()) <= 200 for c in chunk_text('word '*501, {})), "oversized chunk"))
check("empty chunk input", lambda: require(chunk_text('', {}) == [], "empty chunks emitted"))
check("decimal accuracy extraction", lambda: require(extract_accuracy('validation accuracy = 0.948') == '94.8%', "wrong conversion"))
check("percent-word accuracy extraction", lambda: require(extract_accuracy('Accuracy: 94.8 percent') == '94.8%', "documented percent format unsupported"))


def llm_live():
    r = requests.post("http://127.0.0.1:9001/generate", json={"prompt":"Reply with exactly AUDIT_OK."}, timeout=180)
    r.raise_for_status()
    answer = r.json().get("answer", "")
    require("AUDIT_OK" in answer and not answer.startswith("Error"), answer)
    return answer


check("real default LLM generation", llm_live, "live configured local default model")


def llm_failure():
    r = requests.post("http://127.0.0.1:9001/generate", json={"prompt":"test", "model":"__audit_missing_model__"}, timeout=15)
    require(r.status_code >= 400, f"HTTP {r.status_code}: {r.text[:250]}")


check("missing model produces HTTP error", llm_failure, "live HTTP")


with tempfile.TemporaryDirectory(prefix="context_assist_audit_") as tmp:
    base = Path(tmp)
    workspace = base / "workspace"
    workspace.mkdir()
    project = base / "tool"
    project.mkdir()
    os.chdir(project)
    repo = workspace / "audit-demo"
    repo.mkdir()
    (repo / "calculator.py").write_text('def add(a, b):\n    """Return the sum of two numbers."""\n    return a + b\n\naccuracy = 0.948\n', encoding="utf-8")
    (repo / "README.md").write_text('# Calculator\n\nA calculator for adding numbers.\n', encoding="utf-8")
    (repo / ".env").write_text('PASSWORD=fixture-only', encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules/ignored.js").write_text('const ignored = true;', encoding="utf-8")
    (repo / "notebook.ipynb").write_text(json.dumps({"nbformat":4,"nbformat_minor":5,"metadata":{},"cells":[{"cell_type":"code","execution_count":None,"metadata":{},"outputs":[],"id":"audit","source":"result = 2 + 2"}]}), encoding="utf-8")
    originals = {key:getattr(app,key) for key in ['BASE_DIR','WORKSPACE_ROOT','PROJECT_CONTEXT_DIR','PROFILE_DIR','CHUNK_STORE_DIR','INDICES_STORE_DIR']}
    app.BASE_DIR = str(project)
    app.WORKSPACE_ROOT = str(workspace)
    app.PROJECT_CONTEXT_DIR = str(project)
    app.PROFILE_DIR = str(project / 'repo_profiles')
    app.CHUNK_STORE_DIR = str(project / 'chunk_store')
    app.INDICES_STORE_DIR = str(project / 'indices_store')
    from utils.session_store import SessionStore
    original_session_store = app.SESSION_STORE
    app.SESSION_STORE = SessionStore(base / 'sessions.sqlite3')

    def loader():
        docs = load_repo_files(str(repo))
        paths = [d['metadata']['file_path'] for d in docs]
        require('calculator.py' in paths and 'notebook.ipynb' in paths, str(paths))
        require(not any('.env' in p or 'node_modules' in p for p in paths), str(paths))
        return paths
    check("source/notebook ingestion and excluded files", loader)
    check("architecture grounded in calculator fixture", lambda: require('tumor' not in infer_architecture(str(repo)), "calculator architecture contains tumor/CNN workflow"))

    def vectors():
        emb = embed_texts(['The add function returns the sum of two numbers.', 'The subtract function returns their difference.'])
        require(len(emb)==2 and len(emb[0])>0, 'empty embeddings')
        store = VectorStore(emb, ['add sum two numbers', 'subtract difference numbers'], [{'file_path':'a.py'}, {'file_path':'b.py'}])
        store.save('audit-vector')
        loaded = VectorStore.load('audit-vector')
        hits = loaded.search(embed_query('add sum two numbers'), 'add sum two numbers', threshold=0)
        require(hits and hits[0]['metadata']['file_path']=='a.py', str(hits))
        return f"{len(emb[0])}-dimension embeddings; persisted/reloaded; correct top match"
    check("real embeddings, vector persistence and retrieval", vectors, "live local embedding model")

    def index():
        response = client.post('/index',json={'repo_id':'audit-demo'})
        require(response.status_code==200, response.text)
        require(response.json().get('status')=='indexed', response.text)
        require((project/'indices_store/audit-demo/indices.json').exists(), 'indices missing')
        return response.json()
    check("real indexing pipeline", index, "temporary fixture + live embeddings")
    check("index preserves README", lambda: require((repo/'README.md').exists(), 'README deleted by /index'))
    check("repeat index skips unchanged input", lambda: require(client.post('/index',json={'repo_id':'audit-demo'}).json().get('action')=='skipped', 'reindexed because first index deleted README'))
    check("third index skips unchanged input", lambda: require(client.post('/index',json={'repo_id':'audit-demo'}).json().get('action')=='skipped', 'not skipped'))
    check("deterministic metadata answer", lambda: require('94.8%' in core.rag_answer(question='What is the accuracy?',repo_id='audit-demo')['answer'], 'accuracy missing'))

    def rag_live():
        result = core.rag_answer(question='What does the add function return?',repo_id='audit-demo',show_sources=True,show_confidence=True)
        require('sources' in result and any(x['file']=='calculator.py' for x in result['sources']), str(result))
        require(not result['answer'].startswith('Error'), str(result))
        require(any(w in result['answer'].lower() for w in ('sum','addition','a + b','adds')), str(result))
        return result
    check("real grounded RAG answer with sources", rag_live, "live embeddings + default LLM")

    check("repo detection by name", lambda: require(detector.detect_repo_from_question('explain audit-demo',str(project))['repo_id']=='audit-demo','not detected'))
    check("repo detection by filename", lambda: require(detector.detect_repo_from_question('explain calculator.py',str(project))['repo_id']=='audit-demo','not detected'))

    def sessions():
        one=client.post('/ask',json={'session_id':'audit-flow','user':'What is the accuracy?'}).json()
        two=client.post('/ask',json={'session_id':'audit-flow','user':'audit-demo'}).json()
        require('available_repos' in one and '94.8%' in two.get('answer',''),str((one,two)))
        return two
    check("session clarification and repo selection", sessions)
    check("session retains selected repository", lambda: require('answer' in client.post('/ask',json={'session_id':'audit-flow','user':'What is the accuracy?'}).json(), 'asks to select repository again'))

    def stale():
        (project/'vector_store/repos/audit-demo/index.faiss').unlink()
        result=client.post('/index',json={'repo_id':'audit-demo'}).json()
        require(result.get('status')=='indexed', str(result))
    check("missing vector index self-heals", stale)

    def fingerprint():
        f=repo/'fingerprint.py'
        f.write_text('value = 1',encoding='utf-8')
        stamp=f.stat().st_mtime
        before=compute_project_fingerprint(str(repo))
        f.write_text('value = 2',encoding='utf-8')
        os.utime(f,(stamp,stamp))
        require(compute_project_fingerprint(str(repo))!=before,'same-size same-timestamp content edit missed')
    check("fingerprint detects content changes", fingerprint)

    def traversal():
        outside=base/'outside'
        outside.mkdir()
        (outside/'safe.py').write_text('x = 1',encoding='utf-8')
        with patch.object(app,'embed_texts',lambda xs:[[1.,0.] for x in xs]):
            r=client.post('/index',json={'repo_id':'../outside'})
        require(r.status_code in (400,403,422),f'path traversal accepted: HTTP {r.status_code}')
    check("index rejects repository path traversal", traversal, "temporary fixture only")

    def service_failure():
        with patch.object(core,'embed_query',side_effect=RuntimeError('embedding backend unavailable')):
            try:
                result=core.rag_answer(question='Explain addition',repo_id='audit-demo')
            except InferenceError as exc:
                require(exc.status_code == 503, str(exc))
                return 'Embedding outage raises structured 503 error'
        require('error' in result['answer'].lower() or 'unavailable' in result['answer'].lower(),str(result))
    check("embedding outage distinguishable from missing evidence", service_failure, "fault injection")

    def multi():
        store=VectorStore([[1.,0.]],['A fixture returns addition.'],[{'file_path':'fixture.py'}])
        core.register_repo('audit-other',None,store)
        with patch.object(core,'embed_query',return_value=[1.,0.]), patch.object(core,'generate_answer',return_value='Fixture answer'):
            result=core.rag_answer_multi_repo(question='explain fixture',repo_ids=['audit-other'],show_sources=True,show_confidence=True)
        require(result['sources'][0]['repo_id']=='audit-other',str(result))
        return result
    check("multi-repository response/source wiring", multi, "mock embeddings and LLM")
    for key,value in originals.items():
        setattr(app,key,value)
    app.SESSION_STORE = original_session_store
    core._VECTOR_STORES.clear()
    core._REPO_PATHS.clear()
    core._REPO_PROFILES.clear()
    app._SESSIONS.clear()
    os.chdir(ROOT)


def mcp_call(method, params=None):
    p=subprocess.run([sys.executable,str(ROOT/'mcp/server.py')],input=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params or {}}),capture_output=True,text=True,encoding='utf-8',timeout=90,cwd=ROOT)
    require(p.returncode==0,p.stderr[-500:])
    require(bool(p.stdout.strip()),p.stderr[-500:])
    return json.loads(p.stdout)


def mcp_ok(method,params=None):
    result=mcp_call(method,params)
    require('error' not in result,str(result))
    return result


check("MCP custom tool discovery",lambda:mcp_ok('tools/list'),"real subprocess")
check("MCP chat greeting",lambda:mcp_ok('call/ask_project',{'question':'hello','repo_id':'facemask-detector'}),"real subprocess")
check("MCP unknown method reports JSON-RPC error",lambda:require('error' in mcp_call('audit/unknown'),'unknown method wrapped in successful result'),"real subprocess")
check("MCP standard initialize",lambda:require('protocolVersion' in mcp_call('initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'audit','version':'1'}}).get('result',{}),'initialize unsupported'),"real subprocess")
check("MCP standard tools/call",lambda:require('content' in mcp_call('tools/call',{'name':'ask_project','arguments':{'question':'hello','repo_id':'facemask-detector'}}).get('result',{}),'tools/call unsupported'),"real subprocess")


def ui():
    from streamlit.testing.v1 import AppTest
    at=AppTest.from_file(str(ROOT/'ui/streamlit_app.py'),default_timeout=30).run()
    require(not at.exception,str(at.exception))
    require(len(at.tabs)==3,f'tabs={len(at.tabs)}')
    require(at.chat_input, 'chat input missing')
    at.chat_input[0].set_value('hello').run()
    require(not at.exception and not at.error,str((at.exception,at.error)))
    return f"three tabs render; greeting chat executed; controls: {[x.label for x in at.selectbox]}"
check("Streamlit rendering and chat interaction",ui,"Streamlit AppTest + real MCP")


import rag.milestones as milestones
import rag.risk as risks
fixture_issues=[{'title':'Phase one','body':'','state':'open','labels':[{'name':'phase-1'}],'html_url':'https://example.invalid/1'}, {'title':'Fixed bug','body':'','state':'closed','labels':[],'html_url':'https://example.invalid/2'}]
def milestone_logic():
    with patch.object(milestones,'fetch_issues',return_value=fixture_issues):
        result=milestones.list_milestones('fixture','fixture')
    require(result[0]['open_items']==1 and result[0]['status']=='in-progress',str(result))
    return result
check("milestone label grouping",milestone_logic,"mock GitHub issues")
def closed_risks():
    with patch.object(risks,'fetch_issues',return_value=fixture_issues):
        result=risks.detect_risks('fixture','fixture')
    require(not result,f'closed fixed bug counted as current risk: {result}')
check("closed issues excluded from current risks",closed_risks,"mock GitHub issues")

# Public fixture repository used by the UI; no account changes or messages.
original_get=requests.get
def bounded_get(*args,**kwargs):
    kwargs.setdefault('timeout',15)
    return original_get(*args,**kwargs)
def github_read(module):
    with patch.object(requests,'get',side_effect=bounded_get):
        issues=module.fetch_issues('Dharani-Barigeda','facemask-detector')
    return f'{len(issues)} issue records returned'
check("GitHub milestone issue fetch",lambda:github_read(milestones),"live read-only GitHub")
check("GitHub risk issue fetch",lambda:github_read(risks),"live read-only GitHub")


def webhook():
    with patch.dict(os.environ,{'GITHUB_WEBHOOK_SECRET':'audit-fixture-secret'}):
        import github.webhook as webhook_module
    signature='sha256='+hmac.new(webhook_module.GITHUB_WEBHOOK_SECRET.encode(),b'{}',hashlib.sha256).hexdigest()
    webhook_module.verify_github_signature(b'{}',signature)
    try:
        webhook_module.verify_github_signature(b'tampered',signature)
    except Exception as exc:
        require(getattr(exc,'status_code',None)==401,str(exc))
    else:
        raise AssertionError('tampering accepted')
check("webhook valid and tampered signatures",webhook)
def malformed_signature():
    import github.webhook as w
    try:
        w.verify_github_signature(b'{}','malformed')
    except Exception as exc:
        require(getattr(exc,'status_code',None) in (400,401),f'uncaught {type(exc).__name__}')
check("webhook malformed signature handling",malformed_signature)


def tracked_extractor():
    p=subprocess.run(['git','ls-files','repo_profiles/extractor.py'],cwd=ROOT,capture_output=True,text=True,check=True)
    require(bool(p.stdout.strip()),'required MCP module exists locally but is excluded from git')
check("fresh clone contains profile extractor",tracked_extractor)
def postman():
    collection=json.loads((ROOT/'Context_Assist_API.postman_collection.json').read_text(encoding='utf-8'))
    paths=requests.get('http://127.0.0.1:8000/openapi.json',timeout=10).json()['paths']
    for item in collection['item']:
        req=item['request']; path='/'+ '/'.join(req['url']['path'])
        require(req['method'].lower() in paths.get(path,{}),item['name'])
    return f"{len(collection['item'])} request definitions match actual endpoint methods"
check("Postman route alignment",postman)

print('SUMMARY',json.dumps({s:sum(r['status']==s for r in RESULTS) for s in ('PASS','FAIL','BLOCKED')}),flush=True)
