# Modbus Power Monitor - Hordaland Edition

Dette er en lettvektstjeneste utviklet for overvåking av elektriske parametere via Modbus TCP. Systemet er spesialisert for uthenting av data fra Schneider Electric PowerTags via gateway (EGX150, PAS600 etc), med fokus på stabilitet og enkel konfigurasjon.

## Hovedfunksjonalitet

* **Dynamisk Registermapping**: Legg til eller endre Modbus-registre direkte i `settings.json` uten behov for rekompilering eller endring av kildekode.
* **Sanntidsvarsling**: Automatisk utsending av varsler til Discord når spenning eller strøm overstiger definerte grenseverdier.
* **Delt Arkitektur**: Kjører datainnsamling og web-server i separate tråder for å sikre uavhengig drift.
* **REST API**: Innebygd endepunkt som leverer de siste avlesningene i JSON-format for integrasjon mot dashboard eller andre systemer.

## Installasjon og Bruk

Systemet er optimalisert og testet gjennom HordaLAN26 med gode resultater.

1.  **Installer avhengigheter**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Konfigurer enheten**:
    Rediger `settings.json` med korrekt IP-adresse for din Modbus-gateway og definer dine terskelverdier.

3.  **Start overvåking**:
    ```bash
    python main.py
    ```

## Konfigurasjon

Systemet er enkelt bygget opp:
* **main.py**: Programfilen ansvarlig for kjøring av kjerne- og foretningslogikk.
* **settings.json**: Konfigurasjon for programkjøring og databehandling

```json
{
  "discordWebhook": "", //Eventuell kobling mot discord 
  "pollInterval": 80.0, // (s) Forsinkelser for spørringer "modbus inngangsregistere (input)"
  "alertCooldown": 300, // (s) Tid før ny varsling kan gies for samme objekt
  "asciiReadInterval": 600, // (s) Forsinkelser for spørringer "modbus lagrende registere (holding)"

  // Grenseverdier for lav/høy. Nøkkel må være identisk med registrene
  "thresholds": {
    "voltage": { "low": 200, "high": 250 }, 
    "current": { "high": 13 }
  },

  "storage": {
    "databaseConnection": "", // Future-on storage of database connection string
    "csvFile": "powerData.csv" // Name of data storage file
  },

  "modbus": {
    "targetIp": "fe80::200:54ff:fee9:3aee", // Modbus server IP
    "port": 502, // Modbus port
    "retries": 3, // Antall forsøk per spørring ved ugyldig respons
    "retryDelay": 0.3 // Minste forsinkelse mellom spørringer
  },

  "powertags": [
    {
      "deviceId": 101, // Adresse for enhet
      "tagName": "powerTag1" // Nøkkel for objekt
    },...
  ],

  // Registrering av de forskjellige verdiene som skal forespørres
  "registerMap": {
    "voltage": {
      "register": 3027, // Første register for verdi (register = adresse - 1)
      "length": 2, // Lengde for register
      "type": "float", // [float, ascii]
      "registerType": "input" // [input, holding]
    },...
  }
}
