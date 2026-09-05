from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import secrets
import sqlite3
import time

STATUSES = {'queued':'В очереди','running':'Проверка выполняется','completed':'Проверка завершена','partial':'Частичный результат','blocked':'Доступ ограничен','failed':'Проверка не выполнена','budget_exceeded':'Лимит исчерпан'}

class LimitError(Exception):
    pass

class ConflictError(Exception):
    pass

class Store:
    def __init__(self, cfg):
        self.cfg = cfg
        self.path = Path(cfg['data_dir']) / 'private.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, creator TEXT NOT NULL,
              domain TEXT NOT NULL, plan TEXT NOT NULL DEFAULT 'free', status TEXT NOT NULL,
              created REAL NOT NULL, started REAL, expires REAL NOT NULL,
              pages_processed INTEGER NOT NULL DEFAULT 0, payload TEXT, failure_reason TEXT,
              idem TEXT NOT NULL, UNIQUE(creator, idem));
            CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner, created);
            CREATE TABLE IF NOT EXISTS counters (bucket TEXT PRIMARY KEY,value INTEGER NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY,email TEXT NOT NULL UNIQUE,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS tokens (hash TEXT PRIMARY KEY,owner TEXT NOT NULL,account_id TEXT NOT NULL,report_id TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS costs (id TEXT PRIMARY KEY,day TEXT NOT NULL,month TEXT NOT NULL,reserved INTEGER NOT NULL,actual INTEGER);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY,name TEXT NOT NULL,created REAL NOT NULL);
            ''')
        self.cleanup()

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA secure_delete=ON')
        c.execute('PRAGMA busy_timeout=10000')
        try:
            yield c
        finally:
            c.close()

    @contextmanager
    def transaction(self):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            try:
                yield c
                c.execute('COMMIT')
            except Exception:
                c.execute('ROLLBACK')
                raise

    @staticmethod
    def unpack(row):
        if row is None:
            return None
        value = dict(row)
        payload = value.pop('payload', None)
        value['report'] = json.loads(payload) if payload else None
        value['status_label'] = STATUSES.get(value['status'], value['status'])
        return value

    @staticmethod
    def increment(c, key, ceiling, expires):
        row = c.execute('SELECT value FROM counters WHERE bucket=?', (key,)).fetchone()
        if ceiling <= 0 or row and row['value'] >= ceiling:
            raise LimitError('Лимит запросов исчерпан. Новая работа не создана.')
        c.execute('INSERT INTO counters VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET value=value+1', (key, expires))

    @staticmethod
    def event(c, name, now):
        c.execute('INSERT INTO events(name,created) VALUES(?,?)', (name, now))

    def create_job(self, owner, domain, idem, ip_hash, now=None):
        now = time.time() if now is None else now
        hour, day = int(now // 3600), int(now // 86400)
        with self.transaction() as c:
            row = c.execute('SELECT * FROM jobs WHERE creator=? AND idem=? AND expires>?', (owner, idem, now)).fetchone()
            if row:
                if row['domain'] != domain:
                    raise ConflictError('Ключ повторного запроса относится к другому домену.')
                return self.unpack(row), False
            c.execute('DELETE FROM jobs WHERE creator=? AND idem=? AND expires<=?', (owner, idem, now))
            self.increment(c, f'session:{hour}:{owner}', self.cfg['session_scans_per_hour'], (hour+2)*3600)
            self.increment(c, f'ip:{day}:{ip_hash}', self.cfg['ip_scans_per_day'], (day+2)*86400)
            self.increment(c, f'global:{day}', self.cfg['total_scans_per_day'], (day+2)*86400)
            count = c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running') AND expires>?", (now,)).fetchone()[0]
            if count >= 10:
                raise LimitError('Очередь заполнена. Повторите позже.')
            job_id = secrets.token_urlsafe(18)
            ttl = self.cfg['saved_retention_days']*86400 if owner.startswith('acct_') else self.cfg['anonymous_retention_hours']*3600
            c.execute('INSERT INTO jobs(id,owner,creator,domain,status,created,expires,idem) VALUES(?,?,?,?,?,?,?,?)',
                      (job_id, owner, owner, domain, 'queued', now, now+ttl, idem))
            self.event(c, 'scan_started', now)
            return self.unpack(c.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()), True

    def get_job(self, job_id, owner):
        with self.db() as c:
            return self.unpack(c.execute('SELECT * FROM jobs WHERE id=? AND owner=? AND expires>?', (job_id, owner, time.time())).fetchone())

    def list_jobs(self, owner):
        with self.db() as c:
            return [self.unpack(r) for r in c.execute('SELECT * FROM jobs WHERE owner=? AND expires>? ORDER BY created DESC LIMIT 100', (owner, time.time())).fetchall()]

    def claim_job(self):
        with self.transaction() as c:
            if c.execute("SELECT 1 FROM jobs WHERE status='running' AND expires>? LIMIT 1", (time.time(),)).fetchone():
                return None
            row = c.execute("SELECT * FROM jobs WHERE status='queued' AND expires>? ORDER BY created LIMIT 1", (time.time(),)).fetchone()
            if row:
                c.execute("UPDATE jobs SET status='running',started=? WHERE id=?", (time.time(), row['id']))
            return self.unpack(row)

    def progress(self, job_id, pages):
        with self.db() as c:
            cur = c.execute("UPDATE jobs SET pages_processed=? WHERE id=? AND status='running'", (pages, job_id))
            return bool(cur.rowcount)

    def finish(self, job_id, report, status, reason=None):
        if status not in STATUSES or status in ('queued','running'):
            raise ValueError('Invalid final state')
        with self.transaction() as c:
            cur = c.execute('UPDATE jobs SET status=?,payload=?,failure_reason=?,pages_processed=? WHERE id=?',
                (status, json.dumps(report, ensure_ascii=False), reason, report.get('pages_processed',0), job_id))
            if cur.rowcount:
                self.event(c, 'scan_completed' if status=='completed' else 'scan_partial' if status=='partial' else 'scan_failed', time.time())

    def delete_job(self, job_id, owner):
        with self.transaction() as c:
            if not c.execute('SELECT id FROM jobs WHERE id=? AND owner=?', (job_id, owner)).fetchone():
                return False
            c.execute('DELETE FROM tokens WHERE report_id=?', (job_id,))
            c.execute('DELETE FROM jobs WHERE id=? AND owner=?', (job_id, owner))
            self.event(c, 'report_deleted', time.time())
        with self.db() as c:
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return True

    def issue_token(self, owner, report_id, email, now=None):
        now = time.time() if now is None else now
        raw = secrets.token_urlsafe(32)
        with self.transaction() as c:
            if not c.execute('SELECT id FROM jobs WHERE id=? AND owner=? AND expires>?', (report_id, owner, now)).fetchone():
                return None
            hour, day = int(now//3600), int(now//86400)
            self.increment(c, f'mail-owner:{hour}:{owner}', 3, (hour+2)*3600)
            self.increment(c, f'mail-global:{day}', 20, (day+2)*86400)
            account = c.execute('SELECT id FROM accounts WHERE email=?', (email,)).fetchone()
            account_id = account['id'] if account else 'acct_'+secrets.token_urlsafe(18)
            if not account:
                c.execute('INSERT INTO accounts VALUES(?,?,?)', (account_id, email, now))
            c.execute('INSERT INTO tokens VALUES(?,?,?,?,?)', (hashlib.sha256(raw.encode()).hexdigest(), owner, account_id, report_id, now+self.cfg['magic_link_ttl_minutes']*60))
            self.event(c, 'access_requested', now)
        return raw

    def revoke_token(self, raw):
        with self.db() as c:
            c.execute('DELETE FROM tokens WHERE hash=?', (hashlib.sha256(raw.encode()).hexdigest(),))

    def consume_token(self, raw, now=None):
        now = time.time() if now is None else now
        with self.transaction() as c:
            token = c.execute('SELECT * FROM tokens WHERE hash=? AND expires>?', (hashlib.sha256(raw.encode()).hexdigest(), now)).fetchone()
            if not token:
                return None
            exists = c.execute('SELECT id FROM jobs WHERE id=? AND owner=? AND expires>?', (token['report_id'], token['owner'], now)).fetchone()
            c.execute('DELETE FROM tokens WHERE hash=?', (token['hash'],))
            if not exists:
                return None
            c.execute('UPDATE jobs SET owner=?,expires=? WHERE id=? AND owner=?', (token['account_id'], now+self.cfg['saved_retention_days']*86400, token['report_id'], token['owner']))
            c.execute('DELETE FROM tokens WHERE report_id=?', (token['report_id'],))
            self.event(c, 'access_verified', now)
            return token['account_id']

    def reserve_cost(self, reservation_id, amount, now=None):
        if not isinstance(amount,int) or isinstance(amount,bool) or amount<0:
            raise ValueError('Стоимость должна быть целым неотрицательным числом копеек.')
        now = time.time() if now is None else now
        day = datetime.fromtimestamp(now,timezone.utc).strftime('%Y-%m-%d')
        with self.transaction() as c:
            old = c.execute('SELECT reserved FROM costs WHERE id=?', (reservation_id,)).fetchone()
            if old:
                if old['reserved']!=amount:
                    raise ConflictError('Этот резерв уже создан с другой суммой.')
                return
            daily = c.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM costs WHERE day=?', (day,)).fetchone()[0]
            monthly = c.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM costs WHERE month=?', (day[:7],)).fetchone()[0]
            if amount>self.cfg['paid_api_report_limit_kopecks'] or daily+amount>self.cfg['paid_api_daily_limit_kopecks'] or monthly+amount>self.cfg['paid_api_monthly_limit_kopecks']:
                raise LimitError('Денежный потолок исчерпан. Платный вызов запрещён.')
            c.execute('INSERT INTO costs VALUES(?,?,?,?,NULL)', (reservation_id, day, day[:7], amount))

    def settle_cost(self, reservation_id, amount):
        if not isinstance(amount,int) or isinstance(amount,bool) or amount<0:
            raise ValueError('Некорректная сумма')
        with self.transaction() as c:
            row = c.execute('SELECT * FROM costs WHERE id=?', (reservation_id,)).fetchone()
            if not row or amount>row['reserved']:
                raise LimitError('Стоимость превышает резерв.')
            if row['actual'] is not None and row['actual']!=amount:
                raise ConflictError('Резерв уже закрыт.')
            c.execute('UPDATE costs SET actual=? WHERE id=?', (amount, reservation_id))

    def cleanup(self, now=None):
        now = time.time() if now is None else now
        with self.transaction() as c:
            c.execute('DELETE FROM tokens WHERE expires<=? OR report_id IN (SELECT id FROM jobs WHERE expires<=?)', (now, now))
            c.execute('DELETE FROM jobs WHERE expires<=?', (now,))
            c.execute('DELETE FROM counters WHERE expires<=?', (now,))
            c.execute('DELETE FROM events WHERE created<?', (now-30*86400,))
            c.execute('DELETE FROM accounts WHERE id NOT IN (SELECT owner FROM jobs) AND id NOT IN (SELECT account_id FROM tokens) AND created<?', (now-7*86400,))
            c.execute("UPDATE jobs SET status='failed',failure_reason='Обработчик остановлен или истекло время задания.' WHERE status='running' AND started<?", (now-self.cfg['max_seconds']-15,))
        with self.db() as c:
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
