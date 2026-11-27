// BAKO production monitoring gateway (spec 4.1) for the ISS board:
// ESP32-DevKitC + MCP2515/TJA1051T + analogue inputs + DS18B20 + SIM cellular module.
//
//   CAN reader     MCP2515 at 250 kbit/s, normal mode: acknowledges, never transmits (config.h)
//   BMS decoder    pack, cell extremes and temperature values for the alert rules
//   Sensor reader  analogue inputs, handbrake line, DS18B20 probes every 2 s
//   Uplink         Wi-Fi TCP to the backend, one line per message (spec 6.1)
//   Alert engine   local rules, SMS through the cellular module, 15 min rate limit
//   Status LEDs    LED1 connectivity, LED2 system health
//
// Uplink lines:   HELLO <id> | Extended ID: 0x.. DLC: n Data: 0x.. | SENSOR_JSON:{..}
//                 ALERT:{"rule":..,"sms":..,"msg":..} | SMS_RESULT <id> OK|ERR | # status
// Downlink lines: TIME <unix> | SMS <id> <phone> <text> | CONFIG {json}
// No buffering: data is lost while Wi-Fi is down (spec section 7).

#include <Arduino.h>
#include <SPI.h>
#include <WiFi.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include "src/mcp_can/mcp_can.h"
#include "config.h"

MCP_CAN can0(MCP_CS_PIN);
OneWire oneWire(PIN_ONEWIRE);
DallasTemperature probes(&oneWire);
WiFiClient up;
HardwareSerial sim(2);

// ---- state --------------------------------------------------------------------
static bool canOk = false;
static uint32_t framesSeen = 0, framesSent = 0, lastBmsMs = 0, lastSensorMs = 0, lastStatusMs = 0;
static uint32_t lastConnectTryMs = 0;
static bool bmsSeen = false;
static int64_t unixOffset = 0;           // unix time = millis()/1000 + offset (from TIME)
static String supervisorPhone = SUPERVISOR_PHONE;
static String downBuf;

struct Battery {
  uint8_t faultLevel = 0, errorCode = 0, soc = 0;
  float packV = 0, currentA = 0;
  uint16_t cellMax = 0, cellMin = 0;
  int16_t tempMax = -100;
  int16_t probeTemps[8];
  bool valid = false;
} bat;

struct Sensors {
  float v12 = 0, v72 = 0, vMppt = 0, iIn = 0, iOut = 0, vHandbrake = 0;
  bool handbrake = false;
  float temps[3] = {NAN, NAN, NAN};
} sen;

// Alert thresholds (CONFIG from the backend can change them)
static float dcdcMin = DCDC_MIN_V, dcdcMax = DCDC_MAX_V, tempWarn = TEMP_WARNING_C, tempCrit = TEMP_CRITICAL_C;
static uint32_t bmsSilentMs = BMS_SILENT_MS, smsRateMs = SMS_RATE_LIMIT_MS;

enum Rule { R_BMS_FAULT, R_BMS_SILENT, R_DCDC, R_TEMP_CRIT, R_TEMP_WARN, R_COUNT };
static const char *RULE_NAME[R_COUNT] = {"bms_fault", "bms_silent", "dcdc_out_of_range", "temp_critical", "temp_warning"};
static const bool RULE_SMS[R_COUNT] = {true, true, true, true, false};
static bool ruleActive[R_COUNT] = {false};
static uint32_t lastSmsMs[R_COUNT] = {0};
static bool lastSmsValid[R_COUNT] = {false};

// ---- helpers ----------------------------------------------------------------------
static inline uint16_t le16(const uint8_t *b, int i) { return b[i] | (b[i + 1] << 8); }

static bool uplinkUp() { return WiFi.status() == WL_CONNECTED && up.connected(); }

static void sendLine(const String &s) {
  if (!uplinkUp()) return;               // no buffering by design
  up.print(s);
  up.print('\n');
}

