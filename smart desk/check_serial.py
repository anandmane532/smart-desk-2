
import time
import serial

print("Starting Arduino serial test...")

try:
    with serial.Serial("COM8", 9600, timeout=2) as arduino:
        print("Connected to COM8 successfully!")
        time.sleep(2)
        arduino.reset_input_buffer()
        arduino.readline()  # discard any partial first line

        for i in range(10):
            line = arduino.readline().decode(
                "utf-8", errors="replace"
            ).strip()

            if line:
                print("Received:", line)
            else:
                print("No data received in this interval.")

    print("Test finished.")

except serial.SerialException as error:
    print("Serial connection error:", error)
