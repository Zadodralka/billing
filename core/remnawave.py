import httpx
import logging
from datetime import datetime, timedelta
from core.config import settings

logger = logging.getLogger("remnawave")


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

    async def _try_request(self, method: str, path: str, **kwargs):
        """Как _request, но не бросает исключение — возвращает None при ошибке"""
        try:
            return await self._request(method, path, **kwargs)
        except Exception as e:
            logger.info(f"Optional endpoint {path} failed: {e}")
            return None

    async def ping(self) -> bool:
        """Лёгкая проверка доступности панели для админ-дашборда - в отличие от
        get_all_users(), не тянет весь список пользователей (который на реальной
        базе может быть постраничным и тяжёлым), а запрашивает одну страницу
        минимального размера и просто проверяет, что API вообще отвечает."""
        try:
            await self._request("GET", "/api/users?size=1&offset=0")
            return True
        except Exception:
            return False

    async def get_internal_squads(self) -> list:
        """Получить список доступных squad'ов (нужны для создания рабочего пользователя)"""
        data = await self._try_request("GET", "/api/internal-squads")
        if not data:
            return []
        # Подтверждённый формат: {"response": {"internalSquads": [...], "total": N}}
        if isinstance(data, dict):
            candidate = data.get("response", data)
            if isinstance(candidate, dict) and "internalSquads" in candidate:
                return candidate["internalSquads"] or []
        # Фолбэк на общий парсер для других возможных форматов
        squads, _ = self._unwrap_list_and_total(data)
        return squads

    async def create_user(
        self,
        username: str,
        days: int,
        traffic_limit_gb: int = 0,
        telegram_id: int | None = None,
        email: str | None = None,
    ) -> dict:
        expire_at = datetime.utcnow() + timedelta(days=days)
        payload = {
            "username": username,
            "expireAt": expire_at.isoformat() + "Z",
            "trafficLimitBytes": traffic_limit_gb * 1024 ** 3 if traffic_limit_gb else 0,
            "trafficLimitStrategy": "NO_RESET" if not traffic_limit_gb else "MONTH",
            "status": "ACTIVE",
        }
        if telegram_id:
            payload["telegramId"] = telegram_id
        if email:
            payload["email"] = email

        try:
            squads = await self.get_internal_squads()
            default_squad = next((s for s in squads if s.get("name") == "Default-Squad"), None)
            if default_squad and default_squad.get("uuid"):
                payload["activeInternalSquads"] = [default_squad["uuid"]]
                logger.info(f"create_user: assigning Default-Squad uuid={default_squad['uuid']}")
            elif squads:
                # Default-Squad не найден по имени - берём первый доступный как фолбэк
                first_uuid = squads[0].get("uuid")
                if first_uuid:
                    payload["activeInternalSquads"] = [first_uuid]
                    logger.warning(f"create_user: 'Default-Squad' not found by name, using first squad uuid={first_uuid}")
            else:
                logger.warning("create_user: no squads found - user will be created WITHOUT working config")
        except Exception as e:
            logger.warning(f"Could not fetch internal squads, user will be created without them: {e}")

        logger.info(f"create_user: POST /api/users payload={payload}")
        try:
            data = await self._request("POST", "/api/users", json=payload)
        except httpx.HTTPStatusError as e:
            error_body = ""
            try:
                error_body = e.response.text[:300]
            except Exception:
                pass
            logger.error(f"create_user: Remnawave rejected creation. Status={e.response.status_code}, body={error_body}")
            raise Exception(f"Remnawave отклонила создание пользователя (username='{username}'): {error_body or str(e)}")

        result = self._unwrap_single(data)
        logger.info(f"create_user: response uuid={result.get('uuid')}, status={result.get('status')}, subscriptionUrl_present={bool(result.get('subscriptionUrl'))}")
        return result

    async def extend_user(self, uuid: str, extra_days: int) -> dict:
        user = await self.get_user(uuid)
        current_expire_raw = user.get("expireAt", "")
        try:
            current_expire = datetime.fromisoformat(current_expire_raw.replace("Z", ""))
        except (ValueError, AttributeError):
            logger.error(
                f"extend_user: could not parse expireAt='{current_expire_raw}' for uuid={uuid}, "
                f"falling back to now() as base - resulting Remnawave expiry may end up shorter "
                f"than what's recorded in the app DB"
            )
            current_expire = datetime.utcnow()
        if current_expire < datetime.utcnow():
            current_expire = datetime.utcnow()
        new_expire = current_expire + timedelta(days=extra_days)

        return await self._update_user(uuid, {
            "expireAt": new_expire.isoformat() + "Z",
            "status": "ACTIVE",
        })

    _update_method_cache = None  # кэш формата (method, build_path_fn, use_body_uuid) после первого успешного запроса

    async def _update_user(self, uuid: str, fields: dict) -> dict:
        """
        Обновление пользователя. Обнаружено: PUT /api/users/{uuid} даёт 404,
        хотя GET по тому же пути работает - значит обновление идёт другим способом.
        Пробуем несколько вариантов и кэшируем рабочий.
        """
        if self._update_method_cache:
            method, build_path, use_body_uuid = self._update_method_cache
            path = build_path(uuid)
            body = {**fields, "uuid": uuid} if use_body_uuid else fields
            data = await self._request(method, path, json=body)
            return self._unwrap_single(data)

        attempts = [
            ("PATCH", lambda u: "/api/users", True),       # PATCH /api/users  body содержит uuid
            ("PUT", lambda u: "/api/users", True),          # PUT /api/users  body содержит uuid
            ("PATCH", lambda u: f"/api/users/{u}", False),  # PATCH /api/users/{uuid}
            ("POST", lambda u: f"/api/users/{u}/actions/update", False),
        ]

        last_error = None
        for method, build_path, use_body_uuid in attempts:
            path = build_path(uuid)
            body = {**fields, "uuid": uuid} if use_body_uuid else fields
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.request(
                        method, f"{self.base_url}{path}", headers=self.headers, json=body,
                    )
                if response.status_code < 300:
                    logger.info(f"_update_user: resolved working update method: {method} {path} (body_uuid={use_body_uuid})")
                    self._update_method_cache = (method, build_path, use_body_uuid)
                    return self._unwrap_single(response.json())
                else:
                    logger.info(f"_update_user: {method} {path} -> {response.status_code}, trying next")
                    last_error = f"{method} {path} -> {response.status_code}: {response.text[:200]}"
            except Exception as e:
                last_error = str(e)
                logger.info(f"_update_user: {method} {path} -> exception {e}, trying next")

        raise Exception(f"Не удалось обновить пользователя {uuid} ни одним из известных способов. Последняя ошибка: {last_error}")

    async def _find_user_path(self, uuid: str) -> str | None:
        """
        Прямой путь /api/users/{uuid} - подтверждённый рабочий формат для GET и DELETE.
        Оставлены резервные варианты на случай изменений API в будущем.
        """
        direct_path = f"/api/users/{uuid}"
        data = await self._try_request("GET", direct_path)
        if data:
            unwrapped = self._unwrap_single(data)
            if unwrapped.get("uuid") or unwrapped.get("username"):
                return direct_path
        return await self._find_user_path_fallback(uuid)

    async def _find_user_path_fallback(self, uuid: str) -> str | None:
        """Резервные форматы пути (на случай другой версии API) - без повторной
        проверки прямого пути, её уже делает вызывающий код."""
        candidates = [
            f"/api/users/by-uuid/{uuid}",
            f"/api/users/uuid/{uuid}",
        ]
        for path in candidates:
            data = await self._try_request("GET", path)
            if data:
                unwrapped = self._unwrap_single(data)
                if unwrapped.get("uuid") or unwrapped.get("username"):
                    logger.info(f"Resolved user path format: {path}")
                    return path

        logger.warning(f"Could not resolve any working path for user uuid={uuid}")
        return None

    async def get_user(self, uuid: str) -> dict:
        # Прямой путь - подтверждённый рабочий формат. Пробуем его сразу и используем
        # уже полученный ответ, а не выбрасываем его и не делаем второй идентичный
        # запрос - раньше get_traffic_usage_gb на каждый показ кабинета/рефералки
        # дёргал панель дважды за одними и теми же данными.
        direct_path = f"/api/users/{uuid}"
        data = await self._try_request("GET", direct_path)
        if data:
            unwrapped = self._unwrap_single(data)
            if unwrapped.get("uuid") or unwrapped.get("username"):
                return unwrapped

        # Резервные форматы - только если прямой путь не сработал (другая версия API)
        path = await self._find_user_path_fallback(uuid)
        if not path:
            raise Exception(f"Пользователь с UUID {uuid} не найден ни по одному формату эндпоинта")
        data = await self._request("GET", path)
        return self._unwrap_single(data)

    async def get_user_config(self, uuid: str) -> dict:
        """
        Пытается получить ссылку подписки несколькими способами:
        1) Отдельный эндпоинт /config (если существует в этой версии API)
        2) Поле subscriptionUrl прямо в объекте пользователя (обнаружено в реальном ответе API)
        """
        config_data = await self._try_request("GET", f"/api/users/{uuid}/config")
        if config_data:
            unwrapped = self._unwrap_single(config_data)
            if unwrapped.get("subscriptionUrl") or unwrapped.get("link"):
                return unwrapped

        # Фолбэк: берём subscriptionUrl прямо из объекта пользователя
        user = await self.get_user(uuid)
        return {"subscriptionUrl": user.get("subscriptionUrl", "")}

    async def get_traffic_usage_gb(self, uuid: str) -> float | None:
        """Использованный трафик за текущий период для одной подписки, в GB.
        None - если Remnawave недоступна или не отдаёт эти данные (не считаем это ошибкой,
        UI должен просто скрыть строку с расходом, а не падать)."""
        try:
            user = await self.get_user(uuid)
        except Exception as e:
            logger.info(f"get_traffic_usage_gb: could not fetch user {uuid}: {e}")
            return None
        used_bytes = self._extract_traffic_bytes(user)
        if used_bytes is None:
            return None
        return round(used_bytes / 1024 ** 3, 2)

    async def disable_user(self, uuid: str) -> dict:
        return await self._update_user(uuid, {"status": "DISABLED"})

    async def enable_user(self, uuid: str) -> dict:
        return await self._update_user(uuid, {"status": "ACTIVE"})

    async def delete_user(self, uuid: str) -> dict:
        path = await self._find_user_path(uuid)
        if not path:
            raise Exception(f"Пользователь {uuid} не найден для удаления")
        return await self._request("DELETE", path)

    def _unwrap_single(self, data: dict) -> dict:
        if isinstance(data, dict) and "response" in data and isinstance(data["response"], dict):
            return data["response"]
        return data

    def _unwrap_list_and_total(self, data) -> tuple[list, int | None]:
        if isinstance(data, list):
            return data, None
        if not isinstance(data, dict):
            return [], None

        candidate = data.get("response", data)

        total = None
        for total_key in ("total", "totalCount", "count", "totalUsers"):
            if isinstance(candidate, dict) and total_key in candidate:
                total = candidate[total_key]
                break
            if total_key in data:
                total = data[total_key]
                break

        if isinstance(candidate, list):
            return candidate, total
        if isinstance(candidate, dict):
            for key in ("users", "items", "data", "results"):
                if key in candidate and isinstance(candidate[key], list):
                    return candidate[key], total
        for key in ("users", "items", "data", "results"):
            if key in data and isinstance(data[key], list):
                return data[key], total
        return [], total

    async def get_all_users(self) -> list:
        all_users = []
        page_size = 100
        offset = 0
        max_pages = 50

        for _ in range(max_pages):
            try:
                data = await self._request("GET", f"/api/users?size={page_size}&offset={offset}")
            except Exception:
                if offset == 0:
                    data = await self._request("GET", "/api/users")
                    users, _ = self._unwrap_list_and_total(data)
                    return users
                break

            users, total = self._unwrap_list_and_total(data)
            if not users:
                break

            all_users.extend(users)
            offset += page_size

            if total is not None and len(all_users) >= total:
                break
            if len(users) < page_size:
                break

        seen = set()
        deduped = []
        for u in all_users:
            uid = u.get("uuid") or u.get("id")
            if uid and uid in seen:
                continue
            if uid:
                seen.add(uid)
            deduped.append(u)

        return deduped

    async def get_stats(self) -> dict:
        data = await self._request("GET", "/api/stats")
        return self._unwrap_single(data)

    async def get_system_stats(self) -> dict | None:
        """Пробует системные/агрегированные эндпоинты статистики (разные версии Remnawave)"""
        for path in ("/api/system/stats", "/api/stats/system", "/api/dashboard"):
            data = await self._try_request("GET", path)
            if data:
                return self._unwrap_single(data)
        return None

    async def get_nodes_stats(self) -> list | None:
        """Пробует получить список нод — иногда там агрегированный трафик по всем юзерам"""
        data = await self._try_request("GET", "/api/nodes")
        if data:
            users, _ = self._unwrap_list_and_total(data)
            return users
        return None

    def _extract_traffic_bytes(self, user: dict) -> int | None:
        """
        Точный путь обнаружен опытным путём:
        user['userTraffic']['usedTrafficBytes'] - трафик за текущий период
        user['userTraffic']['lifetimeUsedTrafficBytes'] - трафик за всё время
        """
        user_traffic = user.get("userTraffic")
        if isinstance(user_traffic, dict):
            val = user_traffic.get("usedTrafficBytes")
            if isinstance(val, (int, float)):
                return int(val)

        # Фолбэк на случай других версий API
        direct_keys = (
            "usedTrafficBytes", "trafficUsedBytes", "usedTraffic",
            "totalTrafficBytes", "trafficBytes", "usedBytes",
            "totalUsedBytes", "trafficUsed",
        )
        for key in direct_keys:
            if key in user:
                val = user[key]
                if isinstance(val, (int, float)):
                    return int(val)

        return None

    def _extract_lifetime_traffic_bytes(self, user: dict) -> int:
        user_traffic = user.get("userTraffic")
        if isinstance(user_traffic, dict):
            val = user_traffic.get("lifetimeUsedTrafficBytes")
            if isinstance(val, (int, float)):
                return int(val)
        return 0

    def _extract_online_at(self, user: dict) -> str | None:
        user_traffic = user.get("userTraffic")
        if isinstance(user_traffic, dict):
            return user_traffic.get("onlineAt")
        return None

    async def get_panel_overview(self) -> dict:
        from datetime import datetime, timezone

        users = await self.get_all_users()

        total_users = len(users)
        active_users = sum(1 for u in users if u.get("status") == "ACTIVE")
        disabled_users = total_users - active_users

        traffic_bytes_map = {}
        lifetime_traffic_map = {}
        online_at_map = {}
        traffic_field_found = False

        for u in users:
            uid = u.get("uuid")
            if not uid:
                continue
            traffic = self._extract_traffic_bytes(u)
            if traffic is not None:
                traffic_field_found = True
                traffic_bytes_map[uid] = traffic
            else:
                traffic_bytes_map[uid] = 0
            lifetime_traffic_map[uid] = self._extract_lifetime_traffic_bytes(u)
            online_at_map[uid] = self._extract_online_at(u)

        total_traffic_bytes = sum(traffic_bytes_map.values())
        total_lifetime_traffic_bytes = sum(lifetime_traffic_map.values())

        # Считаем "онлайн сейчас" как тех, кто был на связи в последние 3 минуты
        online_now_count = 0
        now = datetime.now(timezone.utc)
        for online_at in online_at_map.values():
            if not online_at:
                continue
            try:
                dt = datetime.fromisoformat(str(online_at).replace("Z", "+00:00"))
                if (now - dt).total_seconds() < 180:
                    online_now_count += 1
            except (ValueError, TypeError):
                continue

        sample_keys = list(users[0].keys()) if users else []
        user_traffic_raw = str(users[0].get("userTraffic"))[:300] if users and "userTraffic" in users[0] else None

        return {
            "total_users": total_users,
            "active_users": active_users,
            "disabled_users": disabled_users,
            "total_traffic_gb": round(total_traffic_bytes / 1024 ** 3, 2),
            "total_lifetime_traffic_gb": round(total_lifetime_traffic_bytes / 1024 ** 3, 2),
            "online_now_count": online_now_count,
            "traffic_field_found": traffic_field_found,
            "traffic_bytes_map": traffic_bytes_map,
            "lifetime_traffic_map": lifetime_traffic_map,
            "online_at_map": online_at_map,
            "users": users,
            "raw_sample": str(users[0])[:1500] if users else None,
            "sample_keys": sample_keys,
            "user_traffic_raw": user_traffic_raw,
        }


remnawave = RemnawaveClient()
