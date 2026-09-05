from contextlib import asynccontextmanager
import math,secrets
from urllib.parse import urlencode,urlsplit
from xml.sax.saxutils import escape
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from jinja2 import Environment,FileSystemLoader,select_autoescape
from .config import settings,ROOT
from .content import SERVICES,CLUSTERS,load_articles,article_counts,markdown
from .pages import PAGES
from .storage import Store
from .worker import Worker
from .providers import Mailer
from .api import install_api,report_view

def create_app(overrides=None):
    cfg=settings(overrides);store=Store(cfg);worker=Worker(store,cfg)
    env=Environment(loader=FileSystemLoader(ROOT/'templates'),autoescape=select_autoescape(['html','xml']))
    all_articles=load_articles();counts=article_counts(all_articles)
    if not cfg['preview'] and counts['published']<100: raise ValueError('Публичный релиз: нужны 100 проверенных опубликованных материалов.')
    articles=all_articles if cfg['preview'] else [a for a in all_articles if a['status']=='published' and a['fact_check_status']=='checked' and a['published_at']]
    @asynccontextmanager
    async def lifespan(app):
        if cfg['scan_enabled'] and cfg['public_scanner'] and cfg['egress_confirmed'] and not cfg['testing'] and not cfg['static_export']: worker.start()
        yield
        worker.stop()
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.cfg=cfg;app.state.store=store;app.state.worker=worker;app.state.mailer=Mailer(cfg)
    app.state.articles=articles;app.state.counts=counts
    app.add_middleware(SessionMiddleware,secret_key=cfg['session_secret'],session_cookie='business_session',max_age=7*86400,same_site='lax',https_only=urlsplit(cfg['base_url']).scheme=='https')
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=cfg['trusted_hosts'])
    @app.middleware('http')
    async def security_headers(request,call_next):
        request.state.nonce=secrets.token_urlsafe(18)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'nonce-"+request.state.nonce+"'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        private=request.url.path.startswith(('/api/','/report/','/access/','/account/','/scan/'))
        if cfg['preview'] or private: response.headers['X-Robots-Tag']='noindex, nofollow'
        if private or 'text/html' in response.headers.get('content-type',''): response.headers['Cache-Control']='private, no-store'
        if not cfg['preview']: response.headers['Strict-Transport-Security']='max-age=31536000'
        return response
    def owner(request):
        if 'owner' not in request.session: request.session['owner']='anon_'+secrets.token_urlsafe(24)
        if 'csrf' not in request.session: request.session['csrf']=secrets.token_urlsafe(32)
        return request.session['owner']
    def available(request):
        return cfg['scan_enabled'] and not cfg['static_export'] and (cfg['testing'] or cfg['public_scanner'] and cfg['egress_confirmed'])
    def render(request,template,title,description='',status=200,noindex=False,**extra):
        owner(request)
        context={'cfg':cfg,'title':title,'description':description,'canonical':request.url.path,'csrf':request.session['csrf'],
          'services':SERVICES,'articles':articles,'counts':counts,'clusters':CLUSTERS,'noindex':noindex,'active':'','related':[],
          'schema':None,'nonce':request.state.nonce,'dark':False,'scan_available':available(request),'body_class':''}
        context.update(extra)
        return HTMLResponse(env.get_template(template).render(context),status_code=status)
    def missing(request):
        return render(request,'page.html','Страница не найдена.','Адрес отсутствует или недоступен.',status=404,noindex=True,eyebrow='404',content=markdown('[На главную](/) · [В журнал](/journal/) · [Мои отчёты](/account/)'))
    @app.exception_handler(404)
    async def notfound(request,exc):
        if request.url.path.startswith('/api/'): return JSONResponse({'code':'not_found','message':'Ресурс не найден или недоступен.'},status_code=404)
        return missing(request)
    install_api(app,cfg,store,owner,available)
    @app.get('/')
    def home(request:Request):
        schema={'@context':'https://schema.org','@type':'WebSite','name':cfg['name'],'url':cfg['base_url'],'inLanguage':'ru'}
        return render(request,'home.html','Бизнесу нужен не ИИ. Бизнесу нужен результат.','SEO, GEO и ограниченные ИИ-системы: от публичного сайта до автоматизации конкретного процесса.',schema=schema,body_class='home')
    @app.get('/services/')
    def services(request:Request):
        return render(request,'services.html','Пять направлений ИИ-систем для бизнеса','Аудит, SEO/GEO, доступность для агентов, автоматизация и сопровождение.',active='services')
    @app.get('/services/{slug}/')
    def service(request:Request,slug:str):
        s=next((s for s in SERVICES if s['slug']==slug),None)
        if s is None: return missing(request)
        schema={'@context':'https://schema.org','@type':'Service','name':s['title'],'description':s['intro']}
        return render(request,'service.html',s['title'],s['intro'],s=s,active='services',schema=schema,related=[a for a in articles if a['cluster']==s['cluster']][:3])
    def journal_response(request,cluster=None,page=1):
        q=request.query_params.get('q','')[:200].strip()
        filtered=[a for a in articles if (not cluster or a['cluster']==cluster) and (not q or q.casefold() in (a['title']+' '+a['description']+' '+a['body']).casefold())]
        pages=max(1,math.ceil(len(filtered)/12))
        if page<1 or page>pages: return missing(request)
        root='/journal/topic/'+cluster+'/' if cluster else '/journal/'
        def page_url(n):
            path=root if n==1 else root+'page/'+str(n)+'/'
            return path+('?' + urlencode({'q':q}) if q else '')
        title=CLUSTERS.get(cluster,'Журнал решений')+(' · Страница '+str(page) if page>1 else '')
        return render(request,'journal.html',title,'Практические материалы о выборе, проверке и стоимости SEO/GEO и ИИ-систем.',active='journal',filtered=filtered[(page-1)*12:page*12],query=q,cluster=cluster,page=page,pages=pages,total=len(filtered),page_url=page_url,label=('Поиск: '+q) if q else CLUSTERS.get(cluster,'Все материалы'),noindex=bool(q))
    @app.get('/journal/')
    def journal(request:Request): return journal_response(request)
    @app.get('/journal/page/{page}/')
    def journal_page(request:Request,page:int): return journal_response(request,page=page)
    @app.get('/journal/topic/{cluster}/')
    def cluster_page(request:Request,cluster:str): return journal_response(request,cluster=cluster) if cluster in CLUSTERS else missing(request)
    @app.get('/journal/topic/{cluster}/page/{page}/')
    def cluster_pagination(request:Request,cluster:str,page:int): return journal_response(request,cluster=cluster,page=page) if cluster in CLUSTERS else missing(request)
    @app.get('/journal/{slug}/')
    def article(request:Request,slug:str):
        a=next((a for a in articles if a['slug']==slug),None)
        if a is None: return missing(request)
        related=[x for i in a['related_ids'] for x in articles if x['id']==i and x['id']!=a['id']]
        related.extend(x for x in articles if x['cluster']==a['cluster'] and x['id']!=a['id'] and x not in related)
        schema={'@context':'https://schema.org','@type':'Article','headline':a['title'],'description':a['description'],'inLanguage':'ru'}
        if a['published_at']: schema['datePublished']=a['published_at']
        return render(request,'article.html',a['title'],a['description'],article=a,related=related[:3],schema=schema,active='journal')
    @app.get('/scan/')
    def scan(request:Request): return render(request,'scan.html','Готовность сайта к поиску и ИИ','Ограниченная проверка публичного HTML. Неизвестность не подменяется плохим баллом.',domain=request.query_params.get('domain','')[:253],noindex=True)
    @app.get('/contact/')
    def contact(request:Request): return render(request,'contact.html','Что должно измениться?','Подготовьте краткий бриф: роль, задача, бюджет и срок. В предпросмотре он не отправляется.')
    @app.get('/status/')
    def status(request:Request): return render(request,'status.html','Что работает. Что не подключено.','Реализованные функции, зависимости, ограничения и точные счётчики.',noindex=True)
    @app.get('/access/')
    def access(request:Request): return render(request,'access.html','Подтвердить доступ.','Открытие письма не расходует одноразовую ссылку. Подтверждение выполняется кнопкой.',noindex=True)
    @app.get('/account/')
    def account(request:Request): return render(request,'account.html','Мои отчёты.','Только результаты текущего владельца.',noindex=True,jobs=store.list_jobs(owner(request)))
    @app.get('/report/{job_id}/')
    def report(request:Request,job_id:str):
        current=owner(request);job=store.get_job(job_id,current)
        if not job: return missing(request)
        data=report_view(job,current)
        return render(request,'report.html','Приватный результат проверки','Структурные наблюдения вашего сайта.',noindex=True,job=job,report=data,evidence_by_id={e['id']:e for e in (data or {}).get('evidence',[])},status_names={'pass':'Подтверждено','fail':'Замечание','unknown':'Неизвестно','not_applicable':'Неприменимо'})
    @app.get('/healthz')
    def health(): return {'status':'ok','preview':cfg['preview'],'articles':counts,'paid_api_enabled':False}
    @app.get('/robots.txt')
    def robots():
        text='User-agent: *\nDisallow: /\n' if cfg['preview'] else 'User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /scan/\nDisallow: /report/\nDisallow: /account/\nDisallow: /access/\nSitemap: '+cfg['base_url']+'/sitemap.xml\n'
        return Response(text,media_type='text/plain')
    @app.get('/sitemap.xml')
    def sitemap():
        paths=[]
        if not cfg['preview']:
            paths=['/','/services/','/journal/','/contact/']+list(PAGES)+['/services/'+s['slug']+'/' for s in SERVICES]+[a['url'] for a in articles]
            paths+=['/journal/page/'+str(n)+'/' for n in range(2,math.ceil(len(articles)/12)+1)]
            for cluster in CLUSTERS:
                paths.append('/journal/topic/'+cluster+'/')
                count=sum(a['cluster']==cluster for a in articles)
                paths+=['/journal/topic/'+cluster+'/page/'+str(n)+'/' for n in range(2,math.ceil(count/12)+1)]
        xml='<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join('<url><loc>'+escape(cfg['base_url']+p)+'</loc></url>' for p in paths)+'</urlset>'
        return Response(xml,media_type='application/xml')
    app.mount('/static',StaticFiles(directory=ROOT/'static',check_dir=False),name='static')
    @app.get('/{path:path}')
    def page(request:Request,path:str):
        p=PAGES.get('/'+path)
        if not p: return missing(request)
        return render(request,'page.html',p['title'],p['description'],eyebrow=p['eyebrow'],content=markdown(p['body']),dark='/'+path not in ('/privacy/','/terms/'))
    return app

app=create_app()
