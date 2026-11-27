// BAKO gateway configuration. Edit before flashing.
// Items marked OPEN refer to the open items in the software specification (section 8).
#pragma once

// ---- Identity and network --------------------------------------------------
#define GATEWAY_ID        "esp32-bms-001"      // must be in the backend gateway inventory
#define WIFI_SSID         "CHANGE_ME"
#define WIFI_PASSWORD     "CHANGE_ME"
#define BACKEND_HOST      "192.168.1.10"       // PC running `python -m backend`
#define BACKEND_TCP_PORT  5000

// ---- CAN (MCP2515 on the ISS board) ----------------------------------------
#define MCP_CS_PIN        5
#define MCP_INT_PIN       -1                  // MCP2515 INT pin if wired, -1 = poll
#define MCP_CRYSTAL       MCP_8MHZ            // module crystal "8.000" (16 MHz module: MCP_16MHZ)
#define CAN_SPEED         CAN_250KBPS         // OPEN 1: 250 kbit/s confirmed on the car 2026-11-23
// Bus mode: MCP_NORMAL (decided 2026-11-24, departs from the spec's listen-only).
// Car test 2026-11-23: with NO charger plugged in, nothing on the battery line
// acknowledges and the BMS repeats one frame forever in listen-only mode.
// In MCP_NORMAL the MCP2515 acknowledges frames; the firmware never sends any
// message of its own (there is no sendMsgBuf call).
// Use MCP_LISTENONLY only where another node (charger, VCU) acknowledges.
#define CAN_MODE          MCP_NORMAL
// #define CAN_MODE       MCP_LISTENONLY

// ---- Board sensors (I2), read every 2 s ------------------------------------
#define SENSOR_PERIOD_MS  2000
#define PIN_V12_DC_OUT    36
#define PIN_V72_DC_IN     39
#define PIN_V72_MPPT_IN   35
#define PIN_CURRENT_IN    33
#define PIN_CURRENT_OUT   32
#define PIN_HANDBRAKE     34
#define PIN_ONEWIRE       15
// Divider ratios (input volts per ADC volt). OPEN 3: measure on the board.
#define DIV_V12           5.70f
#define DIV_V72           30.0f
#define DIV_MPPT          30.0f
#define DIV_HANDBRAKE     5.70f
#define HANDBRAKE_ON_V    6.0f                // line voltage above this = handbrake engaged
// ACS712, output scaled to the ADC. OPEN 3: confirm variant (5 A 185 mV/A, 20 A 100, 30 A 66)
// and the divider in front of the ADC; values here are at the ADC pin.
#define ACS_ZERO_MV       1650.0f
#define ACS_MV_PER_A      66.0f

// DS18B20 probes mapped by address (OPEN 4). Unlisted probes are printed on
// boot ("# probe 28-...") so their address can be copied here.
struct ProbeMap { const char *name; uint8_t addr[8]; };
static const ProbeMap PROBES[] = {
  {"temp_mppt",  {0, 0, 0, 0, 0, 0, 0, 0}},
  {"temp_dcdc",  {0, 0, 0, 0, 0, 0, 0, 0}},
  {"temp_motor", {0, 0, 0, 0, 0, 0, 0, 0}},
};
// While addresses are all zero, probes are assigned in discovery order.

// ---- Alerts and SMS (O2) ----------------------------------------------------
#define SMS_ENABLED       1
#define SIM_RX_PIN        16                  // ESP32 RX <- module TX. OPEN 2: module and pins
#define SIM_TX_PIN        17
#define SIM_BAUD          9600
#define SUPERVISOR_PHONE  "+21600000000"      // overwritten by CONFIG from the backend
#define BMS_SILENT_MS     5000
#define DCDC_MIN_V        11.0f
#define DCDC_MAX_V        14.5f
#define DCDC_PRESENT_V72  40.0f               // converter has its HV input
#define DCDC_PRESENT_V12  3.0f
#define TEMP_WARNING_C    70.0f
#define TEMP_CRITICAL_C   85.0f
#define SMS_RATE_LIMIT_MS (15UL * 60UL * 1000UL)

// ---- Status LEDs -------------------------------------------------------------
#define PIN_LED1          2                   // connectivity. Check the ISS board LED pins.
#define PIN_LED2          4                   // system health
