import re
from urllib.parse import urlsplit

def calculate_index(checks, minimum=0.7):
    applicable=[c for c in checks if c.get('applicable',True) and c['status']!='not_applicable']
    known=[c for c in applicable if c['status'] in ('pass','fail')]
    coverage=len(known)/len(applicable) if applicable else 0
    score=round(100*sum(c['status']=='pass' for c in known)/len(known)) if known and coverage>=minimum else None
    return {'index':score,'coverage':round(100*coverage,1),'known_checks':len(known),'applicable_checks':len(applicable)}

def check(key,title,status,category,explanation,action,evidence_ids=None):
    return {'check_id':key,'title':title,'status':status,'applicable':status!='not_applicable',
      'category':category,'explanation':explanation,'suggested_action':action,'evidence_ids':evidence_ids or [],
      'rule_version':'1.0','confidence_note':'Структурное наблюдение в ограниченной выборке, не оценка всего бизнеса.'}

def page_checks(page,parsed,eid,number):
    yn=lambda v:'pass' if v else 'fail'
    rows=[
      ('html','Публичный HTML','pass','Техническая доступность','Получен HTML с успешным HTTP-статусом.','Это не подтверждение индексации.'),
      ('https','HTTPS',yn(urlsplit(page.url).scheme=='https'),'Техническая доступность','Проверена схема конечного URL.','Проверьте корректность HTTPS.'),
      ('title','Заголовок title',yn(parsed.title.strip()),'Ясность предложения','Проверено наличие текста title.','Точность и смысл заголовка требуют содержательной проверки.'),
      ('h1','Главный заголовок',yn(any(x.strip() for x in parsed.h1)),'Ясность предложения','Проверено наличие текста H1.','Наличие заголовка не доказывает понятность предложения.'),
      ('description','Описание страницы',yn(parsed.description.strip()),'Ясность предложения','Проверено поле meta description.','Сравните описание с реальным содержанием.'),
      ('canonical','Canonical',yn(parsed.canonical.strip()),'Программное чтение','Проверено наличие canonical.','Отсутствие ссылки не всегда ошибка; проверьте управление версиями.'),
      ('noindex','Директива noindex','fail' if re.search(r'\b(noindex|none)\b',' '.join(parsed.robots+[page.headers.get('x-robots-tag','')]),re.I) else 'pass','Техническая доступность','Проверены meta и X-Robots-Tag на noindex/none.','Директива может быть намеренной и относиться к конкретному роботу.'),
      ('jsonld','Синтаксис JSON-LD',parsed.jsonld_status(),'Программное чтение','Проверен синтаксис найденных блоков. Отсутствие JSON-LD не штрафуется.','Типы, поля и истинность разметки проверяются отдельно.')]
    return [check(f'p{number}:'+k,t,s,c,e,a,[eid]) for k,t,s,c,e,a in rows]

def site_checks(evidence):
    output=[]
    for key,title,field in [('contact','Контактный путь','contact_path'),('terms','Ссылка на условия','terms_path')]:
        ids=[e['id'] for e in evidence if e['extracted_value'].get(field)]
        output.append(check(key,title,'pass' if ids else 'fail','Проверяемость информации',
            'Навигационный признак проверен только в полученном HTML.','Отдельно проверьте доставку обращения и достаточность реальных документов.',ids or [e['id'] for e in evidence]))
    output.extend([
      check('meaning','Смысл предложения','unknown','Ясность предложения','Понимание текста покупателем не проверялось.','Нужна содержательная проверка в контексте задачи.'),
      check('truth','Достоверность кейсов','unknown','Проверяемость информации','Истинность заявленных результатов не подтверждалась.','Потребуются первичные данные и разрешение на публикацию.')])
    return output
