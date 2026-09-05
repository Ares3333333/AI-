from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import re
import time
from urllib.parse import urlsplit, urlunsplit, urljoin
import dns.exception
import dns.resolver
import urllib3

USER_AGENT = 'PublicSignalsPreview/1.0'


class FetchError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback or ip.is_link_local:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo or ip in ipaddress.ip_network('64:ff9b::/96')):
        return False
    return True


def normalize_url(value: str, root_only: bool = False) -> str:
    if not isinstance(value,str) or len(value)>2048:
        raise FetchError('invalid_url','Некорректный адрес.')
    value=value.strip()
    if not value or re.search(r'[\x00-\x20\x7f\\]', value):
        raise FetchError('invalid_url','В адресе недопустимы пробелы, управляющие символы и обратная косая черта.')
    if '://' not in value:
        value='https://'+value
    try:
        p=urlsplit(value)
        port=p.port
        host=(p.hostname or '').rstrip('.').lower()
    except ValueError as exc:
        raise FetchError('invalid_url','Не удалось разобрать адрес.') from exc
    if p.scheme not in ('http','https') or p.username is not None or p.password is not None or not host or '%' in host:
        raise FetchError('blocked','Разрешены только публичные HTTP/HTTPS-адреса без логина и пароля.')
    if port is not None and port != (443 if p.scheme=='https' else 80):
        raise FetchError('blocked','Нестандартные порты не разрешены.')
    try:
        literal=ipaddress.ip_address(host)
    except ValueError:
        try:
            host=host.encode('idna').decode('ascii')
        except UnicodeError as exc:
            raise FetchError('invalid_url','Некорректное доменное имя.') from exc
        if len(host)>253 or '.' not in host or not re.fullmatch(r'[a-z0-9.-]+',host) or any(not s or len(s)>63 or s.startswith('-') or s.endswith('-') for s in host.split('.')):
            raise FetchError('blocked','Укажите публичный домен.')
        if host.endswith(('.local','.localhost','.internal','.home','.lan','.test','.invalid')):
            raise FetchError('blocked','Внутренние домены не разрешены.')
    else:
        if not public_ip(str(literal)):
            raise FetchError('blocked','Внутренние, служебные и зарезервированные IP-адреса запрещены.')
        host='['+str(literal)+']' if literal.version==6 else str(literal)
    if root_only and (p.path not in ('','/') or p.query or p.fragment):
        raise FetchError('invalid_url','Введите только домен, без пути, параметров и фрагмента.')
    return urlunsplit((p.scheme,host,p.path or '/',p.query,''))


@dataclass
class FetchResult:
    url: str
    status: int
    headers: dict
    body: bytes
    fetched_at: str

    @property
    def text(self):
        encoding='utf-8'
        match=re.search(r'charset=["\']?([\w-]+)',self.headers.get('content-type',''),re.I)
        if match and match.group(1).lower() in ('utf-8','utf8','windows-1251','cp1251','iso-8859-1'):
            encoding=match.group(1)
        return self.body.decode(encoding,errors='replace')


