"""Optional FranklinWH cloud lookup of the installed accessory modules.

Only used at setup/reload to decide which optional modules exist (the aGate's local
interface has no installed-accessory list). Polling never touches the cloud.
Same login and accessory endpoints the FranklinWH app and the franklinwh-python
library use.
"""
from __future__ import annotations

import hashlib
from typing import Any

import aiohttp

CLOUD_BASE = "https://energy.franklinwh.com/"
ACCESSORY_GENERATOR = 3
ACCESSORY_SMART_CIRCUITS = 4
TIMEOUT = aiohttp.ClientTimeout(total=20)


class CloudError(Exception):
    """The cloud check could not be completed."""


class CloudAuthError(CloudError):
    """Wrong email/password, or the account is locked."""


async def _login(session: aiohttp.ClientSession, account: str, password: str) -> str:
    form = {
        "account": account,
        "password": hashlib.md5(password.encode("utf-8")).hexdigest(),
        "lang": "en_US",
        "type": 1,
    }
    try:
        async with session.post(CLOUD_BASE + "hes-gateway/terminal/initialize/appUserOrInstallerLogin",
                                data=form, timeout=TIMEOUT) as resp:
            resp.raise_for_status()
            js = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise CloudError(f"login request failed: {err}") from err
    code = js.get("code")
    if code in (400, 401):
        raise CloudAuthError(js.get("message") or "login refused")
    token = (js.get("result") or {}).get("token")
    if code != 200 or not token:
        raise CloudError(f"login failed: {js.get('code')} {js.get('message')}")
    return token


async def accessory_types(session: aiohttp.ClientSession, account: str, password: str,
                          gateway: str) -> set[int]:
    """Accessory type codes registered for this gateway (3 generator, 4 smart circuits)."""
    token = await _login(session, account, password)
    try:
        async with session.get(CLOUD_BASE + "hes-gateway/common/getAccessoryList",
                               params={"gatewayId": gateway, "lang": "en_US"},
                               headers={"loginToken": token}, timeout=TIMEOUT) as resp:
            resp.raise_for_status()
            js = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise CloudError(f"accessory list request failed: {err}") from err
    if js.get("code") != 200:
        raise CloudError(f"accessory list failed: {js.get('code')} {js.get('message')}")
    items: list[dict[str, Any]] = js.get("result") or []
    return {int(a["accessoryType"]) for a in items
            if isinstance(a, dict) and a.get("accessoryType") is not None
            and str(a.get("id", gateway)) == gateway}
