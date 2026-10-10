from datetime import timezone
from email.message import EmailMessage
from email import policy
from email.utils import format_datetime
import re
import smtplib
import ssl
from btc_jev.config import require_secrets
from btc_jev.domain import DeliveryResult
from btc_jev.util import time_limit


def header(value):
    if '\r' in value or '\n' in value: raise ValueError('invalid_mail_header')
    return value


def addresses(settings):
    values=[settings.gmail_address.get_secret_value(),settings.recipient_email.get_secret_value()]
    for value in values:
        header(value)
        if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+',value): raise ValueError('invalid_mail_address')
    return values


def build_mime(rendered,settings,message_id,created_at):
    sender,recipient=addresses(settings)
    message=EmailMessage(policy=policy.SMTP)
    message['Subject']=header(rendered.subject)
    message['From']=sender; message['To']=recipient
    message['Message-ID']=header(message_id)
    message['Date']=format_datetime(created_at.astimezone(timezone.utc))
    message.set_content(rendered.text)
    message.add_alternative(rendered.html,subtype='html')
    return message.as_bytes()


def send_mail(mime_bytes,settings,smtp_factory=smtplib.SMTP_SSL):
    require_secrets(settings,'mail')
    sender,recipient=addresses(settings)
    smtp=None; data_started=False; accepted=False
    def result(state,reason): return DeliveryResult(status=state,reason=reason)
    def negative(code):
        return 'retryable' if 400<=code<500 else 'failed' if 500<=code<600 else 'unknown'
    try:
        with time_limit(120):
            smtp=smtp_factory('smtp.gmail.com',465,timeout=30,context=ssl.create_default_context())
            code,_=smtp.ehlo()
            if code!=250: return result(negative(code),'smtp_greeting')
            smtp.login(sender,settings.gmail_app_password.get_secret_value())
            code,_=smtp.mail(sender)
            if code!=250: return result(negative(code),'smtp_sender')
            code,_=smtp.rcpt(recipient)
            if code not in (250,251): return result(negative(code),'smtp_recipient')
            data_started=True
            code,_=smtp.data(mime_bytes)
            if code==250:
                accepted=True
                # QUIT is best effort; acceptance is already authoritative.
                try: smtp.quit()
                except (OSError,smtplib.SMTPException,TimeoutError): pass
                return result('sent','smtp_accepted')
            return result(negative(code),'smtp_data_reply')
    except smtplib.SMTPAuthenticationError:
        return result('failed','smtp_authentication')
    except smtplib.SMTPResponseException as error:
        return result(negative(error.smtp_code),'smtp_explicit_reply')
    except (OSError,smtplib.SMTPException,TimeoutError,ValueError,TypeError):
        return result('sent' if accepted else 'unknown' if data_started else 'retryable','smtp_connection')
    finally:
        if smtp is not None:
            try: smtp.close()
            except (OSError,smtplib.SMTPException): pass
