"""
Email sender.
Sends the Sunday night promo summary email with the Google Sheet link.
Uses Gmail SMTP with the dedicated Gmail account.
"""

import html as html_lib
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _e(val) -> str:
    if val is None:
        return "—"
    return html_lib.escape(str(val))


def get_week_label() -> str:
    week_num = date.today().isocalendar()[1]
    year = date.today().year
    return f"W{week_num} {year}"


def send_weekly_email(sheet_url: str, results_summary: dict, recipient_email: str):
    sender_email = os.environ["GMAIL_ADDRESS"]
    sender_password = os.environ["GMAIL_APP_PASSWORD"]
    week_label = get_week_label()

    rows_html = ""
    for r in results_summary.get("results", []):
        failed = r.get("comments") == "SCRAPE_FAILED"
        if failed:
            continue  # skip failed rows from the email table

        promo = r.get("promo_menu") or "NO"
        promo_cell = "🟢 SÍ" if promo == "YES" else "⭕ NO"
        comments = r.get("comments") or ""
        if len(comments) > 100:
            comments = comments[:97] + "..."

        row_bg = "#f9f9f9" if rows_html.count("<tr") % 2 == 0 else "#ffffff"
        rows_html += f"""
        <tr style="background:{row_bg};">
            <td style="padding:6px 10px; font-weight:500;">{_e(r.get('partner'))}</td>
            <td style="padding:6px 10px; color:#666;">{_e(r.get('platform'))}</td>
            <td style="padding:6px 10px; text-align:center;">{promo_cell}</td>
            <td style="padding:6px 10px; font-size:12px; color:#555;">{_e(comments) if comments else '—'}</td>
        </tr>"""

    # Count by platform
    all_results = results_summary.get("results", [])
    platforms = {}
    for r in all_results:
        p = r.get("platform", "?")
        ok = r.get("comments") != "SCRAPE_FAILED"
        platforms.setdefault(p, {"ok": 0, "total": 0})
        platforms[p]["total"] += 1
        if ok:
            platforms[p]["ok"] += 1

    platform_summary = " &nbsp;|&nbsp; ".join(
        f"<strong>{p}</strong>: {v['ok']}/{v['total']}"
        for p, v in sorted(platforms.items())
    )

    html_body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333; max-width: 700px;">
    <h2 style="color: #FF6B35;">🛵 Competitor Promos — {week_label}</h2>

    <p>Resumen de promociones activas esta semana en Glovo, UberEats y JustEat.</p>

    <p style="font-size:13px; color:#666;">{platform_summary}</p>

    <table style="border-collapse: collapse; width: 100%; font-size: 13px;">
      <thead>
        <tr style="background: #FF6B35; color: white;">
          <th style="padding:8px 10px; text-align:left;">Partner</th>
          <th style="padding:8px 10px; text-align:left;">Plataforma</th>
          <th style="padding:8px 10px; text-align:center;">¿Promo?</th>
          <th style="padding:8px 10px; text-align:left;">Descripción</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>

    <p style="margin-top: 24px;">
      <a href="{sheet_url}"
         style="background:#FF6B35; color:white; padding:12px 24px;
                text-decoration:none; border-radius:6px; font-weight:bold;">
        📊 Ver Google Sheet completo
      </a>
    </p>

    <p style="color: #999; font-size: 11px; margin-top: 32px;">
      Generado automáticamente cada domingo por la noche — Competitor Pricing Tracker
    </p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🛵 Competitor Promos {week_label} — Resumen semana"
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
