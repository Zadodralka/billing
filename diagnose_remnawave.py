"""
Диагностика API Remnawave - определяет рабочие форматы эндпоинтов.
Запуск:  docker compose exec web python3 /app/diagnose_remnawave.py
"""
import asyncio
import httpx
import os
import json


async def main():
    base_url = os.environ["REMNAWAVE_URL"].rstrip("/")
    token = os.environ["REMNAWAVE_TOKEN"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        # 1. Получаем список пользователей (это точно работает)
        print("=== GET /api/users?size=1 ===")
        r = await client.get(f"{base_url}/api/users?size=1")
        print(f"Status: {r.status_code}")
        data = r.json()

        # Достаём первого пользователя
        users = data
        if isinstance(data, dict):
            candidate = data.get("response", data)
            if isinstance(candidate, dict):
                for key in ("users", "items", "data", "results"):
                    if key in candidate and isinstance(candidate[key], list):
                        users = candidate[key]
                        break
            elif isinstance(candidate, list):
                users = candidate

        if not users:
            print("Не удалось получить ни одного пользователя")
            return

        user = users[0]
        uuid = user.get("uuid")
        short_uuid = user.get("shortUuid")
        user_id = user.get("id")
        username = user.get("username")
        print(f"\nПервый пользователь:")
        print(f"  uuid = {uuid}")
        print(f"  shortUuid = {short_uuid}")
        print(f"  id = {user_id}")
        print(f"  username = {username}")

        # 2. Пробуем разные форматы получения одного пользователя
        print("\n=== Проверка форматов GET одного пользователя ===")
        candidates = [
            f"/api/users/{uuid}",
            f"/api/users/by-uuid/{uuid}",
            f"/api/users/uuid/{uuid}",
            f"/api/users/{short_uuid}",
            f"/api/users/by-short-uuid/{short_uuid}",
            f"/api/users/{user_id}",
            f"/api/users/by-username/{username}",
            f"/api/users/username/{username}",
        ]
        for path in candidates:
            try:
                rr = await client.get(f"{base_url}{path}")
                marker = "✅ РАБОТАЕТ" if rr.status_code == 200 else f"❌ {rr.status_code}"
                print(f"  {marker}  GET {path}")
            except Exception as e:
                print(f"  ⚠️ ошибка  GET {path}: {e}")

        # 3. Список доступных squad'ов
        print("\n=== GET /api/internal-squads ===")
        try:
            rs = await client.get(f"{base_url}/api/internal-squads")
            print(f"Status: {rs.status_code}")
            print(json.dumps(rs.json(), ensure_ascii=False)[:600])
        except Exception as e:
            print(f"Ошибка: {e}")


if __name__ == "__main__":
    asyncio.run(main())
