"""Portable preview only. The real backend remains in siteapp and is not simulated here."""
import html,json,math,re,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from siteapp.web import create_app
from siteapp.config import ROOT
from siteapp.content import CLUSTERS,SERVICES
from siteapp.pages import PAGES

def safe_json(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')

def export(destination):
    with tempfile.TemporaryDirectory(prefix='website-export-') as temp:
        app=create_app({'data_dir':temp,'static_export':True,'testing':True,'scan_enabled':False,'preview':True})
        articles=app.state.articles
        paths=['/','/services/','/journal/','/scan/','/contact/','/status/','/account/','/access/']+list(PAGES)
        paths+=['/services/'+s['slug']+'/' for s in SERVICES]+[a['url'] for a in articles]
        paths+=['/journal/page/'+str(n)+'/' for n in range(2,math.ceil(len(articles)/12)+1)]
        for key in CLUSTERS:
            paths.append('/journal/topic/'+key+'/')
            count=sum(a['cluster']==key for a in articles)
            paths+=['/journal/topic/'+key+'/page/'+str(n)+'/' for n in range(2,math.ceil(count/12)+1)]
        records={}
        with TestClient(app) as client:
            for path in list(dict.fromkeys(paths))+['/404/']:
                response=client.get(path);expected=404 if path=='/404/' else 200
                if response.status_code!=expected:raise RuntimeError(f'{path}: expected {expected}, got {response.status_code}')
                text=response.text;main=re.search(r'<main id="main">(.*?)</main>',text,re.S);title=re.search(r'<title>(.*?)</title>',text,re.S)
                if not main or not title:raise RuntimeError('Missing layout: '+path)
                records[path]={'html':main.group(1),'title':html.unescape(title.group(1))}
                if path=='/':base=text
        css=(ROOT/'static/site.css').read_text(encoding='utf-8')
        base=base.replace('<link rel="stylesheet" href="/static/site.css">','<style>'+css+'</style>')
        base=base.replace('<script src="/static/site.js" defer></script>','')
        base=re.sub(r'<link rel="icon"[^>]+>','',base)
        base=re.sub(r'<meta name="csrf-token" content="[^"]*">','<meta name="csrf-token" content="">',base)
        index=[{k:a[k] for k in ('url','title','description','cluster','cluster_name','minutes')}|{'search_text':a['body']} for a in articles]
        payload={'pages':records,'articles':index}
        scripts='<script>window.PREVIEW_DATA='+safe_json(payload)+';</script>'
        for name in ('site.js','preview-router.js'):
            scripts+='<script>'+(ROOT/'static'/name).read_text(encoding='utf-8')+'</script>'
        base=base.replace('</body>',scripts+'</body>')
        destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True);destination.write_text(base,encoding='utf-8')
        result={'routes':len(records)-1,'articles':len(articles),'bytes':destination.stat().st_size,'api_connected':False}
        print(json.dumps(result,ensure_ascii=False));return result

if __name__=='__main__':export(sys.argv[1] if len(sys.argv)>1 else ROOT/'preview/index.html')
