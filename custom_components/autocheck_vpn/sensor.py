"""Check-in state and counters."""
from datetime import datetime
from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorDeviceClass, SensorStateClass
from .const import DOMAIN
from .entity import AutoCheckEntity
from .const import DEFAULT_TIME, DEFAULT_TIMEZONE
from .models import daily_window

DESCRIPTIONS = (
    SensorEntityDescription(key="status", translation_key="status", device_class=SensorDeviceClass.ENUM,
        options=["unknown", "ready", "checked_in", "login_required", "error"], icon="mdi:calendar-check"),
    SensorEntityDescription(key="total", translation_key="total", native_unit_of_measurement="次", state_class=SensorStateClass.TOTAL_INCREASING, icon="mdi:counter"),
    SensorEntityDescription(key="streak", translation_key="streak", native_unit_of_measurement="天", icon="mdi:fire"),
    SensorEntityDescription(key="last_success", translation_key="last_success", device_class=SensorDeviceClass.TIMESTAMP),
    SensorEntityDescription(key="last_reward", translation_key="last_reward", icon="mdi:gift"),
    SensorEntityDescription(key="next_run", translation_key="next_run", device_class=SensorDeviceClass.TIMESTAMP),
)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(AutoCheckSensor(coordinator, description) for description in DESCRIPTIONS)


class AutoCheckSensor(AutoCheckEntity, SensorEntity):
    def __init__(self, coordinator, description):
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        value = self.coordinator.data.get(self.entity_description.key)
        if self.entity_description.device_class == SensorDeviceClass.TIMESTAMP:
            return datetime.fromisoformat(value) if value else None
        return value

    @property
    def extra_state_attributes(self):
        if self.entity_description.key == "next_run":
            options = self.coordinator.entry.options
            target = self.coordinator.next_run
            if target:
                start, end = daily_window(target.date(), options.get("checkin_time", DEFAULT_TIME),
                    options.get("jitter_minutes", 30), options.get("timezone", DEFAULT_TIMEZONE))
                return {"base_time": options.get("checkin_time", DEFAULT_TIME),
                        "jitter_minutes": options.get("jitter_minutes", 30),
                        "random_target": self.coordinator.data.get("random_target"),
                        "window_start": start.isoformat(), "window_end": end.isoformat()}
        if self.entity_description.key == "status":
            return {"last_error": self.coordinator.data.get("error"),
                    "next_refresh": self.coordinator.data.get("next_refresh")}
        if self.entity_description.key == "last_reward":
            return {"reward_date": self.coordinator.data.get("last_reward_day")}
        if self.entity_description.key == "last_success":
            return {"performed_by_integration": self.coordinator.data.get("last_was_performed"),
                    "time_meaning": "集成确认成功的时间；网站不提供实际签到时刻"}
        return None