static float readVolts(int pin, float ratio) {
  uint32_t mv = 0;
  for (int i = 0; i < 16; i++) mv += analogReadMilliVolts(pin);
  return (mv / 16.0f) / 1000.0f * ratio;
}

static float readAmps(int pin) {
  uint32_t mv = 0;
  for (int i = 0; i < 16; i++) mv += analogReadMilliVolts(pin);
  return ((mv / 16.0f) - ACS_ZERO_MV) / ACS_MV_PER_A;
}

static String jsonNum(float v, int digits = 2) {
  if (isnan(v)) return "null";
  return String(v, digits);
}

// ---- SMS (SIM800L / SIM7670E AT commands) ----------------------------------------------------
static bool simCommand(const char *cmd, const char *expect, uint32_t timeoutMs) {
  while (sim.available()) sim.read();
  if (cmd) sim.println(cmd);
  String got;
  uint32_t t0 = millis();
  while (millis() - t0 < timeoutMs) {
    while (sim.available()) {
      got += (char)sim.read();
      if (got.indexOf(expect) >= 0) return true;
      if (got.indexOf("ERROR") >= 0) return false;
    }
    delay(5);
  }
  return false;
}

// Blocks for a few seconds; a few CAN frames may be lost (spec section 7).
static bool sendSms(const String &phone, String text) {
#if SMS_ENABLED
  if (phone.length() < 6) return false;
  if (text.length() > 160) text = text.substring(0, 157) + "...";
  if (!simCommand("AT+CMGF=1", "OK", 1000)) return false;
  String cmd = "AT+CMGS=\"" + phone + "\"";
  if (!simCommand(cmd.c_str(), ">", 3000)) return false;
  sim.print(text);
  sim.write(26);  // Ctrl+Z
  return simCommand(nullptr, "+CMGS", 15000);
#else
  return false;
#endif
}

static String clockText() {
  if (unixOffset == 0) return "";
  time_t t = (time_t)(millis() / 1000 + unixOffset) + 3600;  // Tunis, UTC+1
  struct tm tmv;
  gmtime_r(&t, &tmv);
  char buf[8];
  snprintf(buf, sizeof buf, " %02d:%02d", tmv.tm_hour, tmv.tm_min);
  return String(buf);
}

// ---- alert engine ------------------------------------------------------------------------------
static void setRule(Rule r, bool active, const String &msg) {
  if (active && !ruleActive[r]) {
    bool sms = false;
    if (RULE_SMS[r] && (!lastSmsValid[r] || millis() - lastSmsMs[r] >= smsRateMs)) {
      sms = sendSms(supervisorPhone, "BAKO " GATEWAY_ID ": " + msg + clockText());
      if (sms) { lastSmsMs[r] = millis(); lastSmsValid[r] = true; }
    }
    String m = msg;
    m.replace("\"", "'");
    sendLine(String("ALERT:{\"rule\":\"") + RULE_NAME[r] + "\",\"sms\":" + (sms ? "true" : "false") +
             ",\"msg\":\"" + m + "\"}");
  }
  ruleActive[r] = active;
}

static void evaluateRules() {
  if (bat.valid) {
    setRule(R_BMS_FAULT, bat.faultLevel != 0 || bat.errorCode != 0,
            "BMS fault level " + String(bat.faultLevel) + " code " + String(bat.errorCode));
  }
  if (bmsSeen) {
    uint32_t age = millis() - lastBmsMs;
    setRule(R_BMS_SILENT, age > bmsSilentMs, "BMS silent for " + String(age / 1000) + " s");
  }
  bool present = sen.v72 >= DCDC_PRESENT_V72 || sen.v12 >= DCDC_PRESENT_V12;
  setRule(R_DCDC, present && (sen.v12 < dcdcMin || sen.v12 > dcdcMax),
          "DC-DC 12V output " + String(sen.v12, 2) + " V");
  float hottest = -100;
  for (float t : sen.temps) if (!isnan(t) && t > hottest) hottest = t;
  if (bat.tempMax > hottest) hottest = bat.tempMax;
  for (int16_t t : bat.probeTemps) if (t > hottest) hottest = t;
  setRule(R_TEMP_CRIT, hottest >= tempCrit, "Temperature critical " + String(hottest, 0) + " C");
  setRule(R_TEMP_WARN, hottest >= tempWarn && hottest < tempCrit, "Temperature warning " + String(hottest, 0) + " C");
}

