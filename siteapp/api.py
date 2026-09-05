import hashlib,hmac,json,re,secrets
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from .network import normalize_url,FetchError
from .storage import LimitError,ConflictError
from .providers import ProviderUnavailable

class ApiError(Exception):
    def __init__(self,status,code,message):
        self.status=status;self.code=code;self.message=message

def report_view(job,owner):
    data=job.get('report')
    if not data or owner.startswith('acct_'): return data
    view=dict(data)
    view['checks']=[c for c in data.get('checks',[]) if c['status'] in ('pass','fail')][:3] or data.get('checks',[])[:3]
    ids={i for c in view['checks'] for i in c.get('evidence_ids',[])}
    view['evidence']=[e for e in data.get('evidence',[]) if e['id'] in ids]
    view['measured_findings']=view['checks']
    return view

def install_api(app,cfg,store,owner,available):
    @app.exception_handler(ApiError)
    async def errors(request,exc):
        return JSONResponse({'code':exc.code,'message':exc.message},status_code=exc.status)
    async def body(request):
        if request.headers.get('content-type','').split(';')[0].lower()!='application/json': raise ApiError(415,'content_type','Ожидается JSON-запрос.')
        expected=request.session.get('csrf',''); supplied=request.headers.get('x-csrf-token','')
        if not expected or not supplied or not hmac.compare_digest(expected,supplied): raise ApiError(403,'csrf','Сессия формы истекла. Обновите страницу.')
        origin=request.headers.get('origin')
        if origin and origin.rstrip('/')!=cfg['base_url']: raise ApiError(403,'origin','Источник запроса не разрешён.')
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>16384: raise ApiError(413,'body_limit','Запрос превышает разрешённый размер.')
        try: data=json.loads(raw or b'{}')
        except (ValueError,UnicodeError): raise ApiError(400,'json','Не удалось разобрать JSON.')
        if not isinstance(data,dict): raise ApiError(400,'json','Ожидается объект JSON.')
        return data
    def owned(request,job_id):
        job=store.get_job(job_id,owner(request))
        if not job: raise ApiError(404,'not_found','Ресурс не найден или недоступен.')
        return job
    @app.post('/api/scans')
    async def create_scan(request:Request):
        data=await body(request)
        try: domain=normalize_url(data.get('domain',''),root_only=True)
        except FetchError as exc: raise ApiError(422,exc.code,str(exc))
        if data.get('plan','free')!='free': raise ApiError(503,'plan_disabled','Расширенный платный режим не подключён.')
        if not available(request): raise ApiError(503,'scanner_disabled','Сканер отключён в этой среде. Задание не создавалось.')
        key=data.get('idempotency_key','')
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',key): raise ApiError(422,'key','Нужен корректный ключ повторного запроса.')
        ip=request.client.host if request.client else 'unknown'
        digest=hmac.new(cfg['session_secret'].encode(),ip.encode(),hashlib.sha256).hexdigest()
        try: job,created=store.create_job(owner(request),domain,key,digest)
        except LimitError as exc: raise ApiError(429,'rate_limit',str(exc))
        except ConflictError as exc: raise ApiError(409,'conflict',str(exc))
        return JSONResponse({'id':job['id'],'status':job['status'],'status_label':job['status_label'],'reused':not created},status_code=202 if created else 200)
    @app.get('/api/scans/{job_id}')
    def state(request:Request,job_id:str):
        job=owned(request,job_id)
        return {k:job[k] for k in ('id','status','status_label','pages_processed','failure_reason')}
    @app.get('/api/reports/{job_id}')
    def report(request:Request,job_id:str):
        job=owned(request,job_id)
        return {'id':job['id'],'status':job['status'],'expires_at':job['expires'],'report':report_view(job,owner(request))}
    @app.delete('/api/reports/{job_id}')
    async def delete(request:Request,job_id:str):
        await body(request)
        if not store.delete_job(job_id,owner(request)): raise ApiError(404,'not_found','Ресурс не найден или недоступен.')
        return {'deleted':True}
    @app.post('/api/access/request')
    async def request_access(request:Request):
        data=await body(request); email=data.get('email','')
        if not isinstance(email,str) or len(email)>254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',email): raise ApiError(422,'email','Укажите корректный email.')
        if not cfg['smtp_ready']: raise ApiError(503,'mail_disabled','Почта не подключена. Ссылка не отправлялась.')
        try: token=store.issue_token(owner(request),str(data.get('report_id',''))[:100],email.strip().lower())
        except LimitError as exc: raise ApiError(429,'rate_limit',str(exc))
        if not token: raise ApiError(404,'not_found','Ресурс не найден или недоступен.')
        try: await run_in_threadpool(app.state.mailer.send_access,email.strip(),token)
        except ProviderUnavailable as exc:
            store.revoke_token(token); raise ApiError(503,'mail_failed',str(exc))
        return {'accepted':True,'message':'Письмо передано почтовому серверу.'}
    @app.post('/api/access/verify')
    async def verify_access(request:Request):
        data=await body(request); token=data.get('token','')
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,64}',token): raise ApiError(401,'invalid_link','Ссылка недействительна, истекла или уже использована.')
        account_id=store.consume_token(token)
        if not account_id: raise ApiError(401,'invalid_link','Ссылка недействительна, истекла или уже использована.')
        request.session.clear();request.session['owner']=account_id;request.session['csrf']=secrets.token_urlsafe(32)
        return {'verified':True}
    @app.post('/api/orders')
    async def order(request:Request):
        await body(request)
        raise ApiError(503,'payments_disabled','Оплата не подключена. Заказ и списание не создавались.')
    @app.post('/api/payments/webhook')
    async def webhook(request:Request):
        raise ApiError(503,'payments_disabled','Платёжный провайдер не подключён. Доступ не предоставлен.')
    @app.post('/api/leads')
    async def leads(request:Request):
        await body(request)
        raise ApiError(503,'leads_disabled','Получатель заявок не подтверждён. Можно скачать бриф себе; отправка не выполнялась.')
