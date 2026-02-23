import os
import json
import csv
import time
import struct
import logging
from datetime import datetime
from pymodbus.client import ModbusTcpClient
from pymodbus import FramerType, ModbusException
from flask import Flask, jsonify
import sqlite3
import threading
# ---------------------------------------------------------------------
# Configuration & Global State
# ---------------------------------------------------------------------

settingsFile = os.path.join(os.path.dirname(__file__), "settings.json")
lastKnownValues = {}  # Cache for ASCII-verdier (f.eks. navn)
lastReadTime = {}     # Tidspunkt for siste lesing av spesifikke registre
latestReadings = {}

# Default settings matching the provided JSON structure
settings = {
    "discordWebhook": "",
    "pollInterval": 80.0,
    "alertCooldown": 300,
    "asciiReadInterval": 600,
    "thresholds": {
        "voltage": { "low": 200, "high": 250 },
        "current": { "high": 1 }
    },
    "storage": {
        "databaseConnection": "",
        "csvFile": "powerData.csv"
    },
    "modbus": {
        "gateways":[
          { 
            "ip": "fe80::200:54ff:fee9:3aee",
            "name": "Gateway1"
          },
        ],
        "port": 502,
        "retries": 3,
        "retryDelay": 0.3
    },
    "powertags": [],
    "registerMap": {}
}

# Load configuration from file
if os.path.exists(settingsFile):
    try:
        with open(settingsFile, "r") as f:
            fileSettings = json.load(f)
            settings.update(fileSettings)
    except Exception as e:
        print(f"Failed to load settings.json: {e}")

