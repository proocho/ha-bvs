"""Koordinátor a import dlhodobej histórie do štatistík HA."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .api import BvsApi, BvsAuthError, BvsError
from .const import (
    DOMAIN,
    PERIOD_TYPE_BILLING_CYCLE,
    READING_STATUSES_DONE,
    STAT_ID_TEMPLATE,
    TARIFART_NAMES,
    TARIFART_PRIMARY,
    UNIT_CUBIC_METERS,
    UPDATE_INTERVAL_HOURS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class BvsMeter:
    """Jeden vodomer na odbernom mieste."""

    device_id: str
    serial_number: str | None
    tarifart: str | None
    next_reading: datetime | None = None
    readings: list[dict] = field(default_factory=list)

    @property
    def name(self) -> str:
        return TARIFART_NAMES.get(
            self.tarifart or "", self.serial_number or self.device_id
        )

    @property
    def is_primary(self) -> bool:
        return self.tarifart == TARIFART_PRIMARY

    @property
    def valid_readings(self) -> list[dict]:
        """Vykonané odpočty -- zúčtovateľné aj zatiaľ nezúčtovné."""
        now = dt_util.utcnow()
        return [
            r
            for r in self.readings
            if r["status_id"] in READING_STATUSES_DONE
            and r["when"] <= now
            and r["reading"] is not None
        ]

    @property
    def last_reading(self) -> dict | None:
        readings = self.valid_readings
        return readings[-1] if readings else None

    @property
    def previous_reading(self) -> dict | None:
        readings = self.valid_readings
        return readings[-2] if len(readings) > 1 else None

    @property
    def last_period_consumption(self) -> float | None:
        """Spotreba medzi poslednými dvoma odpočtami.

        Pri výmene vodomeru sa číselník vynuluje -- záporný rozdiel sa
        zahodí, spotrebu z takého obdobia portál rieši vo fakturácii.
        """
        last, previous = self.last_reading, self.previous_reading
        if not last or not previous:
            return None
        diff = last["reading"] - previous["reading"]
        return round(diff, 3) if diff >= 0 else None


@dataclass
class BvsData:
    """Snapshot dát jedného odberného miesta."""

    name: str
    contract_id: str
    account: dict = field(default_factory=dict)
    devices: list[dict] = field(default_factory=list)
    consumption: list[dict] = field(default_factory=list)
    readings: list[dict] = field(default_factory=list)
    yearly: list[dict] = field(default_factory=list)
    meters: list[BvsMeter] = field(default_factory=list)

    @property
    def primary_meter(self) -> BvsMeter | None:
        """Hlavný vodomer -- ten, ktorý reprezentuje odberné miesto."""
        for meter in self.meters:
            if meter.is_primary:
                return meter
        return self.meters[0] if self.meters else None

    # -------------------------------------------------- odvodené hodnoty

    @property
    def valid_readings(self) -> list[dict]:
        """Vykonané odpočty naprieč všetkými vodomermi OM."""
        now = dt_util.utcnow()
        return [
            r
            for r in self.readings
            if r["status_id"] in READING_STATUSES_DONE
            and r["when"] <= now
            and r["reading"] is not None
        ]

    @property
    def last_reading(self) -> dict | None:
        readings = self.valid_readings
        return readings[-1] if readings else None

    @property
    def next_reading_date(self) -> datetime | None:
        """Najbližší plánovaný odpočet (entita FutureMeterReadings)."""
        planned = sorted(m.next_reading for m in self.meters if m.next_reading)
        return planned[0] if planned else None

    @property
    def billing_periods(self) -> list[dict]:
        return [
            p
            for p in self.consumption
            if p["period_type"] == PERIOD_TYPE_BILLING_CYCLE
        ]

    @property
    def last_period(self) -> dict | None:
        periods = self.billing_periods
        return periods[-1] if periods else None

    @property
    def total_consumption(self) -> float:
        """Súčet spotreby cez všetky fakturačné obdobia.

        Používa sa namiesto stavu vodomeru, pretože na OM sa vodomer môže
        vymeniť a číselník sa vynuluje -- fakturovaná spotreba je spojitá.
        """
        return round(sum(p["consumption"] for p in self.billing_periods), 3)

    def year_row(self, year: int) -> dict | None:
        for row in self.yearly:
            if row["year"] == str(year):
                return row
        return None

    @property
    def current_year_row(self) -> dict | None:
        """Ročné súčty portál zverejňuje až za uzavreté roky.

        Prebiehajúci rok tam spravidla ešte nie je -- vtedy sa použije
        posledný dostupný rok (`yearly` je zoradené vzostupne).
        """
        if row := self.year_row(dt_util.now().year):
            return row
        available = [r for r in self.yearly if r["consumption"] is not None]
        return available[-1] if available else None


def build_daily_series(periods: list[dict]) -> dict[date, float]:
    """Rozpočíta spotrebu fakturačných období na jednotlivé dni.

    Portál nedáva dennú spotrebu -- iba súčet za obdobie, ktoré trvá mesiace
    až rok. Aby sa história dala zobraziť ako priebeh, spotreba obdobia sa
    rozdelí rovnomerne na jeho dni. Mesačné a ročné súčty tým zostanú presné,
    denné hodnoty sú interpolované.
    """
    daily: dict[date, float] = {}
    for period in periods:
        start = dt_util.as_local(period["start"]).date()
        end = dt_util.as_local(period["end"]).date()
        if end < start:
            continue
        days = (end - start).days + 1
        per_day = period["consumption"] / days
        for offset in range(days):
            day = start + timedelta(days=offset)
            daily[day] = daily.get(day, 0.0) + per_day
    return daily


def build_statistics(daily: dict[date, float]) -> list[StatisticData]:
    """Kumulatívny rad pre external statistics (jeden bod na deň)."""
    statistics: list[StatisticData] = []
    total = 0.0
    for day in sorted(daily):
        total += daily[day]
        start = dt_util.start_of_local_day(day)
        statistics.append(
            StatisticData(start=start, state=round(daily[day], 4), sum=round(total, 4))
        )
    return statistics


class BvsCoordinator(DataUpdateCoordinator[BvsData]):
    """Sťahuje dáta z portálu a plní dlhodobé štatistiky."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: BvsApi,
        contract_id: str,
        name: str,
        account_id: str | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {name}",
            update_interval=timedelta(hours=UPDATE_INTERVAL_HOURS),
            config_entry=entry,
        )
        self.api = api
        self.contract_id = contract_id
        # Entry z verzie 1.0.0 account_id nemá -- None znamená prvého partnera.
        self.account_id = account_id
        self.installation_name = name
        self.statistic_id = STAT_ID_TEMPLATE.format(
            contract_id=contract_id
        ).lower()

    async def _async_update_data(self) -> BvsData:
        try:
            data = BvsData(name=self.installation_name, contract_id=self.contract_id)
            data.account = await self.api.async_get_account(self.account_id)
            data.devices = await self.api.async_get_devices(self.contract_id)
            data.consumption = await self.api.async_get_consumption(self.contract_id)
            data.readings = await self.api.async_get_readings(self.contract_id)
            data.yearly = await self.api.async_get_yearly(
                self.contract_id, self.account_id
            )

            # Odpočty sa čítajú per vodomer -- na OM ich môže byť viac
            # (hlavný + záhradný) a spoločný zoznam ich mieša dokopy.
            for device in data.devices:
                meter = BvsMeter(
                    device_id=device["device_id"],
                    serial_number=device.get("serial_number"),
                    tarifart=device.get("tarifart"),
                    next_reading=device.get("next_reading"),
                )
                meter.readings = await self.api.async_get_device_readings(
                    meter.device_id
                )
                data.meters.append(meter)
        except BvsAuthError as err:
            # Vyvolá reauth flow v HA.
            raise ConfigEntryAuthFailed(str(err)) from err
        except BvsError as err:
            raise UpdateFailed(str(err)) from err

        await self._async_import_statistics(data)
        return data

    async def _async_import_statistics(self, data: BvsData) -> None:
        """Zapíše celú históriu ako external statistics.

        Import je idempotentný -- recorder prepíše body s rovnakým časom, takže
        sa môže volať pri každom update.
        """
        periods = data.billing_periods
        if not periods:
            return

        statistics = build_statistics(build_daily_series(periods))
        if not statistics:
            return

        metadata = StatisticMetaData(
            # has_mean je deprecated a unit_class bude povinný -- HA 2026.11.
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"BVS spotreba vody {data.name}",
            source=DOMAIN,
            statistic_id=self.statistic_id,
            unit_of_measurement=UNIT_CUBIC_METERS,
            unit_class=VolumeConverter.UNIT_CLASS,
        )
        async_add_external_statistics(self.hass, metadata, statistics)
        _LOGGER.debug(
            "Naimportovaných %s denných bodov do %s",
            len(statistics),
            self.statistic_id,
        )
