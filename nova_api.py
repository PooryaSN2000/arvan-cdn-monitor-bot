"""
Nova VPN Panel API Client (Optional Integration)
Handles connection, online sessions, quota monitoring, and user management for Nova VPN panel.
Optimized with persistent HTTP connection pooling and smart in-memory caching to minimize server load.
"""

import time
import datetime
from typing import List, Dict, Any, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from arvan_api import format_bytes, generate_progress_bar


class NovaClient:
    """
    High-performance REST API client for Nova VPN Panel.
    Uses connection pooling and short-lived caching to reduce API load on the VPN server.
    """
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key.strip()
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

        # Initialize persistent session with connection pooling
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": self.user_agent
        })

        retries = Retry(total=2, backoff_factor=0.5, status_forcelist=[502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries, pool_connections=2, pool_maxsize=5)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        # In-memory caches to prevent redundant API queries
        self._users_cache: Optional[List[Dict[str, Any]]] = None
        self._users_cache_time: float = 0.0
        self.users_cache_ttl: float = 30.0  # 30 seconds TTL

        self._online_cache: Optional[Dict[str, Any]] = None
        self._online_cache_time: float = 0.0
        self.online_cache_ttl: float = 10.0  # 10 seconds TTL

    def invalidate_cache(self):
        """Invalidate in-memory caches after mutating actions."""
        self._users_cache = None
        self._users_cache_time = 0.0
        self._online_cache = None
        self._online_cache_time = 0.0

    def _request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        timeout: int = 15
    ) -> Dict[str, Any]:
        url = f"{self.api_url}/{endpoint.lstrip('/')}"
        try:
            if method.upper() == "GET":
                resp = self.session.get(url, timeout=timeout)
            elif method.upper() == "POST":
                resp = self.session.post(url, json=data, timeout=timeout)
            elif method.upper() == "PATCH":
                resp = self.session.patch(url, json=data, timeout=timeout)
            elif method.upper() == "DELETE":
                resp = self.session.delete(url, timeout=timeout)
            else:
                resp = self.session.request(method, url, json=data, timeout=timeout)

            if not resp.ok:
                try:
                    err_json = resp.json()
                    msg = err_json.get("detail") or err_json.get("title") or err_json.get("message") or err_json.get("error") or str(err_json)
                except Exception:
                    msg = resp.text[:200]
                raise RuntimeError(f"Nova API Error (HTTP {resp.status_code}): {msg}")

            return resp.json()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Network error connecting to Nova Panel: {e}")

    def is_healthy(self) -> bool:
        """Check API connectivity and authentication validity."""
        try:
            res = self._request("health", timeout=8)
            return bool(res.get("ok"))
        except Exception:
            return False

    def get_all_users(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Fetch all users from panel. Reuses in-memory cache if within TTL.
        """
        now = time.time()
        if not force_refresh and self._users_cache is not None and (now - self._users_cache_time) < self.users_cache_ttl:
            return self._users_cache

        res = self._request("users")
        users_list: List[Dict[str, Any]] = []
        if isinstance(res, dict):
            users_list = res.get("users", [])
        elif isinstance(res, list):
            if res and isinstance(res[0], list):
                users_list = res[0]
            else:
                users_list = res

        self._users_cache = users_list
        self._users_cache_time = now
        return users_list

    def get_online_data(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Fetch active online sessions raw response. Cached with 10s TTL.
        """
        now = time.time()
        if not force_refresh and self._online_cache is not None and (now - self._online_cache_time) < self.online_cache_ttl:
            return self._online_cache

        data = self._request("online")
        self._online_cache = data
        self._online_cache_time = now
        return data

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Fetch full details for a specific user."""
        try:
            # Check cached users list first
            if self._users_cache is not None and (time.time() - self._users_cache_time) < self.users_cache_ttl:
                for u in self._users_cache:
                    if u.get("id") == user_id:
                        return u

            res = self._request(f"users/{requests.utils.quote(user_id)}")
            return res.get("user")
        except Exception:
            return None

    def get_online_summary(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Fetch active online sessions and cross-reference with user details.
        Skips querying all users if no devices are online, saving API calls.
        """
        online_data = self.get_online_data(force_refresh=force_refresh)
        online_map = online_data.get("online", {})
        total_users = online_data.get("users", 0)
        total_devices = online_data.get("devices", 0)

        # Optimization: If no active online devices exist, return immediately without loading users
        if total_devices == 0 or not online_map:
            return {
                "total_online_users": 0,
                "total_devices": 0,
                "users": []
            }

        all_users = self.get_all_users(force_refresh=force_refresh)
        user_by_id = {u["id"]: u for u in all_users}

        active_users = []
        for uid, dev_count in online_map.items():
            if dev_count > 0:
                user_info = user_by_id.get(uid, {})
                active_users.append({
                    "id": uid,
                    "name": user_info.get("name", uid),
                    "devices": dev_count,
                    "enabled": user_info.get("enabled", True),
                    "quota_bytes": user_info.get("quotaBytes", 0),
                    "used_bytes": user_info.get("used", 0),
                    "up_bytes": user_info.get("up", 0),
                    "down_bytes": user_info.get("down", 0),
                    "expiry": user_info.get("expiry", ""),
                    "sub_url": user_info.get("subUrl", "")
                })

        # Sort by active devices descending, then by consumed traffic descending
        active_users.sort(key=lambda x: (x["devices"], x["used_bytes"]), reverse=True)

        return {
            "total_online_users": total_users,
            "total_devices": total_devices,
            "users": active_users
        }

    def toggle_user(self, user_id: str, enabled: bool) -> bool:
        """Enable or disable a user account."""
        res = self._request(
            f"users/{requests.utils.quote(user_id)}",
            method="PATCH",
            data={"enabled": enabled}
        )
        self.invalidate_cache()
        return bool(res.get("ok"))

    def get_node_stats(self) -> Dict[str, Any]:
        """Fetch live hardware and network stats from /api/v1/stats."""
        return self._request("stats")

    def get_expiring_or_quota_users(
        self,
        quota_threshold_pct: float = 85.0,
        days_threshold: int = 3,
        force_refresh: bool = False
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Fetch users that are nearing/exceeding their quota or nearing expiration.
        Reuses cached user list to prevent duplicate calls to the VPN server.
        """
        users = self.get_all_users(force_refresh=force_refresh)
        today = datetime.date.today()

        nearing_quota = []
        nearing_expiry = []

        for u in users:
            quota_b = u.get("quotaBytes", 0)
            used_b = u.get("used", 0)
            expiry_str = u.get("expiry", "")

            # Check quota usage
            if quota_b > 0:
                pct = (used_b / quota_b) * 100.0
                if pct >= quota_threshold_pct:
                    u_item = dict(u)
                    u_item["pct"] = pct
                    nearing_quota.append(u_item)

            # Check expiration date
            if expiry_str:
                try:
                    exp_date = datetime.datetime.strptime(expiry_str, "%Y-%m-%d").date()
                    days_left = (exp_date - today).days
                    if days_left <= days_threshold:
                        u_item = dict(u)
                        u_item["days_left"] = days_left
                        nearing_expiry.append(u_item)
                except Exception:
                    pass

        nearing_quota.sort(key=lambda x: x.get("pct", 0), reverse=True)
        nearing_expiry.sort(key=lambda x: x.get("days_left", 999))

        return {
            "nearing_quota": nearing_quota,
            "nearing_expiry": nearing_expiry
        }

    def add_user_quota(self, user_id: str, additional_gb: float) -> bool:
        """Increase user quota by additional_gb."""
        user = self.get_user(user_id)
        if not user:
            return False
        current_quota = user.get("quotaBytes", 0)
        additional_bytes = int(additional_gb * (1024 ** 3))
        new_quota = current_quota + additional_bytes
        res = self._request(
            f"users/{requests.utils.quote(user_id)}",
            method="PATCH",
            data={"quotaBytes": new_quota}
        )
        self.invalidate_cache()
        return bool(res.get("ok"))

    def reset_user_traffic(self, user_id: str) -> bool:
        """Reset user consumed traffic back to 0."""
        res = self._request(
            f"users/{requests.utils.quote(user_id)}",
            method="PATCH",
            data={"used": 0, "up": 0, "down": 0}
        )
        self.invalidate_cache()
        return bool(res.get("ok"))

    def extend_user_expiry(self, user_id: str, days: int = 30) -> bool:
        """Extend user expiration date by specified number of days."""
        user = self.get_user(user_id)
        if not user:
            return False
        today = datetime.date.today()
        try:
            if user.get("expiry"):
                current_exp = datetime.datetime.strptime(user["expiry"], "%Y-%m-%d").date()
                base_date = max(today, current_exp)
            else:
                base_date = today
        except Exception:
            base_date = today

        new_exp = base_date + datetime.timedelta(days=days)
        new_exp_str = new_exp.strftime("%Y-%m-%d")
        res = self._request(
            f"users/{requests.utils.quote(user_id)}",
            method="PATCH",
            data={"expiry": new_exp_str}
        )
        self.invalidate_cache()
        return bool(res.get("ok"))