# Mapping variables for easier access
discordWebhookUrl = settings["discordWebhook"]
pollInterval = settings["pollInterval"]
alertCooldown = settings["alertCooldown"]
asciiReadInterval = settings["asciiReadInterval"]
csvFile = settings["storage"]["csvFile"]
databaseUrl = settings["storage"]["databaseConnection"]
registerMap = settings["registerMap"]
powertags = settings["powertags"]
thresholds = settings["thresholds"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

app = Flask(__name__)

# ---------------------------------------------------------------------
# Discord Notifier
# ---------------------------------------------------------------------

class PowerTagDiscordNotifier:
    """ Summary of function: Handles Discord webhook integration for alerts and status """
    
    def __init__(self, webhookUrl):
        self.webhookUrl = webhookUrl
        self.enabled = False
        self._Initialize()
    
    def _Initialize(self):
        if not self.webhookUrl:
            logging.warning("No Discord webhook URL provided - Discord notifications disabled")
            return
        
        try:
            from discord_webhook import DiscordWebhook, DiscordEmbed
            
            # Create initialization message with limits
            content = "🟢 **PowerTag Monitor initializing...**"
            webhook = DiscordWebhook(url=self.webhookUrl, content=content)
            
            embed = DiscordEmbed(title="System Limits Configured", color=3447003)
            embed.add_embed_field(name="Voltage Thresholds", value=f"Low: {thresholds['voltage']['low']}V\nHigh: {thresholds['voltage']['high']}V")
            embed.add_embed_field(name="Current Threshold", value=f"High: {thresholds['current']['high']}A")
            embed.set_timestamp()
            
            webhook.add_embed(embed)
            response = webhook.execute()
            
            if response and response.status_code == 200:
                self.enabled = True
                logging.info("✅ Discord notifier initialized successfully")
        except Exception as e:
            logging.error(f"❌ Failed to initialize Discord notifier: {e}")
            self.enabled = False
        
    def SendEmbed(self, embedData):
        if not self.enabled:
            return False
        
        try:
            from discord_webhook import DiscordWebhook, DiscordEmbed
            webhook = DiscordWebhook(url=self.webhookUrl, username="PowerTag Monitor")
            embed = DiscordEmbed(
                title=embedData.get('title', ''),
                description=embedData.get('description', ''),
                color=embedData.get('color', 3447003)
            )
            for field in embedData.get('fields', []):
                embed.add_embed_field(name=field['name'], value=field['value'], inline=field.get('inline', False))
            
            embed.set_timestamp()
            webhook.add_embed(embed)
            webhook.execute()
        except Exception as e:
            logging.error(f"Failed to send Discord embed: {e}")

    def SendStatus(self, statusType, title, description):
        colors = {'success': 3066993, 'error': 15158332, 'shutdown': 10181046}
        self.SendEmbed({
            "title": title,
            "description": description,
            "color": colors.get(statusType, 3447003),
            "fields": [{"name": "Time", "value": datetime.now().strftime("%H:%M:%S")}]
        })

# ---------------------------------------------------------------------
# Modbus & Storage Layer
# ---------------------------------------------------------------------

class ModbusReader:
    """ Summary of function: Handles dynamic Modbus register communication """
    def __init__(self, client, retries=3, delay=0.3):
        self.client = client
        self.retries = retries
        self.delay = delay

    def ReadRegisters(self, address, deviceId, count=2, registerType="input"):
        for _ in range(self.retries):
            try:
                if registerType == "input":
                    response = self.client.read_input_registers(address=address, count=count, device_id=deviceId)
                else:
                    response = self.client.read_holding_registers(address=address, count=count, device_id=deviceId)

                if response and not response.isError():
                    return response.registers[:count]
                time.sleep(self.delay)
            except Exception:
                time.sleep(self.delay)
        return None

    def ReadFloat(self, address, deviceId, length):
        registers = self.ReadRegisters(address, deviceId, count=length, registerType="input")
        if not registers: return None
        return round(struct.unpack(">f", struct.pack(">HH", registers[0], registers[1]))[0], 2)

    def ReadAscii(self, address, deviceId, length):
        registers = self.ReadRegisters(address, deviceId, length, "holding")
        if not registers: return None
        rawBytes = bytearray()
        for reg in registers:
            rawBytes.extend([(reg >> 8) & 0xFF, reg & 0xFF])
        return rawBytes.rstrip(b"\x00").decode("ascii", errors="ignore")

def InitStorage():
    """ Summary of function: Prepares CSV storage. Database logic can be expanded here """
    if not os.path.exists(csvFile):
        with open(csvFile, "w", newline="") as file:
            writer = csv.writer(file)
            # Dynamic header based on registerMap keys
            headers = ["Tag", "Timestamp"] + list(registerMap.keys())
            writer.writerow(headers)

def AppendData(rowDict):
    """ Summary of function: Appends a dictionary of readings to the CSV file """
    fileExisted = os.path.exists(csvFile)
    headers = ["Tag", "Timestamp"] + list(registerMap.keys())
    
    with open(csvFile, "a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        if not fileExisted:
            writer.writeheader()
        writer.writerow(rowDict)

# ---------------------------------------------------------------------
# Business Logic
# ---------------------------------------------------------------------

def CheckAlerts(readings):
    """ Summary of function: Checks voltage and current against thresholds """
    alerts = []
    thresholds = settings["thresholds"]
    
    voltage = readings.get("voltage", "float")
    current = readings.get("current", "float")

    if voltage:
        if voltage > float(thresholds["voltage"]["high"]): alerts.append(f"HIGH VOLTAGE: {voltage}V")
        elif voltage < float(thresholds["voltage"]["low"]): alerts.append(f"LOW VOLTAGE: {voltage}V")
    
    if current and current > float(thresholds["current"]["high"]):
        alerts.append(f"HIGH CURRENT: {current}A")
        
    return alerts

def Run():
    """ Summary of function: Main dynamic loop that also updates the global latestReadings object """
    global latestReadings
    InitStorage()
    
    gateWays = settings["modbus"]["gateways"]
    modbusClients = {}
    modbusReaders = {}
    
    for gateway in gateWays:
        gatewayIp = gateway["ip"]
        gatewayName = gateway["name"]
        
        client = ModbusTcpClient(gatewayIp, port=settings["modbus"]["port"], framer=FramerType.SOCKET)
        
        modbusClients[gatewayName] = client
    
        if not client.connect():
            logging.error("Modbus connection failed")
            return
    
        reader = ModbusReader(client, settings["modbus"]["retries"], settings["modbus"]["retryDelay"])
        
        modbusReaders[gatewayName] = reader
    
    discord = PowerTagDiscordNotifier(discordWebhookUrl)
    lastAlertTime = {}

    try:
        while True:
            cycleStart = time.time()
            # Midlertidig dict for å holde denne syklusens data
            currentCycleData = {}

            for tagInfo in powertags:
                deviceId = tagInfo["deviceId"]
                tagName = tagInfo["tagName"]
                gatewayName = tagInfo["gatewayName"]
                
                currentRow = {"Tag": tagName, "Timestamp": cycleStart}
                
                # Dynamisk avlesning av alle registre i registerMap
                for key, config in registerMap.items():
                    regAddr = config["register"]
                    regType = config.get("type", "float")
                    registerLength = config.get("length", "int")
                    
                    registerReader = modbusReaders[gatewayName]
                    
                    if regType == "float":
                        val = registerReader.ReadFloat(regAddr, deviceId, registerLength)
                    elif regType == "ascii":
                        cacheKey = f"{tagName}_{key}"
                        if cycleStart - lastReadTime.get(cacheKey, 0) > asciiReadInterval:
                            val = registerReader.ReadAscii(regAddr, deviceId, config["length"])
                            if val:
                                lastKnownValues[cacheKey] = val
                                lastReadTime[cacheKey] = cycleStart
                        val = lastKnownValues.get(cacheKey, "Unknown")
                    else:
                        val = None
                    
                    currentRow[key] = val

                # 1. Oppdater global tilstand for API-et
                currentCycleData[tagName] = currentRow
                
                # 2. Lagre til disk
                AppendData(currentRow)
                
                # 3. Sjekk for varslinger
                alerts = CheckAlerts(currentRow)
                if alerts and (cycleStart - lastAlertTime.get(tagName, 0) > alertCooldown):
                    discord.SendEmbed({
                        "title": f"⚠️ Alert: {tagName}",
                        "description": "\n".join(alerts),
                        "color": 15158332,
                        "fields": [{"name": "Status", "value": f"V: {currentRow.get('voltage')}V, A: {currentRow.get('current')}A"}]
                    })
                    lastAlertTime[tagName] = cycleStart

            # Oppdater den globale variabelen atomisk etter at alle tags er lest
            latestReadings = currentCycleData
            logging.info(f"Cycle completed. {len(latestReadings)} tags updated.")

            elapsed = time.time() - cycleStart
            time.sleep(max(0, pollInterval - elapsed))

    except Exception as e:
        logging.error(f"Error in Run loop: {e}")
    finally:
        client.close()
        
@app.route("/api/powertags", methods=["GET"])
def GetPowertags():
    return jsonify(latestReadings)

if __name__ == "__main__":
    # Monitor thread
    monitorThread = threading.Thread(target=Run, daemon=True)
    monitorThread.start()

    app.run(host="0.0.0.0", port=5000, debug=False)
