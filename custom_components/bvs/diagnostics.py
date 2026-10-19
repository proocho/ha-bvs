"""Diagnostika integrácie BVS.

Stiahne surové odpovede portálu pre discovery reťaz (Accounts ->
ContractAccounts -> Contracts), aby sa dalo zistiť, prečo sa niektoré odberné
miesto v config flow neponúkne. Osobné údaje sa redigujú.
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from urllib.parse import quote

from . import BvsConfigEntry
from .api import BvsError
from .const import CONF_ACCOUNT_ID, CONF_CONTRACT_ID, CONF_INSTALLATION_ID

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_ACCOUNT_ID,
    CONF_CONTRACT_ID,
    CONF_INSTALLATION_ID,
    "AccountID",
    "BusinessAgreementID",
    "City",
    "CityName",
    "ContractAccountID",
    "ContractAccountName",
    "ContractID",
    "Description",
    "Email",
    "FirstName",
    "FullName",
    "HouseNo",
    "HouseNum1",
    "LastName",
    "MobilePhone",
    "PhoneNumber",
    "PostalCode",
    "Street",
    "ZzRegisteredMail",
    "contract_account_id",
    "name",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BvsConfigEntry
) -> dict[str, Any]:
    """Vráti surové dáta discovery reťaze."""
    coordinator = entry.runtime_data
    api = coordinator.api
    diag: dict[str, Any] = {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "contract_id": coordinator.contract_id,
        "statistic_id": coordinator.statistic_id,
    }

    account_ids: list = []
    try:
        accounts_raw = await api.async_get_raw("/Accounts?$format=json")
        diag["accounts_raw"] = async_redact_data(accounts_raw, TO_REDACT)

        account_ids = [
            row.get("AccountID")
            for row in (accounts_raw or {}).get("results", [])
            if isinstance(row, dict)
        ]
        diag["account_count"] = len(account_ids)

        per_account: dict[str, Any] = {}
        for index, account_id in enumerate(account_ids):
            per_account[f"account_{index}"] = async_redact_data(
                await api.async_get_raw(
                    f"/Accounts('{account_id}')/ContractAccounts"
                    "?$format=json&$expand=Contracts"
                ),
                TO_REDACT,
            )
        diag["contract_accounts_raw"] = per_account

        diag["discovered_contracts"] = async_redact_data(
            await api.async_get_contracts(), TO_REDACT
        )

        # Odberné miesto = Installation; jedna zmluva ich môže mať viac.
        diag["installations_raw"] = async_redact_data(
            await api.async_get_raw(
                f"/Contracts('{coordinator.contract_id}')/InstallationConsumptionSet"
                f"?$format=json"
            ),
            TO_REDACT,
        )
        diag["devices_raw"] = async_redact_data(
            await api.async_get_raw(
                f"/Contracts('{coordinator.contract_id}')/Devices?$format=json"
            ),
            TO_REDACT,
        )
    except BvsError as err:
        diag["error"] = f"{type(err).__name__}: {err}"

    diag["probes"] = await _async_probe(api, accounts=account_ids, diag=diag)
    # Identifikátory zmluvy sa objavujú aj mimo redigovaných kľúčov -- v
    # statistic_id, v chybových hláškach s cestou dotazu a v hodnotách polí,
    # ktorých mená nepoznáme. Preto ešte jeden prechod podľa hodnôt.
    return _scrub(async_redact_data(diag, TO_REDACT), _identifiers(entry, diag))


def _identifiers(entry: BvsConfigEntry, diag: dict[str, Any]) -> set[str]:
    """Konkrétne hodnoty, ktoré identifikujú zákazníka alebo odberné miesto."""
    found = {
        str(entry.data.get(key) or "")
        for key in (CONF_USERNAME, CONF_ACCOUNT_ID, CONF_CONTRACT_ID, CONF_INSTALLATION_ID)
    }
    found.add(str(diag.get("contract_id") or ""))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in TO_REDACT and isinstance(value, (str, int)):
                    found.add(str(value))
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(diag)
    # Krátke hodnoty (prázdne, "1", "SK") by prepisovali aj nesúvisiaci text.
    return {value for value in found if len(value) >= 4}


def _scrub(node: Any, identifiers: set[str]) -> Any:
    """Nahradí výskyty identifikátorov v reťazcoch aj kľúčoch."""
    if not identifiers:
        return node
    pattern = re.compile(
        "|".join(re.escape(v) for v in sorted(identifiers, key=len, reverse=True)),
        re.IGNORECASE,
    )

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {scrub(k): scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        if isinstance(value, str):
            return pattern.sub(REDACTED, value)
        return value

    return scrub(node)


async def _async_probe(api, accounts: list, diag: dict) -> dict[str, Any]:
    """Cielené dotazy na cesty, ktoré používa samotný portál.

    Slúži na dohľadanie odberných miest, ktoré sa cez ContractAccounts
    neukázali. Každý dotaz je izolovaný -- WAF alebo chyba jednej cesty
    nezhodí ostatné. Zámerne ich je málo (WAF pri divokom skenovaní blokuje IP).
    """
    account_id = accounts[0] if accounts else None
    contract_id = diag.get("contract_id")
    ca_ids = [
        ca.get("ContractAccountID")
        for payload in diag.get("contract_accounts_raw", {}).values()
        for ca in (payload or {}).get("results", [])
        if isinstance(ca, dict)
    ]

    paths: dict[str, str] = {}
    if account_id and contract_id:
        flt = quote(f"AccountID eq '{account_id}'", safe="")
        paths["installations_filtered"] = (
            f"/Contracts('{contract_id}')/InstallationConsumptionSet"
            f"?$format=json&$filter={flt}"
        )
    if contract_id:
        paths["contract_detail"] = f"/Contracts('{contract_id}')?$format=json"
    for index, ca_id in enumerate(ca_ids):
        if account_id:
            paths[f"contract_account_{index}"] = (
                f"/Accounts('{account_id}')/ContractAccounts('{ca_id}')"
                "?$format=json&$expand=Contracts"
            )

    results: dict[str, Any] = {}
    for name, path in paths.items():
        try:
            results[name] = async_redact_data(
                await api.async_get_raw(path), TO_REDACT
            )
        except BvsError as err:
            results[name] = f"{type(err).__name__}: {err}"
    return results
