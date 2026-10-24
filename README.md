# Prague waste separation points for Home Assistant using Golemio API

Integrace vytváří senzory pro datum příštího svozu, pravidelné dny svozu a u měřených kontejnerů procento zaplnění. Datum a dny svozu jsou dostupné podle dat poskytovaných Golemiem. Stávající názvy a unikátní ID senzorů zůstávají zachovány.

## 1. Registrace API Golemio

Zaregistrujte se na [Golemio API](https://api.golemio.cz/api-keys/auth/sign-up) a uložte si vygenerovaný token.

## 2. Zjištění KSNKO ID jednotlivých kontejnerů

1. Otevřete [dokumentaci Golemio API](https://api.golemio.cz/docs/openapi/).
2. Klikněte na `Authorize` a vložte token.
3. Vyberte `Waste Collection (v2)` a dotaz `GET All Waste Collection Stations`.
4. Klikněte na `Try it out`. Vyplňte `latlng` souřadnicemi stanoviště, například `50.0890,14.4168`, a `range` vzdáleností v metrech, například `50`. Pro zobrazení všech kontejnerů nastavte `onlyMonitored` na `false`. Filtry `id` a `ksnkoId` nechte prázdné.
5. Klikněte na `Execute` a najděte požadované stanoviště podle názvu nebo ulice.
6. V jeho `properties.containers` vyberte jednotlivý kontejner a zkopírujte jeho `ksnko_id`. Toto číslo patří do YAML položky `container_id`.

Příklad struktury odpovědi:

```json
{
  "properties": {
    "id": 3497,
    "containers": [
      {"ksnko_id": 15288, "container_id": 15288, "trash_type": {"description": "Papír"}},
      {"ksnko_id": 15292, "container_id": 15292, "trash_type": {"description": "Plast"}}
    ]
  }
}
```

`properties.id` označuje celé stanoviště. Nepoužívejte ho jako `container_id`. Každý požadovaný kontejner se konfiguruje samostatně. Příklady ID nahraďte vlastními hodnotami z API.

API filtr `id` je zastaralý. Integrace používá filtr `ksnkoId`, ale název YAML položky `container_id` se nemění. Tím je vyřešena nejasnost popsaná v [upstream issue #15](https://github.com/Cmajda/ha_golemio/issues/15).

## 3. Konfigurace Home Assistant

Do `secrets.yaml` vložte token:

```yaml
golemio: "<TOKEN>"
```

Do `configuration.yaml` přidejte kontejnery pod jediný klíč `sensor:`. Pokud již tento klíč používáte, doplňte položky do jeho existujícího seznamu.

```yaml
sensor:
  - platform: golemio
    name: conc15288
    token: !secret golemio
    container_id: 15288
  - platform: golemio
    name: conc15292
    token: !secret golemio
    container_id: 15292
```

Při použití `sensor: !include sensors.yaml` patří do `sensors.yaml` pouze seznam, bez dalšího klíče `sensor:`:

```yaml
- platform: golemio
  name: conc15288
  token: !secret golemio
  container_id: 15288
- platform: golemio
  name: conc15292
  token: !secret golemio
  container_id: 15292
```

Po změně konfigurace restartujte Home Assistant. Ujistěte se, že názvy a ID odpovídají kontejnerům, které chcete sledovat.

## 4. Senzory a karty

V nástrojích Home Assistant vyhledejte entity podle nastaveného názvu, například `sensor.conc15288`. Pro měřený papírový kontejner mohou být dostupné tyto senzory:

```yaml
type: entities
title: Papír
entities:
  - sensor.conc15288_0_papir_datum_odvozu
  - sensor.conc15288_0_papir_dny_odvozu
  - sensor.conc15288_0_papir_volna_kapacita_0
```

Senzor `dny_odvozu` poskytuje například `Po, St, So`; jeho přidání vychází z [příspěvku cniry](https://github.com/cniry/ha_golemio/commit/dc145da62645a81a645f453ab4b2c2bca69e953f). Procentní senzor vzniká pouze při dostupném měření. Přes zachovaný původní název `volná kapacita` představuje hodnota API `percent_calculated` procento zaplnění. Chybějící měření se nenahrazuje datem svozu.

## Řešení problémů

- Při výpadku sítě, DNS, chybě HTTP nebo prázdné odpovědi při startu integrace požádá Home Assistant o opakování nastavení. Není nutné mazat entity.
- Při chybě během pravidelné aktualizace jsou senzory označeny jako nedostupné. Po úspěšné aktualizaci se obnoví.
- Požadavky mají časový limit 20 sekund a zachovávají omezení 20 požadavků za 10 sekund. Jednotlivé senzory se aktualizují nejvýše jednou za 5 minut.
- Při trvale prázdné odpovědi ověřte KSNKO ID konkrétního kontejneru, nikoli ID stanoviště. Viz také [upstream issue #16](https://github.com/Cmajda/ha_golemio/issues/16).
- Při HTTP 401 ověřte token. Podrobnosti o dalších chybách jsou v protokolu Home Assistant pod `custom_components.golemio`.

## Development checks

The focused regression tests use the real Home Assistant sensor classes, registries, and state machine. Run them in the same container image as CI:

```sh
docker run --rm --network none -v "$(pwd):/workspace:ro" -w /workspace --entrypoint python ghcr.io/home-assistant/home-assistant:2026.10.2 -m unittest discover -s tests -v
```

The tests use synthetic IDs and mock HTTP responses. No API token or live Home Assistant configuration is required.
