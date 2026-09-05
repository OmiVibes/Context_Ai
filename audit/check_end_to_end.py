"""Exercise the complete MCP rebuild on a local Git fixture and smaller-model RAG."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)
import mcp.server as mcp
import github.repo_sync as sync
import rag.core as core
import requests
from mcp.schemas import ReindexRequest

results=[]
def check(name,fn,mode):
    start=time.monotonic()
    try:
        detail=fn(); status='PASS'
    except Exception as e:
        detail=f'{type(e).__name__}: {e}'; status='FAIL'
    row=dict(name=name,status=status,mode=mode,seconds=round(time.monotonic()-start,2),detail=str(detail or 'OK'))
    results.append(row); print(json.dumps(row),flush=True)
    (ROOT/'audit/end_to_end_results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
def require(ok,msg):
    if not ok: raise AssertionError(msg)
def git(*args):
    subprocess.run(['git',*map(str,args)],check=True,capture_output=True,timeout=15)

with tempfile.TemporaryDirectory(prefix='context_e2e_audit_') as tmp:
    base=Path(tmp); remote=base/'remote'; remote.mkdir()
    tool=base/'tool'; tool.mkdir()
    git('init',remote)
    (remote/'README.md').write_text('# Calculator\n\nA calculator that adds two numbers.\n',encoding='utf-8')
    (remote/'calculator.py').write_text('def add(a, b):\n    """Return the sum of two numbers."""\n    return a + b\n',encoding='utf-8')
    git('-C',remote,'add','.')
    git('-C',remote,'-c','user.name=Audit','-c','user.email=audit@example.invalid','commit','-m','fixture')
    os.chdir(tool)
    with patch.object(mcp,'PROJECT_ROOT',str(tool)),patch.object(sync,'BASE_REPO_DIR',str(tool/'repos')),patch.object(sync,'GITHUB_TOKEN',None):
        def rebuild():
            result=mcp.index_agent(ReindexRequest(repo_id='audit-calc',repo_url=str(remote)))
            require(result['status']=='ok',str(result))
            require((tool/'indices_store/audit-calc/indices.json').exists(),'indices missing')
            return result
        check('complete MCP rebuild',rebuild,'temporary local Git + real embeddings')
        def profile():
            p=json.loads((tool/'repo_profiles/audit-calc.json').read_text())
            require(p['title']=='Calculator' and p['description'],str(p))
        check('MCP rebuilt profile retains project metadata',profile,'temporary local Git fixture')
        def small_generate(prompt):
            r=requests.post('http://127.0.0.1:9001/generate',json={'prompt':prompt,'model':'llama3.2:1b'},timeout=120)
            r.raise_for_status()
            return r.json()['answer']
        def answer():
            with patch.object(core,'generate_answer',side_effect=small_generate):
                result=core.rag_answer(question='What does add return?',repo_id='audit-calc',show_sources=True,show_confidence=True)
            require(not result['answer'].startswith('Error'),str(result))
            require(any(x in result['answer'].lower() for x in ['sum','a + b','addition']),str(result))
            require(result.get('sources'),str(result))
            return result
        check('complete RAG with smaller installed model',answer,'real embeddings/retrieval/LLM; model override in audit process only')
        def restart():
            core._VECTOR_STORES.clear();core._REPO_PATHS.clear();core._REPO_PROFILES.clear()
            return answer()
        check('RAG lazy reload from persisted MCP index',restart,'real disk reload/embeddings/LLM; audit model override')
    os.chdir(ROOT)

def live_postman():
    collection=json.loads((ROOT/'Context_Assist_API.postman_collection.json').read_text(encoding='utf-8'))
    outcomes=[]
    for item in collection['item']:
        req=item['request']
        url=req['url']['raw'].replace('{{base_url}}','http://127.0.0.1:8000')
        if url.endswith('/index'):
            outcomes.append(item['name']+': exercised only on isolated fixture (preserves user repo)')
            continue
        body=json.loads(req['body']['raw']) if req.get('body') else None
        if body and 'session_id' in body:
            body['session_id']='audit-postman-'+body['session_id']
        r=requests.request(req['method'],url,json=body,timeout=90)
        require(r.status_code==200,f'{item["name"]}: HTTP {r.status_code}')
        response=r.json()
        outcomes.append({'name':item['name'],'http':r.status_code,'response':response})
    (ROOT/'audit/postman_responses.json').write_text(json.dumps(outcomes,indent=2),encoding='utf-8')
    return '5 read/query requests replayed; response semantics recorded separately; /index tested on temporary fixture'
check('Postman live read/query replay HTTP checks',live_postman,'real HTTP; temporary session IDs')

def original_rag():
    p=subprocess.run([sys.executable,str(ROOT/'utils/test_rag_core.py')],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=120)
    require(p.returncode==0,p.stderr[-500:])
    require('Error generating answer' not in p.stdout,p.stdout[-800:])
    return p.stdout[-800:]
check('original RAG smoke script answer check',original_rag,'real default pipeline; no indexing writes')
