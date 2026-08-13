"""Konštanty integrácie BVS."""

DOMAIN = "bvs"

CONF_CONTRACT_ID = "contract_id"
CONF_ACCOUNT_ID = "account_id"
CONF_INSTALLATION_ID = "installation_id"

# Portál nie je určený na časté dotazovanie -- raz denne stačí, odpočty
# prichádzajú v mesiacoch.
UPDATE_INTERVAL_HOURS = 12

# Statistic ID pre dlhodobú históriu (external statistics).
STAT_ID_TEMPLATE = "bvs:water_{contract_id}"

UNIT_CUBIC_METERS = "m³"

# MeterReadingStatusID -- overené na živých dátach portálu:
#   "1" = Zúčtovateľné, "4" = Uvoľnené referentom (napr. priebežný odpočet
#   alebo odpočet nahlásený zákazníkom, zatiaľ nezúčtovaný).
# Oba sú skutočné vykonané odpočty. Plánovaný odpočet NIE je status "4" --
# ten má vlastnú entitu FutureMeterReadings.
READING_STATUS_BILLABLE = "1"
READING_STATUS_RELEASED = "4"
READING_STATUSES_DONE = frozenset({READING_STATUS_BILLABLE, READING_STATUS_RELEASED})

# ConsumptionPeriodTypeID
PERIOD_TYPE_BILLING_CYCLE = "BC"

# Druh vodomeru podľa tarify (Tarifart). Portál z toho skladá popisky
# v štýle "8SEN0131702184 - hlavný vodomer - diaľkový odpočet"; samotné API
# text nevracia, preto malá mapa. Neznámy kód -> sériové číslo.
TARIFART_NAMES = {
    "DT_MER_1": "hlavný vodomer",
    "DT_ZAH_1": "záhradný vodomer",
}
TARIFART_PRIMARY = "DT_MER_1"