// ---- BMS decoder (same layouts as src/can_decoder.py) -------------------------------------------------
static void decodeBms(uint32_t id, const uint8_t *d, uint8_t len) {
  if (len < 8) return;
  switch (id) {
    case 0x18FF28F4:
      bat.soc = d[1];
      bat.currentA = ((int32_t)le16(d, 2) - 5000) * 0.1f;
      bat.packV = le16(d, 4) * 0.1f;
      bat.faultLevel = d[6];
      bat.errorCode = d[7];
      bat.valid = true;
      break;
    case 0x18FE28F4:
      bat.cellMax = le16(d, 0);
      bat.cellMin = le16(d, 2);
      bat.tempMax = d[4] == 0xFF ? -100 : (int16_t)d[4] - 40;
      break;
    case 0x18B428F4:
      for (int i = 0; i < 8; i++) bat.probeTemps[i] = d[i] == 0xFF ? -100 : (int16_t)d[i] - 40;
      break;
  }
}

static bool isBms(uint32_t id) {
  switch (id) {
    case 0x18FF28F4: case 0x18FE28F4: case 0x18C828F4: case 0x18C928F4: case 0x18CA28F4:
    case 0x18CB28F4: case 0x18CC28F4: case 0x18B428F4: case 0x18FFE5F4: return true;
  }
  return false;
}

// ---- CAN reader -----------------------------------------------------------------------------------------
static void readCan() {
  if (!canOk) return;
#if MCP_INT_PIN >= 0
  if (digitalRead(MCP_INT_PIN) == HIGH) return;   // INT is active low
#endif
  int n = 0;
  while (can0.checkReceive() == CAN_MSGAVAIL && n++ < 32) {
    unsigned long id;               // INT32U in mcp_can
    uint8_t ext, dlc, buf[8];
    if (can0.readMsgBuf(&id, &ext, &dlc, buf) != CAN_OK) break;
    if (dlc > 8) continue;
    id &= 0x1FFFFFFF;
    framesSeen++;
    if (isBms(id)) {
      decodeBms(id, buf, dlc);
      lastBmsMs = millis();
      bmsSeen = true;
    }
    char line[96];
    int p = snprintf(line, sizeof line, "%s ID: 0x%08lX DLC: %u Data:", ext ? "Extended" : "Standard",
                     (unsigned long)id, dlc);
    for (uint8_t i = 0; i < dlc; i++) p += snprintf(line + p, sizeof line - p, " 0x%02X", buf[i]);
    if (uplinkUp()) {
      up.print(line);
      up.print('\n');
      framesSent++;
    }
  }
}

// ---- sensor reader ----------------------------------------------------------------------------------------
static int probeSlot(const DeviceAddress a, int discoveryIndex) {
  bool mapped = false;
  for (size_t i = 0; i < sizeof(PROBES) / sizeof(PROBES[0]); i++) {
    bool zero = true;
    for (int b = 0; b < 8; b++) if (PROBES[i].addr[b]) zero = false;
    if (!zero) mapped = true;
    if (!zero && memcmp(PROBES[i].addr, a, 8) == 0) return i;
  }
  if (!mapped && discoveryIndex < 3) return discoveryIndex;   // not mapped yet: discovery order
  return -1;
}

