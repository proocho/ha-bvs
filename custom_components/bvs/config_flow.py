"""Config flow pre integráciu BVS."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import BvsApi, BvsAuthError, BvsError
from .const import CONF_ACCOUNT_ID, CONF_CONTRACT_ID, CONF_INSTALLATION_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class BvsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Prihlásenie do portálu a výber odberného miesta."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str = ""
        self._password: str = ""
        self._contracts: list[dict] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            # Portál drží session v cookies, preto vlastná session bez cookie jaru HA.
            session = async_create_clientsession(self.hass)
            api = BvsApi(session, self._username, self._password)
            try:
                await api.login()
                self._contracts = await api.async_get_contracts()
            except BvsAuthError:
                errors["base"] = "invalid_auth"
            except BvsError as err:
                _LOGGER.warning("Pripojenie k portálu BVS zlyhalo: %s", err)
                errors["base"] = "cannot_connect"
            else:
                # Portál vracia len OM naviazané na tento login. Ak sú všetky
                # už pridané, povedz to konkrétne -- inak to vyzerá, že sa
                # ďalšie OM „stratilo".
                configured = {
                    entry.unique_id for entry in self._async_current_entries()
                }
                remaining = [
                    c for c in self._contracts if c["contract_id"] not in configured
                ]
                if not remaining:
                    return self.async_abort(
                        reason="all_configured",
                        description_placeholders={
                            "count": str(len(self._contracts)),
                            "found": ", ".join(c["name"] for c in self._contracts),
                        },
                    )
                self._contracts = remaining
                if len(remaining) == 1:
                    return await self._async_create(remaining[0])
                return await self.async_step_contract()
            finally:
                await api.logout()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_contract(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Výber odberného miesta, ak ich je na účte viac."""
        if user_input is not None:
            chosen = next(
                c
                for c in self._contracts
                if c["contract_id"] == user_input[CONF_CONTRACT_ID]
            )
            return await self._async_create(chosen)

        names = [c["name"] for c in self._contracts]
        options = {
            c["contract_id"]: (
                c["name"]
                if names.count(c["name"]) == 1
                else f"{c['name']} ({c['contract_id']})"
            )
            for c in self._contracts
        }
        return self.async_show_form(
            step_id="contract",
            data_schema=vol.Schema({vol.Required(CONF_CONTRACT_ID): vol.In(options)}),
        )

    async def _async_create(self, contract: dict) -> ConfigFlowResult:
        await self.async_set_unique_id(contract["contract_id"])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"BVS {contract['name']}",
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_CONTRACT_ID: contract["contract_id"],
                CONF_ACCOUNT_ID: contract.get("account_id"),
                CONF_INSTALLATION_ID: contract["name"],
            },
        )
