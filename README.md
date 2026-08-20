# BVS - spotreba vody pre Home Assistant

<img src="https://raw.githubusercontent.com/proocho/ha-bvs/main/custom_components/bvs/brand/logo.png" alt="BVS" width="280">

[![hacs][hacs-badge]][hacs-url]
[![release][release-badge]][release-url]

Neoficiálna integrácia spotreby vody zo zákazníckeho portálu
[BVS](https://www.bvsas.sk) (Bratislavská vodárenská spoločnosť) do Home
Assistanta, vrátane importu dlhodobej histórie spotreby.

BVS verejné API nemá. Portál `zakaznik.bvsas.sk` beží na SAP Multichannel
Foundation for Utilities a integrácia číta tie isté OData endpointy ako portál
v prehliadači. Len GET požiadavky, žiadne zápisy.

## Inštalácia

### HACS (odporúčané)

1. HACS → tri bodky vpravo hore → **Custom repositories**
2. Repository: `https://github.com/proocho/ha-bvs`, Type: **Integration**
3. Nainštaluj **BVS - spotreba vody** a reštartuj Home Assistant.

### Ručne

Skopíruj `custom_components/bvs/` do `config/custom_components/` a reštartuj HA.

## Konfigurácia

**Nastavenia → Zariadenia a služby → Pridať integráciu → „BVS - spotreba vody"**
a zadaj prihlasovacie údaje do portálu `zakaznik.bvsas.sk`.

Integrácia nájde všetky odberné miesta pod všetkými obchodnými partnermi
prihláseného účtu. Jedna konfigurácia = jedno odberné miesto — ak ich máš viac,
pridaj integráciu opakovane a zakaždým vyber ďalšie OM.

Ponúkajú sa len OM naviazané na tento portálový login. Ak ti nejaké chýba,
nemá ho ani portál — treba ho pridať tam, prípadne je vedené pod iným
prihlásením.

**Pozor na „Ostatné služby BVS":** portál v *Osobnom profile* zobrazuje aj
zmluvný účet „Ostatná fakturácia" ako samostatný riadok, takže to vyzerá ako
druhé odberné miesto. Nemá zmluvu, službu ani vodomer — integrácia ho
zámerne preskakuje. Smerodajný je zoznam v sekcii *Služby*.

Dáta sa sťahujú hneď po pridaní a potom každých 12 hodín.

## Entity

Odberné miesto:

| entita | popis |
|---|---|
| `…spotreba_celkom` | súčet fakturovanej spotreby (`total_increasing`, device class `water`) |
| `…stav_vodomeru` | stav hlavného vodomeru |
| `…posledny_odpocet` | dátum posledného odpočtu hlavného vodomeru |
| `…planovany_odpocet` | najbližší plánovaný odpočet (`FutureMeterReadings`) |
| `…spotreba_za_posledne_obdobie` | m³ za posledné fakturačné obdobie + fakturovaná suma v atribútoch |
| `…spotreba_tento_rok` | ročný súčet; portál ho zverejňuje až za uzavreté roky, preto sa použije posledný dostupný rok — je v atribúte `rok`, v atribútoch sú aj všetky roky |
| `…referencna_spotreba` | referenčná (priemerná) spotreba, s ktorou portál porovnáva |

**Každý vodomer** dostane vlastné zariadenie (na jednom OM býva hlavný aj
záhradný) so 4 entitami: `stav_vodomeru`, `posledny_odpocet`,
`planovany_odpocet` a `spotreba_od_minuleho_odpoctu`. Pri diaľkovom odpočte
chodia odpočty mesačne, takže tieto entity sa hýbu častejšie než fakturácia.
Vodomery sa pomenúvajú podľa tarify (`DT_MER_1` → hlavný, `DT_ZAH_1` →
záhradný), inak sériovým číslom.

## História a Energy dashboard

Celá história od prvého fakturačného obdobia sa importuje ako **external
statistics** pod ID `bvs:water_<contract_id>`. Štatistiky recorder nemaže,
takže história vydrží bez ohľadu na `purge_keep_days`.

V **Energy dashboarde** pridaj `bvs:water_…` ako zdroj vody (external
štatistika nesie celú históriu; senzor `spotreba_celkom` by začínal až dňom
inštalácie).

### Dôležité: denné hodnoty sú interpolované

Portál dáva spotrebu **len po fakturačných obdobiach** (mesiace až rok).
Spotreba každého obdobia sa rozpočíta rovnomerne na jeho dni — mesačné a ročné
súčty sú presné, denné hodnoty nie. V grafe neuvidíš, kedy si napúšťal bazén;
také dáta v portáli nie sú.

Zdrojom je fakturovaná spotreba, nie stav vodomeru — vodomer sa na odbernom
mieste vymieňa a číselník sa vynuluje, fakturovaná spotreba je spojitá.

## Realtime toto nevie

Portál intervalové (smart meter) dáta zákazníkom nesprístupňuje. Pre živý graf
alebo detekciu úniku treba vlastné meranie — napr.
[AI-on-the-edge-device](https://github.com/jomjol/AI-on-the-edge-device)
(ESP32-CAM nad číselníkom) alebo impulzný snímač na podružnom vodomere. Dá sa
kombinovať: BVS = kontrola fakturácie, vlastný senzor = realtime.

## Etiketa a riziká

- Update interval 12 h neznižuj — pred portálom je WAF, ktorý blokuje IP.
- Portál sa môže kedykoľvek zmeniť a integrácia sa rozbije.
- Heslo je uložené v config entry Home Assistanta (`.storage`), rovnako ako
  u ostatných cloud integrácií.
- Neoficiálny projekt, nemá žiadnu väzbu na BVS a.s.

## Diagnostika

Ak sa niečo správa čudne: **Nastavenia → Zariadenia a služby → BVS → tri bodky
→ Stiahnuť diagnostiku**. Súbor obsahuje surové odpovede portálu pre celú
discovery reťaz (Accounts → ContractAccounts → Contracts) s redigovanými
osobnými údajmi — z neho je hneď vidieť, čo portál pre daný login vracia.

## Licencia

[MIT](LICENSE)

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://github.com/hacs/integration
[release-badge]: https://img.shields.io/github/v/release/proocho/ha-bvs
[release-url]: https://github.com/proocho/ha-bvs/releases

## Ikona integrácie

Ikona je priamo v integrácii (`custom_components/bvs/brand/`) a Home Assistant
ju od verzie 2026.3.0 servíruje sám cez `/api/brands/integration/bvs/icon.png`
([brands proxy API](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api)).
Do repozitára `home-assistant/brands` sa ikony custom integrácií už neposielajú.

V paneli HACS sa zatiaľ ukazuje placeholder „icon not available" — HACS
frontend ešte ikony ťahá z CDN namiesto lokálneho proxy
([hacs/integration#5223](https://github.com/hacs/integration/issues/5223)).
Na stránke *Nastavenia → Zariadenia a služby* sa ikona zobrazuje správne.
