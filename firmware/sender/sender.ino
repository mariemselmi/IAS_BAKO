/*
 * BAKO CAN bench sender, ESP32 + 3.3 V CAN transceiver.
 *
 * Replays the real BAKO vehicle capture onto a bench CAN bus with the original
 * timing, so the receiver can be tested on a desk without a car
 * (validation step 2 on the poster: bitrate, network loss, buffering).
 *
 * The frames come from frames.h, generated from the recorded log by:
 *     python tools/make_sender_frames.py
 *
 * Why NO_ACK mode: on a two-node bench the only other node is the receiver,
 * which runs in listen-only mode and never sends the CAN acknowledge bit.
 * A normal transmitter would treat every frame as failed and retry forever.
 * TWAI_MODE_NO_ACK transmits without waiting for an acknowledge.
 * On the real vehicle the car's own modules acknowledge, so this sender is
 * for the bench only and must never be connected to a vehicle.
 *
 * Bench wiring (two ESP32 + two transceivers):
 *   sender GPIO 5 -> TX, GPIO 4 -> RX on its transceiver (same as the receiver)
 *   CAN-H to CAN-H, CAN-L to CAN-L, a short twisted pair
 *   one 120 ohm resistor across CAN-H/CAN-L at EACH end (two in total)
 *   common GND between the two boards
 */

#include "driver/twai.h"
#include "frames.h"

#define CAN_TX_PIN  GPIO_NUM_5
#define CAN_RX_PIN  GPIO_NUM_4
#define SERIAL_BAUD 115200

// Must match the receiver.
#define CAN_BITRATE_250K
// #define CAN_BITRATE_500K

static uint32_t sent = 0, failed = 0, loops = 0;

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);

  twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(CAN_TX_PIN, CAN_RX_PIN, TWAI_MODE_NO_ACK);
  g.tx_queue_len = 16;
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
  Serial.printf("# BAKO bench sender ready, %lu frames per loop\n", (unsigned long)FRAME_COUNT);
}

void loop() {
  for (uint32_t i = 0; i < FRAME_COUNT; i++) {
    const BakoFrame &fr = FRAMES[i];
    if (fr.dt_ms) delay(fr.dt_ms);   // keep the original spacing between frames

    twai_message_t m = {};
    m.extd = 1;                      // BAKO uses 29-bit extended identifiers
    m.identifier = fr.id;
    m.data_length_code = fr.dlc;
    memcpy(m.data, fr.data, fr.dlc);

    if (twai_transmit(&m, pdMS_TO_TICKS(20)) == ESP_OK) sent++;
    else failed++;
  }
  loops++;
  Serial.printf("# loop %lu done, sent=%lu failed=%lu\n",
                (unsigned long)loops, (unsigned long)sent, (unsigned long)failed);
}
