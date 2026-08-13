"""Asynchrónny klient pre zákaznícky portál BVS.

Portál beží na SAP Multichannel Foundation for Utilities, dáta sa čítajú cez
OData v2 službu ZERP_UTILITIES_UMC_SRV. Detailná dokumentácia endpointov,
login flow a chovania WAF-u je v POZNAMKY.md v koreni projektu.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE = "https://zakaznik.bvsas.sk"
PORTAL = f"{BASE}/portal"
ODATA = f"{BASE}/sap/opu/odata/sap/ZERP_UTILITIES_UMC_SRV"
LOGOFF = f"{BASE}/sap/public/bc/icf/logoff"

# WAF pred aplikáciou zahadzuje requesty bez dôveryhodného User-Agenta.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

SAP_CLIENT = "100"
SAP_LANGUAGE = "SK"

_XSRF_RE = re.compile(r'name="sap-login-XSRF"\s+value="([^"]*)"', re.IGNORECASE)
_ERROR_RE = re.compile(r'<div id="ERROR_MESSAGE"[^>]*>(.*?)</div>', re.S)
_SAP_DATE_RE = re.compile(r"^/Date\((-?\d+)([+-]\d+)?\)/$")


class BvsError(Exception):
    """Základná chyba integrácie."""


class BvsAuthError(BvsError):
    """Nesprávne prihlasovacie údaje alebo vypršaná session."""


class BvsWafError(BvsError):
    """WAF zahodil request -- cesta nie je na whiteliste."""


def parse_sap_date(value: Any) -> datetime | None:
    """SAP OData v2 posiela dátumy ako /Date(1728493620000)/ (ms od epochy, UTC)."""
    if not isinstance(value, str):
        return None
    m = _SAP_DATE_RE.match(value)
    if not m:
        return None
    dt = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
    if m.group(2):
        sign = 1 if m.group(2)[0] == "+" else -1
        dt += sign * timedelta(minutes=int(m.group(2)[1:]))
    return dt


def parse_decimal(value: Any) -> float | None:
    """SAP posiela Edm.Decimal ako string, napr. "152.00000000000000"."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class BvsApi:
    """Tenký klient nad OData službou portálu."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._logged_in = False
        self._account_id: str | None = None

    # ------------------------------------------------------------ prihlásenie

    async def _fetch_login_xsrf(self) -> str:
        async with self._session.get(
            PORTAL, headers={"User-Agent": USER_AGENT}
        ) as resp:
            resp.raise_for_status()
            body = await resp.text()
        if "Request Rejected" in body:
            raise BvsWafError("WAF zahodil GET /portal")
        m = _XSRF_RE.search(body)
        if not m:
            raise BvsError("sap-login-XSRF sa nenašiel -- portál zmenil login stránku")
        # Token je v HTML entity-escapovaný ('=' ako &#x3d;) a je jednorazový.
        return html.unescape(m.group(1))

    async def login(self) -> None:
        """Formulárový login SAP ICF (POST /portal)."""
        xsrf = await self._fetch_login_xsrf()
        data = {
            "sap-system-login-oninputprocessing": "",
            "sap-urlscheme": "",
            "sap-system-login": "onLogin",
            "sap-system-login-basic_auth": "",
            "sap-client": SAP_CLIENT,
            "sap-accessibility": "",
            "sap-login-XSRF": xsrf,
            "sap-system-login-cookie_disabled": "",
            "sap-hash": "",
            "sap-language": SAP_LANGUAGE,
            "sap-alias": self._username,
            "sap-password": self._password,
        }
        async with self._session.post(
            PORTAL,
            data=data,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": PORTAL,
                "Origin": BASE,
            },
        ) as resp:
            resp.raise_for_status()
            body = await resp.text()

        if "LOGIN_FORM" in body:
            message = ""
            if em := _ERROR_RE.search(body):
                message = html.unescape(re.sub(r"<[^>]+>", " ", em.group(1))).strip()
            raise BvsAuthError(message or "prihlásenie zlyhalo")

        self._logged_in = True

    async def logout(self) -> None:
        self._logged_in = False
        self._account_id = None
        try:
            async with self._session.get(
                LOGOFF, headers={"User-Agent": USER_AGENT}
            ) as resp:
                await resp.read()
        except aiohttp.ClientError:  # pragma: no cover - best effort
            pass

    # ------------------------------------------------------------------ OData

    async def _get(self, path: str, _retry: bool = True) -> Any:
        if not self._logged_in:
            await self.login()

        async with self._session.get(
            f"{ODATA}{path}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        ) as resp:
            resp.raise_for_status()
            body = await resp.text()

        # WAF nevracia 4xx -- odpovie 200 s HTML stránkou.
        if "Request Rejected" in body:
            raise BvsWafError(f"WAF zahodil {path}")

        if "LOGIN_FORM" in body:
            self._logged_in = False
            if _retry:
                _LOGGER.debug("Session vypršala, prihlasujem sa znova")
                await self.login()
                return await self._get(path, _retry=False)
            raise BvsAuthError("session vypršala a obnovenie zlyhalo")

        try:
            return json.loads(body)["d"]
        except (ValueError, KeyError) as exc:
            raise BvsError(f"neočakávaná odpoveď pre {path}") from exc

    async def async_get_raw(self, path: str) -> Any:
        """Surová odpoveď OData -- pre diagnostiku."""
        return await self._get(path)

    @staticmethod
    def _rows(payload: Any) -> list[dict]:
        if not isinstance(payload, dict):
            return []
        if "results" in payload:
            return payload["results"]
        return [payload]

    # -------------------------------------------------------------- discovery

    async def async_get_accounts(self) -> list[dict]:
        """Všetci obchodní partneri prihláseného účtu.

        Jeden portálový login môže mať viac obchodných partnerov (napr. dve
        nehnuteľnosti s vlastnými zmluvami) -- odberné miesta treba hľadať
        pod každým z nich.
        """
        rows = self._rows(await self._get("/Accounts?$format=json"))
        if not rows:
            raise BvsError("na účte nie je žiadny obchodný partner")
        self._account_id = rows[0]["AccountID"]
        return [
            {
                "account_id": row.get("AccountID"),
                "full_name": row.get("FullName"),
                "email": row.get("ZzRegisteredMail"),
            }
            for row in rows
        ]

    async def async_get_account(self, account_id: str | None = None) -> dict:
        """Jeden obchodný partner -- podľa ID, alebo prvý na účte."""
        accounts = await self.async_get_accounts()
        if account_id is not None:
            for account in accounts:
                if account["account_id"] == account_id:
                    return account
        return accounts[0]

    async def async_get_contracts(self) -> list[dict]:
        """Odberné miesta zo všetkých obchodných partnerov účtu.

        Kolekcia /Contracts je za WAF-om zakázaná, preto sa ide cez
        Accounts -> ContractAccounts s $expand=Contracts (rovnako ako portál).
        """
        contracts: list[dict] = []
        for account in await self.async_get_accounts():
            payload = await self._get(
                f"/Accounts('{account['account_id']}')/ContractAccounts"
                "?$format=json&$expand=Contracts"
            )
            contract_accounts = self._rows(payload)
            for ca in contract_accounts:
                for contract in self._rows(ca.get("Contracts") or {}):
                    contracts.append(
                        {
                            "contract_id": contract.get("ContractID"),
                            "contract_account_id": ca.get("ContractAccountID"),
                            "account_id": account["account_id"],
                            # Zmluvný účet nesie číslo OM v poli Description
                            # (napr. "OM20008931").
                            "name": ca.get("Description")
                            or ca.get("ContractAccountName")
                            or contract.get("ContractID"),
                        }
                    )
            _LOGGER.debug(
                "Partner %s: %d zmluvných účtov, priebežne %d zmlúv",
                account["account_id"],
                len(contract_accounts),
                len(contracts),
            )
        if not contracts:
            raise BvsError("na účte nie je žiadne odberné miesto")
        return contracts

    async def async_get_devices(self, contract_id: str) -> list[dict]:
        """Vodomery na odbernom mieste.

        Portál vracia pre jeden vodomer aj viac riadkov (jeden býva bez
        sériového čísla), preto sa deduplikuje podľa DeviceID a uprednostní
        sa riadok, ktorý má vyplnené sériové číslo.
        """
        payload = await self._get(
            f"/Contracts('{contract_id}')/Devices"
            "?$format=json&$expand=FutureMeterReadings"
        )
        devices: dict[str, dict] = {}
        for row in self._rows(payload):
            device_id = row.get("DeviceID")
            if not device_id:
                continue
            # Riadky sa dopĺňajú, nie prepisujú -- jeden nesie sériové číslo
            # a tarifu, iný plánovaný odpočet.
            device = devices.setdefault(
                device_id,
                {
                    "device_id": device_id,
                    "serial_number": None,
                    # Tarifart rozlišuje hlavný a záhradný vodomer.
                    "tarifart": None,
                    "installation_id": None,
                    "next_reading": None,
                },
            )
            if row.get("SerialNumber"):
                device["serial_number"] = row["SerialNumber"]
            if row.get("Tarifart"):
                device["tarifart"] = row["Tarifart"]
            if row.get("Anlage"):
                device["installation_id"] = row["Anlage"]
            for future in self._rows(row.get("FutureMeterReadings") or {}):
                when = parse_sap_date(future.get("MeterReadingDate"))
                if when and (
                    device["next_reading"] is None or when < device["next_reading"]
                ):
                    device["next_reading"] = when
        return list(devices.values())

    async def async_get_device_readings(self, device_id: str) -> list[dict]:
        """Odpočty jedného vodomeru (rovnaká cesta, akú volá portál)."""
        payload = await self._get(
            f"/Devices('{device_id}')/MeterReadingResults?$format=json"
            "&$expand=MeterReadingStatus,MeterReadingCategory,MeterReadingReason"
        )
        return self._parse_readings(payload)

    # ------------------------------------------------------------------- dáta

    async def async_get_consumption(
        self, contract_id: str, since: str = "2010-01-01"
    ) -> list[dict]:
        """Spotreba po fakturačných obdobiach."""
        flt = quote(f"StartDate ge datetime'{since}T00:00:00'", safe="")
        payload = await self._get(
            f"/Contracts('{contract_id}')/ContractConsumptionValues"
            f"?$filter={flt}&$format=json"
        )
        rows = []
        for row in self._rows(payload):
            start = parse_sap_date(row.get("StartDate"))
            end = parse_sap_date(row.get("EndDate"))
            value = parse_decimal(row.get("ConsumptionValue"))
            if start is None or end is None or value is None:
                continue
            rows.append(
                {
                    "start": start,
                    "end": end,
                    "consumption": value,
                    "unit": row.get("ConsumptionUnit"),
                    "billed_amount": parse_decimal(row.get("BilledAmount")),
                    "currency": row.get("Currency"),
                    "period_type": row.get("ConsumptionPeriodTypeID"),
                }
            )
        rows.sort(key=lambda r: r["start"])
        return rows

    async def async_get_readings(self, contract_id: str) -> list[dict]:
        """Odpočty všetkých vodomerov na OM vrátane plánovaných (StatusID '4')."""
        payload = await self._get(
            f"/Contracts('{contract_id}')/MeterReadingResults?$format=json"
        )
        return self._parse_readings(payload)

    def _parse_readings(self, payload: Any) -> list[dict]:
        rows = []
        for row in self._rows(payload):
            when = parse_sap_date(row.get("ReadingDateTime"))
            if when is None:
                continue
            rows.append(
                {
                    "when": when,
                    "device_id": row.get("DeviceID"),
                    "serial_number": row.get("SerialNumber"),
                    "reading": parse_decimal(row.get("ReadingResult")),
                    "consumption": parse_decimal(row.get("Consumption")),
                    "unit": row.get("ReadingUnit"),
                    "status_id": row.get("MeterReadingStatusID"),
                    "category_id": row.get("MeterReadingCategoryID"),
                    "reason_id": row.get("MeterReadingReasonID"),
                }
            )
        rows.sort(key=lambda r: r["when"])
        return rows

    async def async_get_yearly(
        self, contract_id: str, account_id: str | None = None
    ) -> list[dict]:
        """Ročné súčty + referenčná spotreba (graf "Vývoj spotreby" v portáli)."""
        if account_id is None:
            if self._account_id is None:
                await self.async_get_accounts()
            account_id = self._account_id
        flt = quote(f"AccountID eq '{account_id}'", safe="")
        payload = await self._get(
            f"/Contracts('{contract_id}')/InstallationConsumptionSet"
            f"?$format=json&$filter={flt}"
        )
        rows = []
        for row in self._rows(payload):
            rows.append(
                {
                    "year": row.get("Year"),
                    "installation_id": row.get("InstallationID"),
                    "consumption": parse_decimal(row.get("ConsumptionAll")),
                    "reference": parse_decimal(row.get("ConsumptionAvg")),
                }
            )
        rows.sort(key=lambda r: r["year"] or "")
        return rows