class SafeFetcher:
    """Bounded HTTP client. Infrastructure egress restrictions are also required."""
    def __init__(self, config, root_url):
        self.cfg=config
        host=urlsplit(root_url).hostname
        self.allowed_hosts={host, host[4:] if host.startswith('www.') else 'www.'+host}
        self.deadline=time.monotonic()+config['max_seconds']
        self.requests=0
        self.bytes=0
        self.resolver=dns.resolver.Resolver()
        self.resolver.timeout=2

    def _remaining(self):
        left=self.deadline-time.monotonic()
        if left<=0:
            raise FetchError('timeout','Исчерпан общий бюджет времени задания.')
        return left

    def _resolve(self, host):
        try:
            ipaddress.ip_address(host)
            addresses=[host]
        except ValueError:
            addresses=[]
            try:
                for kind in ('A','AAAA'):
                    try:
                        answer=self.resolver.resolve(host,kind,lifetime=min(3,self._remaining()),search=False)
                        addresses.extend(str(item) for item in answer)
                    except dns.resolver.NoAnswer:
                        continue
                if not addresses:
                    raise FetchError('network','DNS не вернул публичный адрес. Проверка не выполнена.')
            except (dns.exception.DNSException, OSError) as exc:
                raise FetchError('network','DNS недоступен или имя не разрешается из этой среды. Состояние сайта неизвестно.') from exc
        if not all(public_ip(ip) for ip in addresses):
            raise FetchError('blocked','DNS указывает на запрещённый или смешанный публичный/внутренний набор адресов.')
        return addresses[0]

    def fetch(self, url, max_bytes=None, permit=None):
        cap=min(max_bytes or self.cfg['max_html_bytes'],self.cfg['max_html_bytes'])
        for hop in range(self.cfg['max_redirects']+1):
            url=normalize_url(url)
            p=urlsplit(url)
            if p.hostname not in self.allowed_hosts:
                raise FetchError('blocked','Переход за пределы исходного домена не разрешён.')
            if permit is not None and not permit(url):
                raise FetchError('robots','robots.txt не разрешает чтение этого адреса.')
            if self.requests>=self.cfg['max_requests']:
                raise FetchError('request_limit','Лимит сетевых запросов исчерпан.')
            if self.bytes>=self.cfg['max_total_bytes']:
                raise FetchError('size_limit','Лимит объёма загрузки исчерпан.')
            ip=self._resolve(p.hostname)
            timeout=urllib3.Timeout(connect=min(3,self._remaining()),read=min(self.cfg['request_seconds'],self._remaining()),total=min(self.cfg['request_seconds'],self._remaining()))
            args={'port':443 if p.scheme=='https' else 80,'timeout':timeout,'maxsize':1,'block':True}
            if p.scheme=='https':
                pool=urllib3.HTTPSConnectionPool(ip,server_hostname=p.hostname,assert_hostname=p.hostname,cert_reqs='CERT_REQUIRED',**args)
            else:
                pool=urllib3.HTTPConnectionPool(ip,**args)
            response=None
            self.requests+=1
            try:
                path=p.path or '/'
                if p.query: path+='?'+p.query
                response=pool.urlopen('GET',path,headers={'Host':p.netloc,'User-Agent':USER_AGENT,'Accept':'text/html,application/xhtml+xml,text/plain,application/xml,text/xml;q=0.9','Accept-Encoding':'identity'},redirect=False,retries=False,preload_content=False,assert_same_host=False)
                status=response.status
                headers={k.lower():v for k,v in response.headers.items()}
                if status in (301,302,303,307,308):
                    if hop==self.cfg['max_redirects'] or not headers.get('location'):
                        raise FetchError('redirect_limit','Слишком много переадресаций или отсутствует адрес перехода.')
                    url=urljoin(url,headers['location'])
                    continue
                if headers.get('content-encoding','identity').lower() not in ('','identity'):
                    raise FetchError('unsupported','Сжатый ответ пропущен: экспресс-режим читает только ограниченный несжатый ответ.')
                content_type=headers.get('content-type','').split(';')[0].strip().lower()
                if content_type not in ('text/html','application/xhtml+xml','text/plain','application/xml','text/xml',''):
                    raise FetchError('unsupported','Ответ не относится к поддерживаемому HTML/XML/тексту.')
                length=headers.get('content-length','')
                if length.isdigit() and int(length)>cap:
                    raise FetchError('size_limit','Ответ превышает разрешённый размер.')
                parts=[]; size=0
                while True:
                    self._remaining()
                    data=response.read1(65536,decode_content=False)
                    if not data: break
                    size+=len(data); self.bytes+=len(data)
                    if size>cap or self.bytes>self.cfg['max_total_bytes']:
                        raise FetchError('size_limit','Достигнут лимит размера ответа или задания.')
                    parts.append(data)
                return FetchResult(url,status,headers,b''.join(parts),datetime.now(timezone.utc).isoformat())
            except FetchError:
                raise
            except (urllib3.exceptions.HTTPError,OSError) as exc:
                raise FetchError('network','Сетевой запрос не завершён. Это не доказывает неисправность сайта.') from exc
            finally:
                if response is not None: response.close()
                pool.close()
        raise FetchError('redirect_limit','Лимит переадресаций исчерпан.')
