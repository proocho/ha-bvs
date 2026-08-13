"""Integrácia BVS -- spotreba vody zo zákazníckeho portálu."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import BvsApi
from .const import CONF_ACCOUNT_ID, CONF_CONTRACT_ID, CONF_INSTALLATION_ID
from .coordinator import BvsCoordinator

PLATFORMS = [Platform.SENSOR]

type BvsConfigEntry = ConfigEntry[BvsCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: BvsConfigEntry) -> bool:
    """Nastaví integráciu z config entry."""
    # Vlastná session -- portál potrebuje vlastný cookie jar pre SAP session.
    session = async_create_clientsession(hass)
    api = BvsApi(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    coordinator = BvsCoordinator(
        hass,
        entry,
        api,
        contract_id=entry.data[CONF_CONTRACT_ID],
        name=entry.data.get(CONF_INSTALLATION_ID) or entry.data[CONF_CONTRACT_ID],
        account_id=entry.data.get(CONF_ACCOUNT_ID),
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BvsConfigEntry) -> bool:
    """Odpojí integráciu."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.api.logout()
    return unloaded