static void readSensors() {
  sen.v12 = readVolts(PIN_V12_DC_OUT, DIV_V12);
  sen.v72 = readVolts(PIN_V72_DC_IN, DIV_V72);
  sen.vMppt = readVolts(PIN_V72_MPPT_IN, DIV_MPPT);
  sen.iIn = readAmps(PIN_CURRENT_IN);
  sen.iOut = readAmps(PIN_CURRENT_OUT);
  sen.vHandbrake = readVolts(PIN_HANDBRAKE, DIV_HANDBRAKE);
  sen.handbrake = sen.vHandbrake >= HANDBRAKE_ON_V;

  // Temperatures come from the conversion requested at the end of the previous
  // cycle (non-blocking), so CAN frames are not lost while the probes convert.
  for (int i = 0; i < 3; i++) sen.temps[i] = NAN;
  int count = probes.getDeviceCount();
  for (int i = 0; i < count; i++) {
    DeviceAddress a;
    if (!probes.getAddress(a, i)) continue;
    int slot = probeSlot(a, i);
    float t = probes.getTempC(a);
    if (slot >= 0 && t != DEVICE_DISCONNECTED_C) sen.temps[slot] = t;
  }

  String j = "SENSOR_JSON:{\"v12_dc_out\":" + jsonNum(sen.v12) + ",\"v72_dc_in\":" + jsonNum(sen.v72, 1) +
             ",\"v72_mppt_in\":" + jsonNum(sen.vMppt, 1) + ",\"current_in\":" + jsonNum(sen.iIn) +
             ",\"current_out\":" + jsonNum(sen.iOut) + ",\"v12_handbrake\":" + jsonNum(sen.vHandbrake) +
             ",\"temp_mppt\":" + jsonNum(sen.temps[0], 1) + ",\"temp_dcdc\":" + jsonNum(sen.temps[1], 1) +
             ",\"temp_motor\":" + jsonNum(sen.temps[2], 1) + ",\"handbrake\":" + (sen.handbrake ? "1" : "0") + "}";
  sendLine(j);
  probes.requestTemperatures();                       // returns at once; ready well before the next cycle
}

// ---- uplink and downlink ----------------------------------------------------------------------------------------
static void connectUplink() {
  if (WiFi.status() != WL_CONNECTED || up.connected()) return;
  if (millis() - lastConnectTryMs < 3000) return;
  lastConnectTryMs = millis();
  if (up.connect(BACKEND_HOST, BACKEND_TCP_PORT, 2000)) {
    up.setNoDelay(true);
    sendLine("HELLO " GATEWAY_ID);
    Serial.println("# uplink connected");
  }
}

static float jsonField(const String &j, const char *key, float fallback) {
  int k = j.indexOf(String("\"") + key + "\":");
  if (k < 0) return fallback;
  int s = j.indexOf(':', k) + 1;
  if (j.substring(s, s + 4) == "null") return fallback;
  return j.substring(s).toFloat();
}

static void handleDownlink(const String &line) {
  if (line.startsWith("TIME ")) {
    unixOffset = (int64_t)line.substring(5).toInt() - (int64_t)(millis() / 1000);
  } else if (line.startsWith("SMS ")) {
    int a = line.indexOf(' ', 4), b = line.indexOf(' ', a + 1);
    if (a < 0 || b < 0) return;
    String id = line.substring(4, a), phone = line.substring(a + 1, b), text = line.substring(b + 1);
    bool ok = sendSms(phone, text);
    sendLine("SMS_RESULT " + id + (ok ? " OK" : " ERR"));
  } else if (line.startsWith("CONFIG ")) {
    String j = line.substring(7);
    dcdcMin = jsonField(j, "dcdc_min_v", dcdcMin);
    dcdcMax = jsonField(j, "dcdc_max_v", dcdcMax);
    tempWarn = jsonField(j, "temp_warning_c", tempWarn);
    tempCrit = jsonField(j, "temp_critical_c", tempCrit);
    bmsSilentMs = (uint32_t)(jsonField(j, "bms_silent_s", bmsSilentMs / 1000.0f) * 1000);
    smsRateMs = (uint32_t)(jsonField(j, "sms_rate_limit_min", smsRateMs / 60000.0f) * 60000);
    int p = j.indexOf("\"phones\":[\"");
    if (p >= 0) {
      int s = p + 11, e = j.indexOf('"', s);
      if (e > s) supervisorPhone = j.substring(s, e);
    }
    sendLine("# config applied");
  }
}

