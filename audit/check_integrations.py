"""Additional bounded integration checks; all Git mutations use temporary repos."""
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
RESULTS=[]

def check(name,fn,mode='fixture'):
    start=time.monotonic()
    try:
        detail=fn(); status='PASS'
    except Exception as exc:
        detail=f'{type(exc).__name__}: {exc}'; status='FAIL'
    row=dict(name=name,status=status,mode=mode,seconds=round(time.monotonic()-start,2),detail=str(detail or 'OK'))
    RESULTS.append(row)
    print(json.dumps(row),flush=True)
    (ROOT/'audit/integration_results.json').write_text(json.dumps(RESULTS,indent=2),encoding='utf-8')

def require(ok,msg):
    if not ok: raise AssertionError(msg)

def git(*args):
    return subprocess.run(['git',*map(str,args)],capture_output=True,text=True,check=True,timeout=20)

def git_sync():
    import github.repo_sync as sync
    with tempfile.TemporaryDirectory(prefix='context_git_audit_') as tmp:
        base=Path(tmp)
        remote=base/'remote'; remote.mkdir()
        git('init',remote)
        (remote/'README.md').write_text('# Fixture\n\nDescription\n',encoding='utf-8')
        (remote/'LICENSE').write_text('Fixture license',encoding='utf-8')
        (remote/'main.py').write_text('value = 1',encoding='utf-8')
        git('-C',remote,'add','.')
        git('-C',remote,'-c','user.name=Audit','-c','user.email=audit@example.invalid','commit','-m','fixture')
        with patch.object(sync,'BASE_REPO_DIR',str(base/'clones')),patch.object(sync,'GITHUB_TOKEN',None):
            clone=Path(sync.sync_repo(str(remote),'fixture'))
            require((clone/'main.py').exists(),'clone failed')
            check('Git clone basic operation',lambda:'local fixture cloned')
            check('Git sync preserves README and LICENSE',lambda:require((clone/'README.md').exists() and (clone/'LICENSE').exists(),'README and LICENSE deleted'))
            (clone/'main.py').write_text('value = 999',encoding='utf-8')
            try:
                sync.sync_repo(str(remote),'fixture')
            except sync.RepositorySyncError:
                pass  # Safe refusal is the intended update contract.
            check('Git sync preserves local edits',lambda:require((clone/'main.py').read_text()=='value = 999','reset --hard discarded local edit'))
check('Git sync isolated integration',git_sync)

def ui_button(label):
    from streamlit.testing.v1 import AppTest
    at=AppTest.from_file(str(ROOT/'ui/streamlit_app.py'),default_timeout=35).run()
    next(b for b in at.button if b.label==label).click()
    at.run()
    require(not at.exception,str(at.exception))
    require(not at.error,'; '.join(x.value for x in at.error))
    return 'button completed without UI error'
check('UI milestones tab real button',lambda:ui_button('Load Milestones'),'AppTest + real MCP/GitHub')
check('UI risks tab real button',lambda:ui_button('Analyze Risks'),'AppTest + real MCP/GitHub')

def rebuild_missing():
    p=subprocess.run([sys.executable,'mcp/server.py'],cwd=ROOT,input=json.dumps({'jsonrpc':'2.0','id':5,'method':'call/rebuild_index','params':{}}),capture_output=True,text=True,timeout=30)
    r=json.loads(p.stdout)
    require('required' in str(r.get('error','')),str(r))
    return r
check('MCP rebuild missing inputs rejected',rebuild_missing,'real subprocess, no Git writes')

def groups():
    from rag.repo_detector import detect_repo_from_question
    with tempfile.TemporaryDirectory(prefix='context_group_audit_') as tmp:
        for repo in ['shop-frontend','shop-backend']:
            folder=Path(tmp)/'indices_store'/repo; folder.mkdir(parents=True)
            (folder/'indices.json').write_text(json.dumps({'repo_id':repo,'indexed_files':['app.py']}),encoding='utf-8')
        result=detect_repo_from_question('Explain shop-frontend',tmp)
        require(result['status']=='unique_match' and result['repo_id']=='shop-frontend',str(result))
check('explicit frontend selection stays in one repo',groups)

def old_tests():
    with tempfile.TemporaryDirectory(prefix='context_legacy_audit_') as tmp:
        p=subprocess.run([sys.executable,str(ROOT/'utils/test_vector_store.py')],cwd=tmp,capture_output=True,text=True,timeout=30)
        require(p.returncode==0,p.stderr[-500:])
        return p.stdout.strip()
check('original vector-store smoke script',old_tests)

def changes():
    from github.change_detector import extract_changed_files
    r=extract_changed_files({'commits':[{'added':['a'],'modified':['b'],'removed':['c']},{'modified':['b']}]})
    require(r=={'added':['a'],'modified':['b'],'removed':['c']},str(r))
check('GitHub change extraction and deduplication',changes)

def small_model():
    import requests
    r=requests.post('http://127.0.0.1:9001/generate',json={'prompt':'Reply with exactly AUDIT_OK.','model':'llama3.2:1b'},timeout=120)
    answer=r.json().get('answer','')
    require('AUDIT_OK' in answer and not answer.startswith('Error'),answer)
    return answer
check('installed smaller LLM fallback',small_model,'live local llama3.2:1b; no config change')
