/*
 * Smart Parking Monitoring System - Arduino UNO Sketch
 * 
 * Hardware Wiring:
 * - IR1 OUT -> Arduino Digital Pin 2 (Desk 1)
 * - IR2 OUT -> Arduino Digital Pin 3 (Desk 2)
 * - IR3 OUT -> Arduino Digital Pin 4 (Desk 3)
 * - IR4 OUT -> Arduino Digital Pin 5 (Desk 4)
 * - VCC     -> Arduino 5V
 * - GND     -> Arduino GND
 *
 * Logic:
 * - Sensor Output 1 = physically UNOCCUPIED (no obstacle detected)
 * - Sensor Output 0 = physically OCCUPIED (obstacle/car detected)
 *
 * Serial Protocol:
 * - Baud rate: 9600
 * - Format: IR1:1,IR2:1,IR3:0,IR4:1 followed by \r\n
 * - Transmit interval: 500 ms
 * - Sends initial reading immediately upon startup.
 * - Machine-readable only (no debug text).
 */

const int PIN_IR1 = 2; // Desk 1
const int PIN_IR2 = 3; // Desk 2
const int PIN_IR3 = 4; // Desk 3
const int PIN_IR4 = 5; // Desk 4

const unsigned long TRANSMIT_INTERVAL_MS = 500;
unsigned long lastTransmitTime = 0;

void transmitReadings() {
  int ir1 = digitalRead(PIN_IR1);
  int ir2 = digitalRead(PIN_IR2);
  int ir3 = digitalRead(PIN_IR3);
  int ir4 = digitalRead(PIN_IR4);

  // Exact format expected by backend parser:
  // IR1:<0|1>,IR2:<0|1>,IR3:<0|1>,IR4:<0|1>
  Serial.print("IR1:");
  Serial.print(ir1);
  Serial.print(",IR2:");
  Serial.print(ir2);
  Serial.print(",IR3:");
  Serial.print(ir3);
  Serial.print(",IR4:");
  Serial.println(ir4);
}

void setup() {
  // Configure sensor pins as inputs
  pinMode(PIN_IR1, INPUT);
  pinMode(PIN_IR2, INPUT);
  pinMode(PIN_IR3, INPUT);
  pinMode(PIN_IR4, INPUT);

  // Initialize USB serial communication
  Serial.begin(9600);
  
  // Wait brief moment for serial connection to stabilize
  delay(100);

  // Transmit initial reading immediately upon startup
  transmitReadings();
  lastTransmitTime = millis();
}

void loop() {
  unsigned long currentMillis = millis();

  // Transmit complete readings every 500 milliseconds
  if (currentMillis - lastTransmitTime >= TRANSMIT_INTERVAL_MS) {
    lastTransmitTime = currentMillis;
    transmitReadings();
  }
}
