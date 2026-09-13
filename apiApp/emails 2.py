"""
Generic email service for Moctezuma Records.

Renders Django HTML email templates, auto-generates a plain-text version using
only the Python standard library (html.parser.HTMLParser), and sends a
multipart (text + html) message through the configured email backend.
"""

import logging
from html.parser import HTMLParser

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

_BLOCK_TAGS = frozenset(
    {"p", "div", "tr", "li", "br", "table", "ul", "ol", "hr", "h1", "h2", "h3", "h4", "h5", "h6"}
)


def _template_path(template_name):
    """Normalize 'order_created', 'order_created.html' or 'emails/order_created.html'
    to 'emails/order_created.html'."""
    name = template_name.strip().rsplit("/", 1)[-1]
    if not name.endswith(".html"):
        name = f"{name}.html"
    return f"emails/{name}"


def render_email_html(template_name, context=None):
    """Render an email template to HTML with the given context."""
    return render_to_string(_template_path(template_name), context or {})


class _PlainTextParser(HTMLParser):
    """HTMLParser subclass that produces a plain-text approximation of HTML."""

    def __init__(self):
        super().__init__()
        self._chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._chunks.append(f" ({href})")

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data):
        self._chunks.append(data)

    def result(self):
        lines = [line.strip() for line in "".join(self._chunks).splitlines()]
        collapsed = []
        previous_blank = True
        for line in lines:
            if not line:
                if not previous_blank:
                    collapsed.append("")
                previous_blank = True
            else:
                collapsed.append(line)
                previous_blank = False
        return "\n".join(collapsed).strip()


def html_to_plain_text(html):
    """Convert rendered HTML to a readable plain-text version."""
    parser = _PlainTextParser()
    parser.feed(html)
    parser.close()
    return parser.result()


def _resolve_recipients(to):
    """Accept a single email string, a single User-like object, or a list mixing
    both; return a deduplicated list of email address strings. Raise ValueError
    on invalid entries."""
    recipients = to if isinstance(to, (list, tuple)) else [to]
    resolved = []
    seen = set()
    for recipient in recipients:
        if isinstance(recipient, str):
            if not recipient.strip():
                raise ValueError(f"Invalid email recipient: {recipient!r}")
            email = recipient
        elif hasattr(recipient, "email") and recipient.email:
            email = recipient.email
        else:
            raise ValueError(f"Invalid email recipient: {recipient!r}")
        if email not in seen:
            seen.add(email)
            resolved.append(email)
    return resolved


def send_email(*, template_name, context, subject, to, from_email=None, cc=None, bcc=None, reply_to=None):
    """Render and send a multipart (text + html) transactional email.
    Raises on failure so callers own their error policy."""
    html_body = render_email_html(template_name, context)
    text_body = html_to_plain_text(html_body)
    recipients = _resolve_recipients(to)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email or settings.DEFAULT_FROM_EMAIL,
        to=recipients,
        cc=cc or [],
        bcc=bcc or [],
        reply_to=reply_to or [],
    )
    email.attach_alternative(html_body, "text/html")
    try:
        email.send(fail_silently=False)
        logger.info("Email sent: %r to %s", subject, ", ".join(recipients))
    except Exception:
        logger.warning("Failed to send email %r to %s", subject, ", ".join(recipients), exc_info=True)
        raise


def send_password_recovery_email(user, reset_link, expiry_hours=24):
    """Send the password-recovery email to a user."""
    context = {
        "user_name": user.first_name or user.username,
        "reset_link": reset_link,
        "expiry_hours": expiry_hours,
        "frontend_url": settings.FRONTEND_URL,
    }
    send_email(
        template_name="password_recovery",
        context=context,
        subject="Restablece tu contraseña — Moctezuma Records",
        to=[user],
    )


def send_user_recovery_email(user, recovery_link, expiry_hours=24):
    """Send the account-recovery email to a user."""
    context = {
        "user_name": user.first_name or user.username,
        "username": user.username,
        "recovery_link": recovery_link,
        "expiry_hours": expiry_hours,
        "frontend_url": settings.FRONTEND_URL,
    }
    send_email(
        template_name="user_recovery",
        context=context,
        subject="Recuperación de cuenta — Moctezuma Records",
        to=[user],
    )
