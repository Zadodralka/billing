import hashlib
import hmac
import uuid
from urllib.parse import urlencode, quote
from core.config import settings


class YooMoneyClient:
    PAYMENT_URL = "https://yoomoney.ru/quickpay/confirm.xml"

    def generate_label(self) -> str:
        """Уникальный label для платежа"""
        return str(uuid.uuid4()).replace("-", "")[:20]

    def create_payment_url(self, amount: int, label: str, comment: str) -> str:
        """Ссылка на оплату через ЮМани"""
        params = {
            "receiver": settings.yoomoney_receiver,
            "quickpay-form": "shop",
            "targets": comment,
            "paymentType": "AC",  # банковская карта
            "sum": str(amount),
            "label": label,
            "successURL": f"{settings.webapp_url}/payment/success?label={label}",
        }
        return f"{self.PAYMENT_URL}?{urlencode(params)}"

    def verify_notification(self, data: dict) -> bool:
        """
        Проверка подписи уведомления ЮMoney.

        Современная схема (актуальная, поле 'sign'):
        HMAC-SHA256 в HEX от URL-кодированной строки всех параметров
        уведомления КРОМЕ sign, отсортированных по алфавиту ключей,
        склеенных через '&' как key=value.

        Устаревшая схема (поле 'sha1_hash') оставлена как fallback
        на случай если ЮMoney пришлёт уведомление в старом формате.
        """
        if "sign" in data:
            return self._verify_hmac_sha256(data)
        if "sha1_hash" in data:
            return self._verify_sha1_legacy(data)
        return False

    def _verify_hmac_sha256(self, data: dict) -> bool:
        received_sign = data.get("sign", "")
        params = {k: v for k, v in data.items() if k != "sign"}

        parts = []
        for key in sorted(params.keys()):
            value = str(params[key])
            parts.append(f"{key}={quote(value, safe='')}")
        hash_string = "&".join(parts)

        expected = hmac.new(
            settings.yoomoney_secret.encode("utf-8"),
            hash_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, received_sign)

    def _verify_sha1_legacy(self, data: dict) -> bool:
        """Старая схема подписи (для совместимости, если когда-либо понадобится)"""
        fields = [
            data.get("notification_type", ""),
            data.get("operation_id", ""),
            data.get("amount", ""),
            data.get("currency", ""),
            data.get("datetime", ""),
            data.get("sender", ""),
            data.get("codepro", ""),
            settings.yoomoney_secret,
            data.get("label", ""),
        ]
        check_string = "&".join(fields)
        expected = hashlib.sha1(check_string.encode("utf-8")).hexdigest()
        received = data.get("sha1_hash", "")
        return hmac.compare_digest(expected, received)


yoomoney = YooMoneyClient()
