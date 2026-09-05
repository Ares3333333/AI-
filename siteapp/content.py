import json
import re
import mistune
from .config import ROOT

CLUSTERS={'geo':'GEO и видимость','ai-search':'ИИ-поиск','seo':'SEO и архитектура','automation':'Автоматизация','agents':'ИИ-агенты','mcp':'MCP и доступ к данным','proof':'Доказательства и метод','economics':'Экономика решений'}
DESTINATIONS={'geo':'seo-geo','ai-search':'seo-geo','seo':'seo-geo','automation':'ai-systems','agents':'ai-systems','mcp':'agent-readiness','proof':'ai-audit','economics':'ai-audit'}
SOURCES=json.loads((ROOT/'content/sources.json').read_text(encoding='utf-8'))
SERVICES=json.loads((ROOT/'content/services.json').read_text(encoding='utf-8'))
markdown=mistune.create_markdown(escape=True,plugins=['table'])

def load_articles():
    result=[]
    for path in sorted((ROOT/'content/articles').glob('*.md')):
        text=path.read_text(encoding='utf-8')
        if not text.startswith('---\n'): raise ValueError('Нет метаданных: '+path.name)
        metadata,body=text[4:].split('\n---\n',1)
        a=json.loads(metadata)
        if not re.fullmatch(r'A\d{3}',a['id']) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',a['slug']): raise ValueError('Некорректный ID/slug: '+path.name)
        a['body']=body.strip()
        if not a['body']: raise ValueError('Нет полного текста: '+path.name)
        a['html']=markdown(a['body'])
        a['word_count']=len(re.findall(r'[\w-]+',a['body']))
        a['minutes']=max(2,round(a['word_count']/180))
        a['url']='/journal/'+a['slug']+'/'
        a['cluster_name']=CLUSTERS[a['cluster']]
        a['headings']=[x[3:] for x in body.splitlines() if x.startswith('## ')]
        a.setdefault('status','draft'); a.setdefault('fact_check_status','pending')
        a.setdefault('published_at',None); a.setdefault('updated_at',None)
        a.setdefault('search_volume',None); a.setdefault('demand_status','unverified')
        a.setdefault('primary_query_hypothesis',a['title'])
        a.setdefault('sources',[SOURCES[s] for s in a.get('source_ids',[])])
        a.setdefault('source_note','Практическая схема предложена редакцией. Первичные источники приведены для проверки фактических утверждений; индивидуальная проверка поискового спроса и финальная редакционная приёмка не завершены.')
        a.setdefault('service_url','/services/'+DESTINATIONS[a['cluster']]+'/')
        a.setdefault('cta_label','Перейти от чтения к конкретной задаче')
        a.setdefault('related_ids',[])
        result.append(a)
    if len({a['id'] for a in result})!=len(result) or len({a['slug'] for a in result})!=len(result): raise ValueError('Повтор ID или URL статьи')
    return result

def article_counts(items):
    written=[a for a in items if a.get('body') and a.get('status')!='planned']
    return {'planned':100,'written':len(written),'fact_checked':sum(a.get('fact_check_status')=='checked' for a in written),
      'published':sum(a.get('status')=='published' and bool(a.get('published_at')) and a.get('fact_check_status')=='checked' for a in written)}
