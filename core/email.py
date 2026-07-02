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
  /* Сброс для почтовых клиентов */
  body, table, td, p, a {{ margin: 0; padding: 0; border: 0; }}
  body {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
  table {{ border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
  img {{ border: 0; display: block; outline: none; }}

  /* Мобильная адаптация */
  @media screen and (max-width: 600px) {{
    .email-wrapper {{ width: 100% !important; }}
    .email-content {{ padding: 24px 16px !important; }}
    .btn-link {{ padding: 14px 24px !important; font-size: 15px !important; }}
    .link-box {{ padding: 12px !important; font-size: 11px !important; word-break: break-all; }}
  }}
</style>
</head>
<body style="background-color: #0a0e14; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">

<!-- Внешняя обёртка -->
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background-color: #0a0e14; padding: 40px 16px;">
  <tr>
    <td align="center">

      <!-- Карточка письма -->
      <table class="email-wrapper" width="520" cellpadding="0" cellspacing="0" role="presentation"
             style="background-color: #12161f; border: 1px solid #232a38; border-radius: 16px; overflow: hidden;">

        <!-- Шапка с брендом -->
        <tr>
          <td style="background: linear-gradient(135deg, #12161f 0%, #161b26 100%); border-bottom: 1px solid #232a38; padding: 28px 36px;">
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
                        <span style="color: #ffffff; font-size: 18px; font-weight: 700; letter-spacing: -0.3px;">Unlock VPN</span>
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

            <p style="color: #9aa5ba; font-size: 15px; line-height: 1.6; margin-bottom: 28px;">
              Нажмите кнопку ниже — она откроет личный кабинет автоматически, без пароля.
            </p>

            <!-- Кнопка входа -->
            <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="margin-bottom: 24px;">
              <tr>
                <td align="center">
                  <a href="{link}" class="btn-link"
                     style="display: inline-block; background-color: #3ddc84; color: #06140c;
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
                <td style="border-top: 1px solid #232a38; font-size: 0;">&nbsp;</td>
              </tr>
            </table>

            <!-- Запасная ссылка -->
            <p style="color: #6b7689; font-size: 13px; margin-bottom: 8px;">
              Если кнопка не работает — скопируйте ссылку вручную:
            </p>
            <div class="link-box"
                 style="background-color: #0a0e14; border: 1px solid #232a38; border-radius: 8px;
                        padding: 10px 14px; font-size: 12px; color: #6b7689;
                        word-break: break-all; font-family: 'Courier New', monospace;">
              {link}
            </div>

          </td>
        </tr>

        <!-- Предупреждение об истечении -->
        <tr>
          <td style="background-color: rgba(61,220,132,0.05); border-top: 1px solid #232a38; border-bottom: 1px solid #232a38; padding: 14px 36px;">
            <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
              <tr>
                <td style="width: 20px; vertical-align: top; padding-top: 1px;">
                  <span style="color: #3ddc84; font-size: 14px;">⏱</span>
                </td>
                <td style="padding-left: 8px;">
                  <p style="color: #9aa5ba; font-size: 13px; line-height: 1.5; margin: 0;">
                    Ссылка действительна <strong style="color: #e2e8f0;">15 минут</strong>.
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
            <p style="color: #6b7689; font-size: 12px; line-height: 1.6; margin: 0;">
              Это письмо отправлено автоматически, потому что кто-то (скорее всего вы)
              запросил вход в кабинет на сайте
              <a href="{webapp_url}" style="color: #6b7689; text-decoration: underline;">{webapp_url}</a>.
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
            <p style="color: #3a4254; font-size: 11px; margin: 0;">
              © Unlock VPN · <a href="{webapp_url}" style="color: #3a4254; text-decoration: none;">{webapp_url}</a>
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
