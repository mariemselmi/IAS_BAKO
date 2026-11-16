/*
 * BAKO CAN receiver (gateway), ESP32 + 3.3 V CAN transceiver.
 *
 * Reads every frame on the vehicle CAN bus and prints it over USB serial in
 * the same text format as data/raw/bms_log_car_Bako.txt:
 *
 *   [11455ms] ID: 0x18FE28F4  DLC: 8 Data: 44 0D 36 0D 3D 3B E8 03
 *
 * so src/serial_ingest.py (live) and src/ingest.py (recorded) share one parser.
 *
 * The bus is opened in LISTEN-ONLY mode: this node never transmits and never
 * acknowledges frames, so it cannot disturb the vehicle (poster constraint,
 * ISO 11898 listen-only).
 *
 * Hardware:
 *   ESP32 dev board, built-in CAN controller (TWAI), no MCP2515 needed.
 *   SN65HVD230 or TJA1051T/3 transceiver module, powered at 3.3 V.
 *     ESP32 GPIO 5  -> transceiver TX (CTX / D)
 *     ESP32 GPIO 4  -> transceiver RX (CRX / R)
 *     3V3 and GND   -> transceiver VCC and GND
 *     CAN-H / CAN-L -> vehicle bus (or bench bus)
 *   Do NOT add a 120 ohm terminator when tapping the real vehicle bus:
 *   it is already terminated at both ends.
 *
 * Build: Arduino IDE or arduino-cli, board "ESP32 Dev Module", core esp32 2.x or 3.x.
 */

#include "driver/twai.h"

// ---- Configuration ----------------------------------------------------------
#define CAN_TX_PIN   GPIO_NUM_5
#define CAN_RX_PIN   GPIO_NUM_4
#define SERIAL_BAUD  921600   // fast enough for a busy bus; match src/serial_ingest.py

// Bus speed. CONFIRM WITH BAKO BEFORE THE FIRST LIVE CAPTURE:
// the ISS repository says 250 kbit/s, BAKO_CAN_Protocol_v2.docx says 500 kbit/s.
// With the wrong speed you will see no frames, only bus errors.
#define CAN_BITRATE_250K
// #define CAN_BITRATE_500K
// ----------------------------------------------------------------------------

static uint32_t framesSeen = 0;
static uint32_t lastStatusMs = 0;

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);

  twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(CAN_TX_PIN, CAN_RX_PIN, TWAI_MODE_LISTEN_ONLY);
  g.rx_queue_len = 64;   // absorb bursts while serial is busy

#if defined(CAN_BITRATE_500K)
  twai_timing_config_t t = TWAI_TIMING_CONFIG_500KBITS();
#else
  twai_timing_config_t t = TWAI_TIMING_CONFIG_250KBITS();
#endif
  twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();

  if (twai_driver_install(&g, &t, &f) != ESP_OK || twai_start() != ESP_OK) {
    Serial.println("# ERROR: TWAI driver failed to start");
    while (true) delay(1000);
  }
  Serial.println("# BAKO CAN receiver ready, listen-only");
}

void loop() {
  twai_message_t msg;
  if (twai_receive(&msg, pdMS_TO_TICKS(50)) == ESP_OK) {
    if (!msg.rtr && msg.data_length_code > 0) {
      framesSeen++;
      // Same layout as the recorded log, parsed by src/replay.py FRAME_RE.
      Serial.printf("[%lums] ID: 0x%08lX  DLC: %d Data:",
                    (unsigned long)millis(),
                    (unsigned long)msg.identifier,
                    msg.data_length_code);
      for (int i = 0; i < msg.data_length_code; i++) {
        Serial.printf(" %02X", msg.data[i]);
      }
      Serial.print("\n");
    }
  }

  // Health line every 5 s. Lines starting with '#' are ignored by the parser.
  uint32_t now = millis();
  if (now - lastStatusMs >= 5000) {
    lastStatusMs = now;
    twai_status_info_t s;
    twai_get_status_info(&s);
    Serial.printf("# status frames=%lu rx_missed=%lu rx_queued=%lu bus_errors=%lu state=%d\n",
                  (unsigned long)framesSeen,
                  (unsigned long)s.rx_missed_count,
                  (unsigned long)s.msgs_to_rx,
                  (unsigned long)s.bus_error_count,
                  (int)s.state);
  }
}
