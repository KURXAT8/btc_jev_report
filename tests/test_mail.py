from datetime import datetime,timezone
import smtplib
import pytest
from btc_jev.domain import Settings,RenderedMail


class FakeSMTP:
    instances=[]
    code=250
    phase=None
    def __init__(self,*args,**kwargs):
        self.data_bytes=[]; self.instances.append(self)
        if self.phase=='connect': raise TimeoutError()
    def ehlo(self): return 250,b'ok'
    def login(self,*args):
        if self.phase=='auth': raise smtplib.SMTPAuthenticationError(535,b'secret provider body')
    def mail(self,*args): return 250,b'ok'
    def rcpt(self,*args): return 250,b'ok'
    def data(self,value):
        self.data_bytes.append(value)
        if self.phase=='data': raise TimeoutError()
        return self.code,b'answer'
    def quit(self):
        if self.phase=='quit': raise TimeoutError()
    def close(self): pass


def settings(tmp_path):
    return Settings(data_dir=tmp_path,gmail_address='sender@example.org',gmail_app_password='dummy',recipient_email='reader@example.org')


@pytest.mark.parametrize('phase,code,want',[(None,250,'sent'),('quit',250,'sent'),('data',250,'unknown'),
    ('connect',250,'retryable'),('auth',250,'failed'),(None,450,'retryable'),(None,550,'failed'),(None,999,'unknown')])
def test_smtp_protocol_outcomes_and_identical_mime(tmp_path,phase,code,want):
    from btc_jev.mail import send_mail,build_mime
    s=settings(tmp_path); payload=build_mime(RenderedMail(subject='中文主旨',text='文字',html='<p>文字</p>'),s,'<test@example.org>',datetime.now(timezone.utc))
    factory=type('Scenario',(FakeSMTP,),dict(phase=phase,code=code))
    assert send_mail(payload,s,factory).status==want
    assert send_mail(payload,s,factory).status==want
    if phase not in ('connect','auth'):
        assert factory.instances[-1].data_bytes==[payload]


def test_mime_headers_injection_rejected(tmp_path):
    from btc_jev.mail import build_mime
    from email import policy
    from email.parser import BytesParser
    s=settings(tmp_path); now=datetime.now(timezone.utc)
    rendered=RenderedMail(subject='Report',text='text',html='<p>text</p>')
    payload=build_mime(rendered,s,'<id@example.org>',now)
    parsed=BytesParser(policy=policy.default).parsebytes(payload)
    assert parsed['Message-ID']=='<id@example.org>' and len(list(parsed.iter_parts()))==2
    with pytest.raises(ValueError): build_mime(rendered.model_copy(update={'subject':'bad\r\nBcc: x'}),s,'<id@example.org>',now)
    with pytest.raises(ValueError): build_mime(rendered,s.model_copy(update={'recipient_email':__import__('pydantic').SecretStr('a@b\nx')}),'<id@example.org>',now)
