"""
Nova VPN Panel API Client (Optional Integration)
Handles connection, online sessions, quota monitoring, and user management for Nova VPN panel.
"""

import json
import urllib.request
import urllib.error
import urllib.parse
from typing import List, Dict, Any, Optional

from arvan_api import format_bytes, generate_progress_bar


class NovaClient:
    """
    Lightweight REST API client for Nova VPN Panel.
    """
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key.strip()
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    def _request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        timeout: int = 15
    ) -> Dict[str, Any]:
        url = f"{self.api_url}/{endpoint.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": self.user_agent
        }

        body_bytes = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body_bytes = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("detail") or err_json.get("title") or err_json.get("message") or str(err_json)
            except Exception:
                msg = err_body
            raise RuntimeError(f"Nova API Error (HTTP {e.code}): {msg}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Network error connecting to Nova Panel: {e.reason}")

    def is_healthy(self) -> bool:
        """Check API connectivity and authentication validity."""
        try:
            res = self._request("health")
            return bool(res.get("ok"))
        except Exception:
            return False

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Fetch all users from panel."""
        res = self._request("users")
        if isinstance(res, dict):
            return res.get("users", [])
        elif isinstance(res, list):
            if res and isinstance(res[0], list):
                return res[0]
            return res
        return []

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Fetch full details for a specific user."""
        try:
            res = self._request(f"users/{urllib.parse.quote(user_id)}")
            return res.get("user")
        except Exception:
            return None

    def get_online_summary(self) -> Dict[str, Any]:
        """
        Fetch active online sessions and cross-reference with user details.
        """
        online_data = self._request("online")
        online_map = online_data.get("online", {})
        total_users = online_data.get("users", 0)
        total_devices = online_data.get("devices", 0)

        all_users = self.get_all_users()
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
            f"users/{urllib.parse.quote(user_id)}",
            method="PATCH",
            data={"enabled": enabled}
        )
        return bool(res.get("ok"))
