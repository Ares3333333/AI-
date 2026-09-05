from collections import deque
from datetime import datetime,timezone
from urllib.parse import urljoin,urlsplit
from urllib.robotparser import RobotFileParser
import hashlib,json,re
from defusedxml import ElementTree
from .network import SafeFetcher,FetchError,normalize_url,USER_AGENT
from .html_signals import PageParser
from .scoring import calculate_index,check,page_checks,site_checks

def candidate(href,base,allowed):
    if not href or href.startswith('#'): return None
    try: url=normalize_url(urljoin(base,href))
    except FetchError: return None
    p=urlsplit(url)
    if p.hostname not in allowed or p.query or re.search(r'\.(pdf|zip|jpg|jpeg|png|webp|svg|mp4|mp3|css|js|woff2?|xml|txt)$',p.path,re.I): return None
    return url

def run_scan(root_url,cfg,progress=None,fetcher=None):
    root_url=normalize_url(root_url,root_only=True)
    fetcher=fetcher or SafeFetcher(cfg,root_url)
    checks=[]; evidence=[]; unknowns=[]; pages=[]; errors=[]
    robots=RobotFileParser()
    def result():
        return {**calculate_index(checks,cfg['minimum_coverage']),'checks':checks,'evidence':evidence,
          'unknowns':list(dict.fromkeys(unknowns+['Фактическая видимость в ответах ИИ не проверялась.','Сравнение конкурентов пропущено: поисковый источник не подключён.','Охват ограничен полученными страницами, это не полный обход сайта.','Внутренние процессы, экономия, позиции и подтверждённая индексация неизвестны.'])),
          'pages_processed':len(pages),'pages':pages,'method_version':cfg['method_version'],
          'measured_findings':[c for c in checks if c['status'] in ('pass','fail')],
          'estimates':[],'hypotheses':[],'competitor_comparison':None,'ai_visibility_tested':False,
          'generated_at':datetime.now(timezone.utc).isoformat(),'requests':fetcher.requests,'downloaded_bytes':fetcher.bytes,
          'limits':{k:cfg[k] for k in ('max_free_pages','max_requests','max_html_bytes','max_total_bytes','max_seconds')}}
    try:
        rr=fetcher.fetch(root_url.rstrip('/')+'/robots.txt',max_bytes=131072)
        if rr.status in (401,403): raise FetchError('robots','robots.txt ограничивает доступ. Сайт не оценивался.')
        if rr.status==404: robots.parse([])
        elif rr.status==200:
            lines=rr.text.splitlines()[:10000]
            for line in lines:
                clean=line.split('#',1)[0]
                if re.match(r'\s*(allow|disallow)\s*:',clean,re.I) and any(x in clean.split(':',1)[1] for x in ('*','$')):
                    raise FetchError('robots','robots.txt содержит неподдерживаемые шаблоны. Ограниченный обход не запускался.')
            robots.parse(lines)
        else: raise FetchError('network','Не удалось надёжно прочитать robots.txt. Обход остановлен.')
        if not robots.can_fetch(USER_AGENT,root_url): raise FetchError('robots','robots.txt запрещает обход начальной страницы.')
    except FetchError as exc:
        checks.append(check('access','Доступ к данным','unknown','Техническая доступность',str(exc),'Повторить из разрешённой среды; сетевой сбой не доказывает неисправность сайта.'))
        unknowns.append(str(exc))
        return result(),'blocked' if exc.code in ('blocked','robots') else 'failed',str(exc)
    queue=deque([root_url]); seen=set()
    try:
        sr=fetcher.fetch(root_url.rstrip('/')+'/sitemap.xml',max_bytes=524288,permit=lambda u:robots.can_fetch(USER_AGENT,u))
        if sr.status==200:
            tree=ElementTree.fromstring(sr.body)
            if tree.tag.rsplit('}',1)[-1]=='urlset':
                for node in list(tree.iter())[:1500]:
                    if node.tag.rsplit('}',1)[-1]=='loc' and node.text and len(queue)<300:
                        url=candidate(node.text.strip(),root_url,fetcher.allowed_hosts)
                        if url: queue.append(url)
            else: unknowns.append('Sitemap index не разворачивался: рекурсивное чтение отключено.')
        elif sr.status!=404: unknowns.append('Sitemap не прочитан с успешным статусом.')
    except Exception:
        unknowns.append('Sitemap недоступен, ограничен или не прошёл безопасный разбор.')
    while queue and len(pages)<cfg['max_free_pages'] and len(seen)<cfg['max_requests']:
        url=queue.popleft()
        if url in seen: continue
        seen.add(url)
        if not robots.can_fetch(USER_AGENT,url): unknowns.append('Страница пропущена по robots.txt.'); continue
        try:
            page=fetcher.fetch(url,permit=lambda u:robots.can_fetch(USER_AGENT,u))
            if not 200<=page.status<300: raise FetchError('http_status',f'Одна из страниц вернула HTTP {page.status}; содержимое не оценивалось.')
            if page.headers.get('content-type','').split(';')[0].lower() not in ('text/html','application/xhtml+xml'): raise FetchError('unsupported','Не-HTML-ответ не включён в выборку.')
            parsed=PageParser(); parsed.feed(page.text)
            eid='e'+str(len(evidence)+1); values=parsed.values()
            evidence.append({'id':eid,'source_url':page.url,'fetched_at':page.fetched_at,'http_status':page.status,'fact_type':'html_structure','extracted_value':values,'safe_excerpt':json.dumps(values,ensure_ascii=False)[:3000],'extraction_rule':'html-parser/1.0','content_hash':hashlib.sha256(page.body).hexdigest(),'limits_note':'Наблюдение структуры, не подтверждение истинности заявлений сайта.'})
            pages.append(page.url); checks.extend(page_checks(page,parsed,eid,len(pages)))
            if progress is not None and progress(len(pages)) is False: raise FetchError('cancelled','Задание удалено владельцем. Чтение остановлено.')
            for href in parsed.links:
                url2=candidate(href,page.url,fetcher.allowed_hosts)
                if url2 and url2 not in seen and len(queue)<300: queue.append(url2)
        except FetchError as exc:
            errors.append(exc.code); unknowns.append(str(exc))
            checks.append(check('unknown:'+str(len(seen)),'Страница не оценена','unknown','Техническая доступность',str(exc),'Не считать неизвестность дефектом.'))
            if exc.code in ('timeout','request_limit','size_limit','cancelled'): break
    if pages: checks.extend(site_checks(evidence))
    status='partial' if pages and errors else 'completed' if pages else 'failed'
    return result(),status,'Часть источников пропущена; смотрите ограничения.' if errors else None
