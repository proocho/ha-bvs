# Ikona pre Home Assistant (brands repo) — voliteľné

**Pre HA 2026.3.0 a novšie toto nepotrebuješ.** Ikona je priamo v integrácii
(`custom_components/bvs/brand/`) a HA ju servíruje sám cez
`/api/brands/integration/bvs/icon.png`.

Toto je len pre používateľov na **staršom HA ako 2026.3.0**, kde sa ikona ťahá
výhradne z CDN `brands.home-assistant.io` (a bez záznamu tam sa zobrazuje
„icon not available"). Obsah tohto priečinka je pripravený na PR do
[home-assistant/brands](https://github.com/home-assistant/brands).

## Ako ju tam dostať

1. Forkni `home-assistant/brands`.
2. Skopíruj adresár `custom_integrations/bvs/` z tohto priečinka do
   `custom_integrations/bvs/` vo forku (súbory `icon.png`, `icon@2x.png`,
   `logo.png`, `logo@2x.png`, `dark_logo.png`, `dark_logo@2x.png`).
3. Otvor PR. CI skontroluje rozmery (icon 256×256, @2x 512×512) automaticky.
4. Po merge sa ikona objaví v HA po pár dňoch (CDN cache).

Zdroj grafiky: oficiálne SVG logá z www.bvsas.sk
(`/templates/bvs_web/assets/images/bvs_logo_mobile.svg` + plné logo),
ikona je biely symbol na firemnej modrej (#00b4f7 → #0077a5).
