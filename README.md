# Golemio – tříděný odpad (Praha) pro Home Assistant

Integrace sleduje pražská stanoviště tříděného odpadu přes [Golemio API](https://api.golemio.cz/docs/openapi/):
termíny odvozu a u nádob s čidlem i jejich **zaplněnost**.

Fork projektu [Cmajda/ha_golemio](https://github.com/Cmajda/ha_golemio) (přes [MangelSpec/ha_golemio](https://github.com/MangelSpec/ha_golemio)), přepsaný od základu:

- nastavení přes UI, vyhledání stanoviště na mapě,
- zařízení: **stanoviště** a pod ním jednotlivé **nádoby**,
- `percent_calculated` je správně interpretováno jako zaplněnost (0 % = prázdná),
- jeden dotaz na API za stanoviště (dřív jeden za každý senzor),
- kalendář nejbližších odvozů.

> Doména je `golemio_waste`, integrace tedy může běžet vedle původní `golemio`.
> Staré YAML senzory se nepřevádějí, po přechodu původní integraci odinstalujte.

## Instalace

**HACS:** *HACS → Integrace → ⋮ → Vlastní repozitáře* → `https://github.com/janlanger/ha_golemio_waste`, kategorie *Integration*. Pak nainstalovat a restartovat HA.

**Ručně:** zkopírujte `custom_components/golemio_waste` do `config/custom_components/` a restartujte HA.

Vyžaduje Home Assistant 2026.8 nebo novější.

## Nastavení

1. Zaregistrujte se na [api.golemio.cz](https://api.golemio.cz/api-keys/auth/sign-up) a zkopírujte si API token.
2. *Nastavení → Zařízení a služby → Přidat integraci → Golemio Sorted Waste*.
3. Vložte token. Při přidávání dalšího stanoviště se token předvyplní.
4. Najděte stanoviště:
   - **Hledat na mapě:** výchozí je poloha domova, posuňte značku a upravte poloměr. Stanoviště s čidly zaplněnosti jsou označena 📶.
   - **Zadat KSNKO ID:** ID libovolné nádoby na stanovišti nebo stanoviště samotného.

Každé stanoviště je samostatná položka integrace, přidejte jich kolik potřebujete.

### Volby

| Volba | Výchozí | Význam |
|---|---|---|
| Nádoba je plná od | 80 % | Práh pro binární senzor *Plná* |
| Interval aktualizace | 30 min | API data cachuje 30 minut, kratší interval nic nepřinese (minimum 15) |

## Zařízení a entity

```
📍 Přátelství 356/61                     stanoviště
 ├─ Příští odvoz            (datum)      nejbližší odvoz čehokoli, atribut trash_types
 ├─ Odvozy                  (kalendář)   celodenní události „Odvoz: Papír, Plast“
 │
 ├─ 🗑 Plast – Přátelství 356/61         nádoba (připojena přes stanoviště)
 │   ├─ Zaplněnost          62 %         jen nádoby s čidlem
 │   ├─ Plná                on/off       jen nádoby s čidlem, práh v Volbách
 │   ├─ Příští odvoz        datum
 │   ├─ Dny odvozu          „Po, St, Pá“ atributy duration/frequency
 │   ├─ Předpověď zaplnění  čas          predikce dodavatele čidla
 │   ├─ Poslední odvoz      čas
 │   └─ Poslední měření     čas          diagnostika
 └─ 🗑 Papír – Přátelství 356/61 …
```

Je-li na stanovišti víc nádob stejného druhu, čísluje se (*Plast 1*, *Plast 2*).
Nádoby bez čidla mají jen termíny odvozu.

Zaplněnost má `state_class: measurement`, takže se ukládá do dlouhodobých statistik.

Veřejné API dává u každé nádoby jen nejbližší termín odvozu, kalendář proto ukazuje
jen ten. Endpointy `/pickdays`, `/measurements` a `/picks` jsou v Golemiu interní
a běžný token na ně dostane 403, integrace je proto nepoužívá.

### Příklad automatizace

```yaml
automation:
  - alias: Připomenout odvoz papíru
    triggers:
      - trigger: calendar
        event: start
        offset: "-12:00:00"
        entity_id: calendar.pratelstvi_356_61_odvozy
    conditions:
      - condition: template
        value_template: "{{ 'Papír' in trigger.calendar_event.summary }}"
    actions:
      - action: notify.notify
        data:
          message: "Zítra se odváží papír."
```

## Vývoj

Testy používají [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component):

```bash
pip install -r requirements_test.txt
pytest
```

Nebo bez lokálního Pythonu:

```bash
docker run --rm -v "$PWD":/src:ro python:3.14-slim sh -c \
  'pip install -q -r /src/requirements_test.txt && cp -r /src /work && cd /work && pytest -q'
```

## Licence

Apache 2.0, viz [LICENSE](LICENSE).
