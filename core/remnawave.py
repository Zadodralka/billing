import httpx
from datetime import datetime, timedelta
from core.config import settings


class RemnawaveClient:
    def __init__(self):
        self.base_url = settings.remnawave_url.rstrip("/")
        self.token = settings.remnawave_token
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs):
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                headers=self.headers,
                **kwargs,
            )
            response.raise_for_status()
            return response.json()

    async def create_user(self, username: str, days: int, traffic_limit_gb: int = 0) -> dict:
        """Создать пользователя в Remnawave"""
        expire_at = datetime.utcnow() + timedelta(days=days)
        payload = {
            "username": username,
            "expireAt": expire_at.isoformat() + "Z",
            "trafficLimitBytes": traffic_limit_gb * 1024 ** 3 if traffic_limit_gb else 0,
            "trafficLimitStrategy": "NO_RESET" if not traffic_limit_gb else "MONTH",
            "status": "ACTIVE",
        }
        return await self._request("POST", "/api/users", json=payload)

    async def extend_user(self, uuid: str, extra_days: int) -> dict:
        """Продлить подписку пользователя"""
        user = await self.get_user(uuid)
        current_expire = datetime.fromisoformat(user["expireAt"].replace("Z", ""))
        if current_expire < datetime.utcnow():
            current_expire = datetime.utcnow()
        new_expire = current_expire + timedelta(days=extra_days)
        return await self._request("PUT", f"/api/users/{uuid}", json={
            "expireAt": new_expire.isoformat() + "Z",
            "status": "ACTIVE",
        })

    async def get_user(self, uuid: str) -> dict:
        """Получить данные пользователя"""
        return await self._request("GET", f"/api/users/{uuid}")

    async def get_user_config(self, uuid: str) -> dict:
        """Получить конфиги подключения"""
        return await self._request("GET", f"/api/users/{uuid}/config")

    async def disable_user(self, uuid: str) -> dict:
        """Заблокировать пользователя"""
        return await self._request("PUT", f"/api/users/{uuid}", json={"status": "DISABLED"})

    async def enable_user(self, uuid: str) -> dict:
        """Разблокировать пользователя"""
        return await self._request("PUT", f"/api/users/{uuid}", json={"status": "ACTIVE"})

    async def delete_user(self, uuid: str) -> dict:
        """Удалить пользователя"""
        return await self._request("DELETE", f"/api/users/{uuid}")

    async def get_all_users(self) -> list:
        """Получить всех пользователей"""
        data = await self._request("GET", "/api/users")
        return data if isinstance(data, list) else data.get("users", [])

    async def get_stats(self) -> dict:
        """Статистика панели"""
        return await self._request("GET", "/api/stats")


remnawave = RemnawaveClient()
