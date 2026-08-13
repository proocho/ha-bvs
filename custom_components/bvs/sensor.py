"""Sensory integrácie BVS."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import BvsConfigEntry
from .const import DOMAIN, UNIT_CUBIC_METERS
from .coordinator import BvsCoordinator, BvsData, BvsMeter


@dataclass(frozen=True, kw_only=True)
class BvsSensorDescription(SensorEntityDescription):
    """Popis sensora s funkciou na získanie hodnoty."""

    value_fn: Callable[[BvsData], float | datetime | str | None]
    attrs_fn: Callable[[BvsData], dict] | None = None


def _last_reading_value(data: BvsData) -> float | None:
    """Stav hlavného vodomeru.

    Zámerne nie „posledný odpočet na OM" -- ak je na mieste aj záhradný
    vodomer, odpočty oboch majú rovnaký dátum a spoločný zoznam by vracal
    raz jeden, raz druhý.
    """
    meter = data.primary_meter
    reading = meter.last_reading if meter else None
    return reading["reading"] if reading else None


def _last_reading_attrs(data: BvsData) -> dict:
    meter = data.primary_meter
    reading = meter.last_reading if meter else None
    if not meter or not reading:
        return {}
    return {
        "vodomer": meter.name,
        "serial_number": meter.serial_number,
        "device_id": meter.device_id,
        "consumption": reading["consumption"],
        "category_id": reading["category_id"],
    }


def _last_period_attrs(data: BvsData) -> dict:
    period = data.last_period
    if not period:
        return {}
    return {
        "start": dt_util.as_local(period["start"]).date().isoformat(),
        "end": dt_util.as_local(period["end"]).date().isoformat(),
        "billed_amount": period["billed_amount"],
        "currency": period["currency"],
    }


def _yearly_attrs(data: BvsData) -> dict:
    attrs = {
        row["year"]: row["consumption"]
        for row in data.yearly
        if row["consumption"] is not None
    }
    # Za ktorý rok je hodnota sensora -- portál prebiehajúci rok nemá.
    if row := data.current_year_row:
        attrs["rok"] = row["year"]
    return attrs


SENSORS: tuple[BvsSensorDescription, ...] = (
    BvsSensorDescription(
        key="total_consumption",
        name="Spotreba celkom",
        native_unit_of_measurement=UNIT_CUBIC_METERS,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: data.total_consumption,
    ),
    BvsSensorDescription(
        key="meter_reading",
        name="Stav vodomeru",
        native_unit_of_measurement=UNIT_CUBIC_METERS,
        icon="mdi:counter",
        value_fn=_last_reading_value,
        attrs_fn=_last_reading_attrs,
    ),
    BvsSensorDescription(
        key="last_reading_date",
        name="Posledný odpočet",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: (
            reading["when"]
            if (meter := data.primary_meter) and (reading := meter.last_reading)
            else None
        ),
    ),
    BvsSensorDescription(
        key="next_reading_date",
        name="Plánovaný odpočet",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.next_reading_date,
    ),
    BvsSensorDescription(
        key="last_period_consumption",
        name="Spotreba za posledné obdobie",
        native_unit_of_measurement=UNIT_CUBIC_METERS,
        icon="mdi:water",
        value_fn=lambda data: (
            data.last_period["consumption"] if data.last_period else None
        ),
        attrs_fn=_last_period_attrs,
    ),
    BvsSensorDescription(
        key="consumption_this_year",
        name="Ročná spotreba",
        native_unit_of_measurement=UNIT_CUBIC_METERS,
        icon="mdi:calendar-month",
        value_fn=lambda data: (
            row["consumption"] if (row := data.current_year_row) else None
        ),
        attrs_fn=_yearly_attrs,
    ),
    BvsSensorDescription(
        key="reference_consumption",
        name="Referenčná spotreba",
        native_unit_of_measurement=UNIT_CUBIC_METERS,
        icon="mdi:chart-line",
        value_fn=lambda data: (
            row["reference"] if (row := data.current_year_row) else None
        ),
        attrs_fn=lambda data: (
            {"rok": row["year"]} if (row := data.current_year_row) else {}
        ),
    ),
)


@dataclass(frozen=True, kw_only=True)
class BvsMeterSensorDescription(SensorEntityDescription):
    """Popis sensora jedného vodomeru."""

    value_fn: Callable[[BvsMeter], float | datetime | None]
    attrs_fn: Callable[[BvsMeter], dict] | None = None


def _meter_reading_attrs(meter: BvsMeter) -> dict:
    reading = meter.last_reading
    if not reading:
        return {}
    return {
        "serial_number": meter.serial_number,
        "device_id": meter.device_id,
        "tarifart": meter.tarifart,
        "reason_id": reading["reason_id"],
    }


METER_SENSORS: tuple[BvsMeterSensorDescription, ...] = (
    BvsMeterSensorDescription(
        key="meter_reading",
        name="Stav vodomeru",
        native_unit_of_measurement=UNIT_CUBIC_METERS,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda meter: (
            reading["reading"] if (reading := meter.last_reading) else None
        ),
        attrs_fn=_meter_reading_attrs,
    ),
    BvsMeterSensorDescription(
        key="last_reading_date",
        name="Posledný odpočet",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda meter: (
            reading["when"] if (reading := meter.last_reading) else None
        ),
    ),
    BvsMeterSensorDescription(
        key="next_reading_date",
        name="Plánovaný odpočet",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda meter: meter.next_reading,
    ),
    BvsMeterSensorDescription(
        key="last_period_consumption",
        name="Spotreba od minulého odpočtu",
        native_unit_of_measurement=UNIT_CUBIC_METERS,
        icon="mdi:water",
        value_fn=lambda meter: meter.last_period_consumption,
        attrs_fn=lambda meter: (
            {"od": dt_util.as_local(previous["when"]).date().isoformat()}
            if (previous := meter.previous_reading)
            else {}
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BvsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Vytvorí sensory pre odberné miesto a pre každý vodomer."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        BvsSensor(coordinator, description) for description in SENSORS
    ]
    # Na jednom OM býva viac vodomerov (napr. hlavný + záhradný).
    for meter in coordinator.data.meters:
        entities.extend(
            BvsMeterSensor(coordinator, meter.device_id, description)
            for description in METER_SENSORS
        )
    async_add_entities(entities)


class BvsSensor(CoordinatorEntity[BvsCoordinator], SensorEntity):
    """Sensor nad dátami z portálu BVS."""

    _attr_has_entity_name = True
    entity_description: BvsSensorDescription

    def __init__(
        self, coordinator: BvsCoordinator, description: BvsSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.contract_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.contract_id)},
            name=f"BVS {coordinator.installation_name}",
            manufacturer="Bratislavská vodárenská spoločnosť",
            model="Odberné miesto",
            configuration_url="https://zakaznik.bvsas.sk/portal",
        )

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class BvsMeterSensor(CoordinatorEntity[BvsCoordinator], SensorEntity):
    """Sensor nad jedným vodomerom.

    Vodomer je vlastné zariadenie pod odberným miestom, aby sa hlavný
    a záhradný vodomer v UI nemiešali.
    """

    _attr_has_entity_name = True
    entity_description: BvsMeterSensorDescription

    def __init__(
        self,
        coordinator: BvsCoordinator,
        device_id: str,
        description: BvsMeterSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._device_id = device_id
        self._attr_unique_id = f"{coordinator.contract_id}_{device_id}_{description.key}"

        meter = self._meter
        name = meter.name if meter else device_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.contract_id}_{device_id}")},
            via_device=(DOMAIN, coordinator.contract_id),
            name=f"BVS {coordinator.installation_name} {name}",
            manufacturer="Bratislavská vodárenská spoločnosť",
            model="Vodomer",
            serial_number=meter.serial_number if meter else None,
            configuration_url="https://zakaznik.bvsas.sk/portal",
        )

    @property
    def _meter(self) -> BvsMeter | None:
        for meter in self.coordinator.data.meters:
            if meter.device_id == self._device_id:
                return meter
        return None

    @property
    def available(self) -> bool:
        return super().available and self._meter is not None

    @property
    def native_value(self):
        meter = self._meter
        return self.entity_description.value_fn(meter) if meter else None

    @property
    def extra_state_attributes(self) -> dict | None:
        meter = self._meter
        if meter is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(meter)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
