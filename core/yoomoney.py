import hashlib
import uuid
from urllib.parse import urlencode
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
        """Проверить подпись уведомления от ЮМани"""
        # Порядок полей строго по документации ЮМани
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
        return expected == received


yoomoney = YooMoneyClient()
