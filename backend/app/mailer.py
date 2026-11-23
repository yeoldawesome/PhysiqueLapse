"""Sending email (password-reset links) through an SMTP server set in the environment.

Works with any SMTP service: Resend (smtp.resend.com, user "resend", an API key as password),
Gmail with an app password, a mail provider of your own. Without SMTP_HOST nothing is sent and
the admin page shows reset links to pass on by hand.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from . import config

log = logging.getLogger(__name__)


def configured() -> bool:
    return bool(config.SMTP_HOST and config.MAIL_FROM)


def send(to: str, subject: str, text: str) -> None:
    if not configured():
        raise RuntimeError("email is not set up (SMTP_HOST / MAIL_FROM)")
    msg = EmailMessage()
    msg["From"] = config.MAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    ctx = ssl.create_default_context()
    if config.SMTP_TLS == "ssl":
        server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
    with server:
        if config.SMTP_TLS == "starttls":
            server.starttls(context=ctx)
        if config.SMTP_USER:
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(msg)
    log.info("sent %r to %s", subject, to)


def reset_link(token: str, base: str) -> str:
    return f"{(config.PUBLIC_URL or base).rstrip('/')}/reset?token={token}"


def send_reset(to: str, name: str, link: str) -> None:
    send(
        to,
        "Reset your PhysiqueLapse password",
        f"Hi{(' ' + name) if name else ''},\n\n"
        f"Someone (hopefully you) asked to reset the password of your PhysiqueLapse account.\n"
        f"Open this link within an hour to choose a new one:\n\n{link}\n\n"
        f"If you didn't ask for this, ignore this email; your password stays the same.\n",
    )
