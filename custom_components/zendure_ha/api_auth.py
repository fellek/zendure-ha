"""Zendure Cloud API authentication and token handling."""

from __future__ import annotations

import hashlib
import logging
import secrets
import traceback
from base64 import b64decode
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_APPTOKEN, CONF_HAKEY, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def api_ha(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any] | None:
    """Connect to the Zendure API, validate token and fetch device list."""
    session = async_get_clientsession(hass)

    if (token := data.get(CONF_APPTOKEN)) is not None and len(token) > 1:
        try:
            base64_url = b64decode(str(token)).decode("utf-8")
            api_url, app_key = base64_url.rsplit(".", 1)
        except Exception as e:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_zendure_token") from e
    else:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_zendure_token")

    try:
        timestamp = int(datetime.now().timestamp())
        nonce = str(secrets.randbelow(90000) + 10000)

        sign_params = {"appKey": app_key, "timestamp": timestamp, "nonce": nonce}
        body_str = "".join(f"{k}{v}" for k, v in sorted(sign_params.items()))

        # SHA1 bleibt erstmal, da es die Zendure API so verlangt
        sign_str = f"{CONF_HAKEY}{body_str}{CONF_HAKEY}"
        sha1 = hashlib.sha1()
        sha1.update(sign_str.encode("utf-8"))
        sign = sha1.hexdigest().upper()

        headers = {
            "Content-Type": "application/json",
            "timestamp": str(timestamp),
            "nonce": nonce,
            "clientid": "zenHa",
            "sign": sign,
        }

        result = await session.post(url=f"{api_url}/api/ha/deviceList", json={"appKey": app_key}, headers=headers)
        resp_data = await result.json()

        if resp_data.get("code") != 200:
            _LOGGER.error("Zendure API error: Code %s - Message: %s", resp_data.get("code"), resp_data.get("msg"))
            return None

        if not resp_data.get("success") or not isinstance(resp_data.get("data"), dict):
            _LOGGER.error("Invalid Zendure API response structure")
            return None

        result_data = resp_data["data"]

        if not result_data.get("deviceList"):
            _LOGGER.error("Zendure API does not reply any devices")
            return None

        if not result_data.get("mqtt"):
            _LOGGER.error("Zendure API does not reply any mqtt info")
            return None

        return result_data

    except Exception as e:
        _LOGGER.error("Unable to connect to Zendure %s!", e)
        _LOGGER.error(traceback.format_exc())
        return None
