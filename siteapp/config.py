from __future__ import annotations
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = json.loads((ROOT / 'site.config.json').read_text(encoding='utf-8'))


def settings(overrides: dict | None = None) -> dict:
    cfg = dict(SITE)
    cfg.update({
        'data_dir': os.environ.get('DATA_DIR', str(ROOT / 'instance')),
        'base_url': os.environ.get('BASE_URL', 'http://127.0.0.1:8000').rstrip('/'),
        'scan_enabled': os.environ.get('SCAN_ENABLED') == 'true',
        'public_scanner': os.environ.get('PUBLIC_SCANNER') == 'true',
        'egress_confirmed': os.environ.get('EGRESS_CONFIRMED') == 'true',
        'smtp_host': os.environ.get('SMTP_HOST', ''),
        'smtp_port': int(os.environ.get('SMTP_PORT', '465')),
        'smtp_user': os.environ.get('SMTP_USER', ''),
        'smtp_password': os.environ.get('SMTP_PASSWORD', ''),
        'mail_from': os.environ.get('MAIL_FROM', ''),
        'lead_recipient': os.environ.get('LEAD_RECIPIENT', ''),
        'contact_enabled': os.environ.get('CONTACT_ENABLED') == 'true',
        'preview': os.environ.get('PUBLIC_RELEASE') != 'true',
        'testing': False,
        'static_export': False,
    })
    if overrides:
        cfg.update(overrides)
    base = urlsplit(cfg['base_url'])
    if base.scheme not in ('http', 'https') or not base.hostname or base.username or base.query or base.fragment:
        raise ValueError('BASE_URL должен быть HTTP(S)-адресом без логина, параметров и фрагмента.')
    if not cfg['preview']:
        if not cfg['brand_approved'] or not cfg['legal_approved'] or not cfg['contact_email']:
            raise ValueError('Публичный релиз заблокирован: подтвердите имя, документы и контакты.')
        if base.scheme != 'https':
            raise ValueError('Для публичного релиза требуется HTTPS.')
    if cfg['public_scanner'] and not cfg['egress_confirmed']:
        raise ValueError('Публичный сканер запрещён без подтверждённого ограничения исходящей сети.')
    data_dir = Path(cfg['data_dir'])
    data_dir.mkdir(parents=True, exist_ok=True)
    secret = os.environ.get('SESSION_SECRET', '')
    if not secret:
        secret_path = data_dir / '.session-secret'
        try:
            fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            secret = secret_path.read_text(encoding='utf-8')
        else:
            secret = secrets.token_urlsafe(48)
            with os.fdopen(fd, 'w') as stream:
                stream.write(secret)
    if len(secret) < 32:
        raise ValueError('SESSION_SECRET должен содержать не менее 32 символов.')
    cfg['session_secret'] = secret
    cfg['smtp_ready'] = bool(cfg['smtp_host'] and cfg['mail_from'] and cfg['smtp_user'] and cfg['smtp_password'])
    cfg['payments_ready'] = False
    cfg['search_ready'] = False
    cfg['llm_ready'] = False
    cfg['trusted_hosts'] = [base.hostname, '127.0.0.1', 'localhost', 'testserver'] if cfg['preview'] else [base.hostname]
    return cfg
