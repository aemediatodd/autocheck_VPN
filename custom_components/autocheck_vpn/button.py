"""Manual actions."""
from homeassistant.components.button import ButtonEntity
from .const import DOMAIN
from .entity import AutoCheckEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AutoCheckButton(coordinator, "checkin"), AutoCheckButton(coordinator, "refresh")])


class AutoCheckButton(AutoCheckEntity, ButtonEntity):
    def __init__(self, coordinator, key):
        super().__init__(coordinator, key)
        self._key = key
        self._attr_translation_key = key
        self._attr_icon = "mdi:calendar-check" if key == "checkin" else "mdi:refresh"

    async def async_press(self):
        if self._key == "checkin":
            await self.coordinator.async_checkin()
        else:
            await self.coordinator.async_request_refresh()
