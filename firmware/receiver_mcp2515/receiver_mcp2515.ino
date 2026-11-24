#include <SPI.h>
// Bundled, fixed copy of mcp_can 1.5.1 (see src/mcp_can). The stock library
// crashes the ESP32 when a frame's length field reads above 8.
#include "src/mcp_can/mcp_can.h"

// ---- Configuration ----------------------------------------------------------
#define MCP_CS_PIN   5
#define SERIAL_BAUD  115200     // pass --baud 115200 to src/serial_ingest.py

// Crystal printed on the MCP2515 module: "8.000" -> MCP_8MHZ, "16.000" -> MCP_16MHZ
#define MCP_CRYSTAL  MCP_8MHZ

// Bus speed. 250 kbit/s first. If the car shows no frames, try 500, then 125.
#define CAN_SPEED    CAN_250KBPS
// #define CAN_SPEED CAN_500KBPS
// #define CAN_SPEED CAN_125KBPS

// Bus mode.
// MCP_NORMAL: the board acknowledges frames (it still sends no messages of its own).
//   Needed on the BAKO battery's CAN line when no charger is plugged in: nothing
//   else on that line acknowledges, so the battery repeats one frame forever.
// MCP_LISTENONLY: the board stays completely silent. Use it when tapping a line
//   where other modules (charger, dashboard, controller) already acknowledge.
#define CAN_MODE     MCP_NORMAL
// #define CAN_MODE  MCP_LISTENONLY
// ----------------------------------------------------------------------------

MCP_CAN can0(MCP_CS_PIN);

static uint32_t framesSeen = 0;
static uint32_t lastStatusMs = 0;

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(300);
  Serial.println("# BAKO MCP2515 receiver starting");

  while (can0.begin(MCP_ANY, CAN_SPEED, MCP_CRYSTAL) != CAN_OK) {
    Serial.println("# ERROR: MCP2515 not responding (check 12 V on J9 and the module), retrying");
    delay(1000);
  }
  can0.setMode(CAN_MODE);
  Serial.println(CAN_MODE == MCP_LISTENONLY
                 ? "# BAKO MCP2515 receiver ready, listen-only"
                 : "# BAKO MCP2515 receiver ready, normal mode (acknowledging)");
}

void loop() {
  // Empty both receive buffers every pass.
  while (can0.checkReceive() == CAN_MSGAVAIL) {
    uint32_t id;
    uint8_t ext, dlc, buf[8];
    if (can0.readMsgBuf(&id, &ext, &dlc, buf) != CAN_OK) break;
    if (dlc == 0 || dlc > 8) continue;

    framesSeen++;
    Serial.printf("[%lums] ID: 0x%08lX  DLC: %u Data:",
                  (unsigned long)millis(),
                  (unsigned long)(id & 0x1FFFFFFF),
                  dlc);
    for (uint8_t i = 0; i < dlc; i++) Serial.printf(" %02X", buf[i]);
    Serial.print("\n");
  }

  // Health line every 5 s. Lines starting with '#' are ignored by the parser.
  uint32_t now = millis();
  if (now - lastStatusMs >= 5000) {
    lastStatusMs = now;
    Serial.printf("# status frames=%lu rx_errors=%u error_flags=0x%02X\n",
                  (unsigned long)framesSeen,
                  can0.errorCountRX(),
                  can0.getError());
  }
}
