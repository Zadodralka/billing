from html import escape
import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from core.config import settings


async def send_email(to: str, subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_pass,
        use_tls=True,
    )


def _magic_link_html(link: str) -> str:
    """
    HTML-шаблон письма для входа через email.
    Использует таблицы вместо flexbox/grid — единственный надёжный способ
    добиться корректного отображения в почтовых клиентах (Gmail, Apple Mail, Outlook).
    Светлая тема: тёмный фон в письмах многие клиенты (особенно Outlook/корпоративная
    почта) рендерят с переопределёнными цветами текста, из-за чего текст становится
    нечитаемым - светлый фон с тёмным текстом ведёт себя предсказуемо везде.
    """
    webapp_url = settings.webapp_url

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>Вход в Unlock VPN</title>
<style>
  body, table, td, p, a {{ margin: 0; padding: 0; border: 0; }}
  body {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
  table {{ border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
  img {{ border: 0; display: block; outline: none; }}

  @media screen and (max-width: 600px) {{
    .email-wrapper {{ width: 100% !important; }}
    .email-content {{ padding: 24px 16px !important; }}
    .btn-link {{ padding: 14px 24px !important; font-size: 15px !important; }}
    .link-box {{ padding: 12px !important; font-size: 11px !important; word-break: break-all; }}
  }}
</style>
</head>
<body style="background-color: #f1f4f8; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">

<!-- Внешняя обёртка -->
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background-color: #f1f4f8; padding: 40px 16px;">
  <tr>
    <td align="center">

      <!-- Карточка письма -->
      <table class="email-wrapper" width="520" cellpadding="0" cellspacing="0" role="presentation"
             style="background-color: #ffffff; border: 1px solid #e3e8ef; border-radius: 16px; overflow: hidden;">

        <!-- Шапка с брендом -->
        <tr>
          <td style="background-color: #ffffff; border-bottom: 1px solid #e3e8ef; padding: 28px 36px;">
            <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
              <tr>
                <td>
                  <table cellpadding="0" cellspacing="0" role="presentation">
                    <tr>
                      <td style="width: 44px; height: 44px; border-radius: 12px; overflow: hidden; vertical-align: middle;">
                        <img src="{webapp_url}/static/img/logo.jpg"
                             alt="Unlock VPN"
                             width="44" height="44"
                             style="display: block; width: 44px; height: 44px; border-radius: 12px; object-fit: cover;">
                      </td>
                      <td style="padding-left: 12px; vertical-align: middle;">
                        <span style="color: #14181f; font-size: 18px; font-weight: 700; letter-spacing: -0.3px;">Unlock VPN</span>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Тело письма -->
        <tr>
          <td class="email-content" style="padding: 36px 36px 28px;">

            <p style="color: #4b5565; font-size: 15px; line-height: 1.6; margin-bottom: 28px;">
              Нажмите кнопку ниже — она откроет личный кабинет автоматически, без пароля.
            </p>

            <!-- Кнопка входа -->
            <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="margin-bottom: 24px;">
              <tr>
                <td align="center">
                  <a href="{link}" class="btn-link"
                     style="display: inline-block; background-color: #16a34a; color: #ffffff;
                            text-decoration: none; padding: 15px 40px; border-radius: 10px;
                            font-size: 16px; font-weight: 700; letter-spacing: -0.1px;">
                    Войти в кабинет →
                  </a>
                </td>
              </tr>
            </table>

            <!-- Разделитель -->
            <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="margin-bottom: 20px;">
              <tr>
                <td style="border-top: 1px solid #e3e8ef; font-size: 0;">&nbsp;</td>
              </tr>
            </table>

            <!-- Запасная ссылка -->
            <p style="color: #7c8697; font-size: 13px; margin-bottom: 8px;">
              Если кнопка не работает — скопируйте ссылку вручную:
            </p>
            <div class="link-box"
                 style="background-color: #f1f4f8; border: 1px solid #e3e8ef; border-radius: 8px;
                        padding: 10px 14px; font-size: 12px; color: #4b5565;
                        word-break: break-all; font-family: 'Courier New', monospace;">
              {link}
            </div>

          </td>
        </tr>

        <!-- Предупреждение об истечении -->
        <tr>
          <td style="background-color: #f0fdf4; border-top: 1px solid #e3e8ef; border-bottom: 1px solid #e3e8ef; padding: 14px 36px;">
            <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
              <tr>
                <td style="width: 20px; vertical-align: top; padding-top: 1px;">
                  <span style="color: #16a34a; font-size: 14px;">⏱</span>
                </td>
                <td style="padding-left: 8px;">
                  <p style="color: #4b5565; font-size: 13px; line-height: 1.5; margin: 0;">
                    Ссылка действительна <strong style="color: #14181f;">15 минут</strong>.
                    После входа вы останетесь в системе на 30 дней.
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Подвал -->
        <tr>
          <td style="padding: 22px 36px;">
            <p style="color: #97a1b0; font-size: 12px; line-height: 1.6; margin: 0;">
              Это письмо отправлено автоматически, потому что кто-то (скорее всего вы)
              запросил вход в кабинет на сайте
              <a href="{webapp_url}" style="color: #97a1b0; text-decoration: underline;">{webapp_url}</a>.
              Если вы не запрашивали вход — просто проигнорируйте это письмо.
              Ваш аккаунт в безопасности.
            </p>
          </td>
        </tr>

      </table>
      <!-- /Карточка -->

      <!-- Копирайт под карточкой -->
      <table width="520" cellpadding="0" cellspacing="0" role="presentation" style="margin-top: 20px;">
        <tr>
          <td align="center">
            <p style="color: #97a1b0; font-size: 11px; margin: 0;">
              © Unlock VPN · <a href="{webapp_url}" style="color: #97a1b0; text-decoration: none;">{webapp_url}</a>
            </p>
          </td>
        </tr>
      </table>

    </td>
  </tr>
</table>

</body>
</html>"""


async def send_magic_link(email: str, token: str):
    link = f"{settings.webapp_url}/auth/verify?token={token}"
    html = _magic_link_html(link)
    await send_email(email, "Ссылка для входа в Unlock VPN", html)


def _gift_html(code: str, plan_name: str, days: int, redeem_url: str) -> str:
    """HTML-письмо получателю подарочной подписки, в стиле _magic_link_html выше (светлая тема)."""
    webapp_url = settings.webapp_url

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>Вам подарили VPN — Unlock VPN</title>
<style>
  body, table, td, p, a {{ margin: 0; padding: 0; border: 0; }}
  body {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
  table {{ border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
  img {{ border: 0; display: block; outline: none; }}

  @media screen and (max-width: 600px) {{
    .email-wrapper {{ width: 100% !important; }}
    .email-content {{ padding: 24px 16px !important; }}
    .btn-link {{ padding: 14px 24px !important; font-size: 15px !important; }}
    .code-box {{ font-size: 20px !important; padding: 16px !important; letter-spacing: 2px !important; }}
  }}
</style>
</head>
<body style="background-color: #f1f4f8; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background-color: #f1f4f8; padding: 40px 16px;">
  <tr>
    <td align="center">

      <table class="email-wrapper" width="520" cellpadding="0" cellspacing="0" role="presentation"
             style="background-color: #ffffff; border: 1px solid #e3e8ef; border-radius: 16px; overflow: hidden;">

        <tr>
          <td style="background-color: #ffffff; border-bottom: 1px solid #e3e8ef; padding: 28px 36px;">
            <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
              <tr>
                <td>
                  <table cellpadding="0" cellspacing="0" role="presentation">
                    <tr>
                      <td style="width: 44px; height: 44px; border-radius: 12px; overflow: hidden; vertical-align: middle;">
                        <img src="{webapp_url}/static/img/logo.jpg"
                             alt="Unlock VPN"
                             width="44" height="44"
                             style="display: block; width: 44px; height: 44px; border-radius: 12px; object-fit: cover;">
                      </td>
                      <td style="padding-left: 12px; vertical-align: middle;">
                        <span style="color: #14181f; font-size: 18px; font-weight: 700; letter-spacing: -0.3px;">Unlock VPN</span>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <tr>
          <td class="email-content" style="padding: 36px 36px 28px;">

            <p style="font-size: 34px; margin-bottom: 6px;">🎁</p>
            <h1 style="color: #14181f; font-size: 20px; font-weight: 700; margin-bottom: 14px;">Вам подарили подписку!</h1>

            <p style="color: #4b5565; font-size: 15px; line-height: 1.6; margin-bottom: 24px;">
              Кто-то оформил для вас подарочную подписку
              <strong style="color: #14181f;">«{plan_name}»</strong> на <strong style="color: #14181f;">{days} дней</strong>.
              Нажмите кнопку ниже — она сразу откроет активацию подарка, входить отдельно не нужно.
            </p>

            <div class="code-box"
                 style="background-color: #f0fdf4; border: 1px dashed #16a34a; border-radius: 10px;
                        padding: 18px; text-align: center; margin-bottom: 24px;
                        font-family: 'Courier New', monospace; font-size: 24px; font-weight: 700;
                        letter-spacing: 4px; color: #15803d;">
              {code}
            </div>

            <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="margin-bottom: 8px;">
              <tr>
                <td align="center">
                  <a href="{redeem_url}" class="btn-link"
                     style="display: inline-block; background-color: #16a34a; color: #ffffff;
                            text-decoration: none; padding: 15px 40px; border-radius: 10px;
                            font-size: 16px; font-weight: 700; letter-spacing: -0.1px;">
                    Активировать подарок →
                  </a>
                </td>
              </tr>
            </table>

          </td>
        </tr>

        <tr>
          <td style="background-color: #f0fdf4; border-top: 1px solid #e3e8ef; border-bottom: 1px solid #e3e8ef; padding: 14px 36px;">
            <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
              <tr>
                <td style="width: 20px; vertical-align: top; padding-top: 1px;">
                  <span style="color: #16a34a; font-size: 14px;">💬</span>
                </td>
                <td style="padding-left: 8px;">
                  <p style="color: #4b5565; font-size: 13px; line-height: 1.5; margin: 0;">
                    Кнопка сразу авторизует вас в личном кабинете — отдельного письма для входа не будет.
                    Отсчёт срока подписки начнётся в момент активации, а не покупки.
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <tr>
          <td style="padding: 22px 36px;">
            <p style="color: #97a1b0; font-size: 12px; line-height: 1.6; margin: 0;">
              Это письмо отправлено автоматически, потому что кто-то подарил вам подписку на сервисе
              <a href="{webapp_url}" style="color: #97a1b0; text-decoration: underline;">{webapp_url}</a>.
            </p>
          </td>
        </tr>

      </table>

      <table width="520" cellpadding="0" cellspacing="0" role="presentation" style="margin-top: 20px;">
        <tr>
          <td align="center">
            <p style="color: #97a1b0; font-size: 11px; margin: 0;">
              © Unlock VPN · <a href="{webapp_url}" style="color: #97a1b0; text-decoration: none;">{webapp_url}</a>
            </p>
          </td>
        </tr>
      </table>

    </td>
  </tr>
</table>

</body>
</html>"""


async def send_gift_email(recipient_email: str, code: str, plan_name: str, days: int):
    redeem_url = f"{settings.webapp_url}/gift/redeem/{code}"
    html = _gift_html(code, plan_name, days, redeem_url)
    await send_email(recipient_email, "🎁 Вам подарили подписку Unlock VPN", html)


def _simple_notice_html(
    emoji: str, title: str, body_html: str, cta_text: str, cta_url: str,
    footer_note: str,
) -> str:
    """Общий каркас для коротких уведомлений (нулевой трафик, ответ поддержки) -
    та же карточка в стиле остальных писем, но без специфичных для конкретного
    письма блоков вроде кода подарка или запасной ссылки."""
    webapp_url = settings.webapp_url

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>{title} — Unlock VPN</title>
<style>
  body, table, td, p, a {{ margin: 0; padding: 0; border: 0; }}
  body {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
  table {{ border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
  img {{ border: 0; display: block; outline: none; }}

  @media screen and (max-width: 600px) {{
    .email-wrapper {{ width: 100% !important; }}
    .email-content {{ padding: 24px 16px !important; }}
    .btn-link {{ padding: 14px 24px !important; font-size: 15px !important; }}
  }}
</style>
</head>
<body style="background-color: #f1f4f8; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background-color: #f1f4f8; padding: 40px 16px;">
  <tr>
    <td align="center">

      <table class="email-wrapper" width="520" cellpadding="0" cellspacing="0" role="presentation"
             style="background-color: #ffffff; border: 1px solid #e3e8ef; border-radius: 16px; overflow: hidden;">

        <tr>
          <td style="background-color: #ffffff; border-bottom: 1px solid #e3e8ef; padding: 28px 36px;">
            <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
              <tr>
                <td>
                  <table cellpadding="0" cellspacing="0" role="presentation">
                    <tr>
                      <td style="width: 44px; height: 44px; border-radius: 12px; overflow: hidden; vertical-align: middle;">
                        <img src="{webapp_url}/static/img/logo.jpg"
                             alt="Unlock VPN"
                             width="44" height="44"
                             style="display: block; width: 44px; height: 44px; border-radius: 12px; object-fit: cover;">
                      </td>
                      <td style="padding-left: 12px; vertical-align: middle;">
                        <span style="color: #14181f; font-size: 18px; font-weight: 700; letter-spacing: -0.3px;">Unlock VPN</span>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <tr>
          <td class="email-content" style="padding: 36px 36px 28px;">

            <p style="font-size: 34px; margin-bottom: 6px;">{emoji}</p>
            <h1 style="color: #14181f; font-size: 20px; font-weight: 700; margin-bottom: 14px;">{title}</h1>

            {body_html}

            <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="margin-top: 8px;">
              <tr>
                <td align="center">
                  <a href="{cta_url}" class="btn-link"
                     style="display: inline-block; background-color: #16a34a; color: #ffffff;
                            text-decoration: none; padding: 15px 40px; border-radius: 10px;
                            font-size: 16px; font-weight: 700; letter-spacing: -0.1px;">
                    {cta_text} →
                  </a>
                </td>
              </tr>
            </table>

          </td>
        </tr>

        <tr>
          <td style="padding: 22px 36px;">
            <p style="color: #97a1b0; font-size: 12px; line-height: 1.6; margin: 0;">
              {footer_note}
            </p>
          </td>
        </tr>

      </table>

      <table width="520" cellpadding="0" cellspacing="0" role="presentation" style="margin-top: 20px;">
        <tr>
          <td align="center">
            <p style="color: #97a1b0; font-size: 11px; margin: 0;">
              © Unlock VPN · <a href="{webapp_url}" style="color: #97a1b0; text-decoration: none;">{webapp_url}</a>
            </p>
          </td>
        </tr>
      </table>

    </td>
  </tr>
</table>

</body>
</html>"""


async def send_zero_traffic_email(recipient_email: str, plan_name: str):
    """Письмо тем, у кого подписка активна уже давно, а расход трафика так и остался
    нулевым - вероятно, не получилось подключиться, и стоит предложить помощь заранее,
    не дожидаясь пока человек сам напишет (или просто молча не продлит подписку)."""
    body_html = f"""
    <p style="color: #4b5565; font-size: 15px; line-height: 1.6; margin-bottom: 24px;">
      У вас активна подписка <strong style="color: #14181f;">«{escape(plan_name)}»</strong>,
      но мы пока не видим расхода трафика по ней. Если уже пытались подключиться и
      что-то не получилось — напишите в поддержку, поможем настроить VPN-клиент.
      Если ещё не приступали — самое время начать: конфиг ждёт в личном кабинете.
    </p>
    """
    html = _simple_notice_html(
        emoji="📡",
        title="Не видим активности по вашей подписке",
        body_html=body_html,
        cta_text="Открыть личный кабинет",
        cta_url=f"{settings.webapp_url}/dashboard",
        footer_note=(
            "Это письмо отправлено автоматически по подписке на "
            f"<a href='{settings.webapp_url}' style='color: #97a1b0; text-decoration: underline;'>{settings.webapp_url}</a>. "
            "Если уже пользуетесь VPN на другом устройстве или через другую подписку — можно проигнорировать."
        ),
    )
    await send_email(recipient_email, "📡 Не видим активности по вашей VPN-подписке", html)


async def send_ticket_reply_email(recipient_email: str, ticket_id: int, subject: str, text_preview: str):
    """Письмо о том, что администратор ответил на тикет поддержки - дублирует
    Telegram-уведомление на случай, если у пользователя не привязан Telegram
    или он его не проверяет."""
    preview = escape(text_preview[:400]) + ("…" if len(text_preview) > 400 else "")
    body_html = f"""
    <p style="color: #4b5565; font-size: 15px; line-height: 1.6; margin-bottom: 18px;">
      Администратор ответил на ваше обращение <strong style="color: #14181f;">«{escape(subject)}»</strong> (#{ticket_id}):
    </p>
    <div style="background-color: #f1f4f8; border: 1px solid #e3e8ef; border-radius: 10px;
                padding: 16px 18px; margin-bottom: 24px; color: #14181f; font-size: 14px; line-height: 1.6;">
      {preview}
    </div>
    """
    html = _simple_notice_html(
        emoji="💬",
        title="Ответ по вашему обращению в поддержку",
        body_html=body_html,
        cta_text="Открыть обращение",
        cta_url=f"{settings.webapp_url}/dashboard/support/{ticket_id}",
        footer_note=(
            "Это письмо отправлено автоматически по обращению в поддержку на "
            f"<a href='{settings.webapp_url}' style='color: #97a1b0; text-decoration: underline;'>{settings.webapp_url}</a>."
        ),
    )
    await send_email(recipient_email, f"💬 Ответ по обращению #{ticket_id} — Unlock VPN", html)


async def send_balance_bonus_email(recipient_email: str, amount: int, reason_text: str, balance: int):
    """Письмо о начислении бонуса на баланс (реферальная программа) - дублирует
    Telegram-уведомление на случай, если у пользователя не привязан Telegram."""
    body_html = f"""
    <p style="color: #4b5565; font-size: 15px; line-height: 1.6; margin-bottom: 24px;">
      {escape(reason_text)}<br>
      Вам начислено <strong style="color: #14181f;">{amount} ₽</strong> на баланс.
      Текущий баланс: <strong style="color: #14181f;">{balance} ₽</strong>.
      Баланс можно использовать при оплате подписки.
    </p>
    """
    html = _simple_notice_html(
        emoji="🎉",
        title="Начислен бонус на баланс",
        body_html=body_html,
        cta_text="Перейти в кабинет",
        cta_url=f"{settings.webapp_url}/dashboard/referral",
        footer_note=(
            "Это письмо отправлено автоматически по реферальной программе на "
            f"<a href='{settings.webapp_url}' style='color: #97a1b0; text-decoration: underline;'>{settings.webapp_url}</a>."
        ),
    )
    await send_email(recipient_email, "🎉 Начислен бонус на баланс — Unlock VPN", html)


async def send_expiry_reminder_email(recipient_email: str, plan_name: str, days_left: int, expires_str: str):
    """Письмо-напоминание о скором истечении подписки. Раньше напоминание уходило
    только в Telegram - пользователи, вошедшие по email без привязанного Telegram,
    не узнавали об истечении вовсе и молча теряли доступ."""
    body_html = f"""
    <p style="color: #4b5565; font-size: 15px; line-height: 1.6; margin-bottom: 24px;">
      Ваша подписка <strong style="color: #14181f;">«{escape(plan_name)}»</strong> истекает
      через <strong style="color: #14181f;">{days_left} дн.</strong> ({escape(expires_str)}).
      Продлите заранее, чтобы доступ к VPN не прерывался — после истечения
      подключение блокируется автоматически.
    </p>
    """
    html = _simple_notice_html(
        emoji="⏳",
        title="Подписка скоро истекает",
        body_html=body_html,
        cta_text="Продлить подписку",
        cta_url=f"{settings.webapp_url}/dashboard",
        footer_note=(
            "Это письмо отправлено автоматически по вашей подписке на "
            f"<a href='{settings.webapp_url}' style='color: #97a1b0; text-decoration: underline;'>{settings.webapp_url}</a>."
        ),
    )
    await send_email(recipient_email, f"⏳ Подписка истекает через {days_left} дн. — Unlock VPN", html)


async def send_subscription_expired_email(recipient_email: str, plan_name: str):
    """Письмо о том, что подписка истекла и доступ заблокирован. Пара к
    send_expiry_reminder_email - тот же случай пользователей без Telegram."""
    body_html = f"""
    <p style="color: #4b5565; font-size: 15px; line-height: 1.6; margin-bottom: 24px;">
      Срок действия подписки <strong style="color: #14181f;">«{escape(plan_name)}»</strong> закончился,
      и доступ к VPN по ней заблокирован. Продлите подписку в личном кабинете —
      доступ восстановится сразу после оплаты, на том же конфиге.
    </p>
    """
    html = _simple_notice_html(
        emoji="⚠️",
        title="Подписка истекла",
        body_html=body_html,
        cta_text="Продлить подписку",
        cta_url=f"{settings.webapp_url}/dashboard",
        footer_note=(
            "Это письмо отправлено автоматически по вашей подписке на "
            f"<a href='{settings.webapp_url}' style='color: #97a1b0; text-decoration: underline;'>{settings.webapp_url}</a>."
        ),
    )
    await send_email(recipient_email, "⚠️ Подписка истекла — доступ заблокирован — Unlock VPN", html)
