from __future__ import annotations
from email.message import EmailMessage
import smtplib
import ssl


class ProviderUnavailable(Exception):
    pass


class Mailer:
    def __init__(self, config):
        self.cfg = config

    def send_access(self, recipient, token):
        if not self.cfg['smtp_ready']:
            raise ProviderUnavailable('Почта не подключена. Ссылка не отправлялась.')
        message = EmailMessage()
        message['Subject'] = 'Одноразовая ссылка на ваш отчёт'
        message['From'] = self.cfg['mail_from']
        message['To'] = recipient
        link = self.cfg['base_url'] + '/access/#token=' + token
        message.set_content('Вы запросили доступ к собственному отчёту.\n\n'+link+
                            '\n\nСсылка действует 15 минут. Вход подтверждается кнопкой на странице.'+
                            '\nЕсли запрос сделали не вы, не открывайте ссылку. Подписка на рассылку не создаётся.')
        try:
            with smtplib.SMTP_SSL(self.cfg['smtp_host'], self.cfg['smtp_port'], timeout=10,
                                  context=ssl.create_default_context()) as smtp:
                smtp.login(self.cfg['smtp_user'],self.cfg['smtp_password'])
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise ProviderUnavailable('Почтовый сервер не принял письмо. Повторите позже.') from exc


class DisabledPayments:
    """Fail-closed boundary. A provider-specific signed-webhook adapter is not installed."""
    ready = False

    def create_order(self, **_):
        raise ProviderUnavailable('Оплата не подключена. Заказ и списание не создавались.')

    def handle_webhook(self, *_):
        raise ProviderUnavailable('Платёжный провайдер не подключён. Доступ не предоставлен.')
