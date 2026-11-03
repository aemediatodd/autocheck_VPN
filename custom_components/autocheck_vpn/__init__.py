"""VPN check-in integration."""
from .const import DOMAIN, PLATFORMS
from .coordinator import AutoCheckCoordinator


async def async_setup_entry(hass, entry):
    coordinator = AutoCheckCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    try:
        await coordinator.async_initialize()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_close()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    # Config updates (session rotation) should not reload; options flow does it explicitly.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    coordinator.loaded_options = dict(entry.options)
    return True


async def _async_options_updated(hass, entry):
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator and dict(entry.options) != coordinator.loaded_options:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_close()
        return True
    return False