static void readDownlink() {
  while (up.available()) {
    char c = up.read();
    if (c == '\n') {
      downBuf.trim();
      if (downBuf.length()) handleDownlink(downBuf);
      downBuf = "";
    } else if (downBuf.length() < 400) {
      downBuf += c;
    }
  }
}

// ---- LEDs ------------------------------------------------------------------------------------------------------
static void updateLeds() {
  uint32_t t = millis();
  // LED1 connectivity: on = backend connected, slow blink = Wi-Fi only, off = no Wi-Fi
  bool led1 = uplinkUp() ? true : (WiFi.status() == WL_CONNECTED ? (t / 500) % 2 : false);
  // LED2 health: on = CAN ok and BMS talking, fast blink = alert active, slow blink = no BMS data / CAN fault
  bool anyAlert = false;
  for (int r = 0; r < R_COUNT; r++) anyAlert |= ruleActive[r];
  bool led2;
  if (anyAlert) led2 = (t / 125) % 2;
  else if (canOk && bmsSeen && t - lastBmsMs < bmsSilentMs) led2 = true;
  else led2 = (t / 1000) % 2;
  digitalWrite(PIN_LED1, led1);
  digitalWrite(PIN_LED2, led2);
}

// ---- setup / loop --------------------------------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("# BAKO gateway " GATEWAY_ID " starting");
  pinMode(PIN_LED1, OUTPUT);
  pinMode(PIN_LED2, OUTPUT);
#if MCP_INT_PIN >= 0
  pinMode(MCP_INT_PIN, INPUT_PULLUP);
#endif
  for (int i = 0; i < 8; i++) bat.probeTemps[i] = -100;
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  for (int tries = 0; tries < 5 && !canOk; tries++) {
    canOk = can0.begin(MCP_ANY, CAN_SPEED, MCP_CRYSTAL) == CAN_OK;
    if (!canOk) { Serial.println("# ERROR: MCP2515 not responding, retrying"); delay(500); }
  }
  if (canOk) {
    can0.setMode(CAN_MODE);
    Serial.println(CAN_MODE == MCP_LISTENONLY ? "# CAN ready, listen-only" : "# CAN ready, normal mode (acknowledging)");
  }

  probes.begin();
  probes.setResolution(10);
  probes.setWaitForConversion(false);
  for (int i = 0; i < probes.getDeviceCount(); i++) {
    DeviceAddress a;
    if (!probes.getAddress(a, i)) continue;
    Serial.printf("# probe %d address {0x%02X,0x%02X,0x%02X,0x%02X,0x%02X,0x%02X,0x%02X,0x%02X}\n", i,
                  a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7]);
  }

#if SMS_ENABLED
  sim.begin(SIM_BAUD, SERIAL_8N1, SIM_RX_PIN, SIM_TX_PIN);
  if (!simCommand("AT", "OK", 1000)) Serial.println("# WARNING: cellular module not answering");
#endif

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void loop() {
  readCan();
  connectUplink();
  if (up.connected()) readDownlink();

  uint32_t now = millis();
  if (now - lastSensorMs >= SENSOR_PERIOD_MS) {
    lastSensorMs = now;
    readSensors();
  }
  evaluateRules();
  updateLeds();

  if (now - lastStatusMs >= 10000) {
    lastStatusMs = now;
    char s[120];
    snprintf(s, sizeof s, "# status frames=%lu sent=%lu can=%d wifi=%d rssi=%d", (unsigned long)framesSeen,
             (unsigned long)framesSent, canOk, WiFi.status() == WL_CONNECTED, WiFi.RSSI());
    Serial.println(s);
    sendLine(s);
  }
}
