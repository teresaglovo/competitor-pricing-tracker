"""
Email sender.
Sends the Monday morning summary email with the Google Sheet link.
Uses Gmail SMTP with the dedicated Gmail account.
"""

import html as html_lib
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _e(val) -> str:
    """HTML-escape a value for safe insertion into email HTML."""
    if val is None:
        return "—"
    return html_lib.escape(str(val))


def get_week_label() -> str:
    week_num = date.today().isocalendar()[1]
    year = date.today().year
    return f"W{week_num} {year}"


def send_weekly_email(
    sheet_url: str,
    results_summary: dict,
    recipient_email: str,
):
    """Send the Monday WBR pricing email."""

    sender_email = os.environ["GMAIL_ADDRESS"]
    sender_password = os.environ["GMAIL_APP_PASSWORD"]
    week_label = get_week_label()

    # Build HTML summary table
    rows_html = ""
    for r in results_summary.get("results", []):
        status = "✅" if r.get("df") else "⚠️"
        promo = "🟢" if r.get("promo_menu") == "YES" else "⭕"
        comments = r.get("comments") or ""
        # Truncate long comments so the table stays readable
        if len(comments) > 80:
            comments = comments[:77] + "..."
        row_bg = "#f9f9f9" if rows_html.count("<tr") % 2 == 0 else "#ffffff"
        rows_html += f"""
        <tr style="background:{row_bg};">
            <td style="padding:6px 8px;">{status} {_e(r.get('partner'))}</td>
            <td style="padding:6px 8px;">{_e(r.get('platform'))}</td>
            <td style="padding:6px 8px;">{_e(r.get('df'))}</td>
            <td style="padding:6px 8px;">{_e(r.get('sf'))}</td>
            <td style="padding:6px 8px;">{_e(r.get('mbs'))}</td>
            <td style="padding:6px 8px;">{promo} {_e(comments) if comments else '—'}</td>
        </tr>"""

    total = results_summary.get("total", 0)
    ok = results_summary.get("ok", 0)
    failed = results_summary.get("failed", 0)

    html_body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
    <h2 style="color: #FF6B35;">🛵 Competitor Pricing — {week_label}</h2>

    <p>El scraper ha completado la recopilación semanal de fees y promociones.</p>

    <table style="border-collapse: collapse; width: 100%; font-size: 13px;">
      <thead>
        <tr style="background: #FF6B35; color: white;">
          <th style="padding:8px; text-align:left;">Partner</th>
          <th style="padding:8px; text-align:left;">Plataforma</th>
          <th style="padding:8px; text-align:left;">DF</th>
          <th style="padding:8px; text-align:left;">SF</th>
          <th style="padding:8px; text-align:left;">MBS</th>
          <th style="padding:8px; text-align:left;">Promo</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>

    <br>
    <p>
      <strong>Total:</strong> {total} entradas &nbsp;|&nbsp;
      ✅ {ok} OK &nbsp;|&nbsp;
      ⚠️ {failed} fallidas
    </p>

    <p style="margin-top: 24px;">
      <a href="{sheet_url}"
         style="background:#FF6B35; color:white; padding:12px 24px;
                text-decoration:none; border-radius:6px; font-weight:bold;">
        📊 Ver Google Sheet completo
      </a>
    </p>

    <p style="color: #999; font-size: 11px; margin-top: 32px;">
      Generado automáticamente — Competitor Pricing Tracker
    </p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📊 Competitor Pricing {week_label} — Listo para el WBR"
    msg["From"] = sender_email
    msg["To"] = recipient_email

    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient_email, msg.as_string())
        print(f"[Email] Sent to {recipient_email}")
    except Exception as e:
        print(f"[Email] Error sending: {e}")
        raise
