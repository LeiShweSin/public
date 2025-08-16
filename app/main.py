import time
import threading 
from threading import Thread, Lock
import queue
import cv2
import numpy as np
from picamera2 import Picamera2
from pyzbar import pyzbar
from pyzbar.pyzbar import decode, ZBarSymbol
from datetime import datetime
import os
import requests

# HAL imports
from hal import hal_lcd as LCD
from hal import hal_led as led
from hal import hal_keypad as keypad
from hal import hal_usonic as usonic
from hal import hal_rfid_reader as rfid_reader
from hal import hal_dc_motor as dc_motor
from hal import hal_temp_humidity_sensor as temp_humid_sensor
from hal import hal_buzzer as buzzer


BASE_URL = "http://localhost:80/api"
shared_keypad_queue = queue.Queue()
valid_pin = "1234"
system_state = {
    'power': False,
    'scanning': False,
    'payment_success': False,
    'lock': Lock()
}

system_warning = None

critical_states = {
    "active_alarm": None
}

OVERHEAT_THRESHOLD = 45
HIGH_HUMIDITY_THRESHOLD = 60
MIN_CRITICAL_DURATION = 30

# Scanning variables
total = 0.0
items_scanned = 0
scanned_items = []
system_warn = None

# Debug directory for captured images
DEBUG_DIR = "/home/pi/ET0735/debug_images"
os.makedirs(DEBUG_DIR, exist_ok=True)

FALLBACK_PRODUCTS = {
    "1234567890": ("Milk", 3.00),
    "1111222233": ("Bread", 2.00),
    "6677889900": ("Eggs", 3.50),
    "4444555566": ("Cheese", 4.50),
    "7777888899": ("Butter", 2.75),
    "3333444455": ("Yogurt", 1.25),
    "8888999900": ("Apple", 0.75),
    "2222333344": ("Banana", 0.50),
    "5555666677": ("Orange", 0.85)
}


def key_pressed(key):
    shared_keypad_queue.put(key)

def update_state(key, value):
    with system_state['lock']:
        system_state[key] = value

def power_on_display(lcd):
    lcd.lcd_clear()
    lcd.lcd_display_string("Supermarket", 1)
    lcd.lcd_display_string("Checkout System", 2)
    time.sleep(2)
    update_state('power', True)

def power_off_display(lcd):
    lcd.lcd_clear()
    lcd.lcd_display_string("Shutting Down", 1)
    lcd.lcd_display_string("Thank you!", 2)
    time.sleep(2)
    lcd.lcd_clear()
    update_state('power', False)

def read_pin_input(lcd, digits=4):
    pin = ""
    lcd.lcd_clear()
    lcd.lcd_display_string("Enter PIN:", 1)
    
    while len(pin) < digits:
        if not shared_keypad_queue.empty():
            key = shared_keypad_queue.get()
            if isinstance(key, int) and 0 <= key <= 9:
                pin += str(key)
                lcd.lcd_display_string("*" * len(pin), 2)
    
    return pin

def invalid_barcode_display(lcd):
    lcd.lcd_clear()
    lcd.lcd_display_string("Invalid barcode", 1)
    lcd.lcd_display_string("Try again", 2)
    time.sleep(2)

def update_display(lcd, product, price, total):
    lcd.lcd_clear()
    lcd.lcd_display_string(f"{product[:15]}", 1)
    lcd.lcd_display_string(f"P:${price:.2f} T:${total:.2f}", 2)

def play_overheat_alarm():
    try:
        print("[BUZZER] Playing overheat alarm")
        while critical_states["active_alarm"] == "overheat":
            print("Overheat: Beeping 5 times")
            buzzer.beep(0.2, 0.1, 5)
            time.sleep(1)
    except Exception as e:
        print(f"[BUZZER ERROR] Overheat alarm: {str(e)}")
    finally:
        buzzer.turn_off()

def play_humidity_alarm():
    try:
        print("[BUZZER] Playing humidity alarm")
        while critical_states["active_alarm"] == "high_humidity":
            print("Humidity: Long beep")
            buzzer.beep(0.2, 0, 1)
            time.sleep(1)
            print("Humidity: Silence")
            buzzer.turn_off()
            time.sleep(1)
    except Exception as e:
        print(f"[BUZZER ERROR] Humidity alarm: {str(e)}")
    finally:
        buzzer.turn_off()

def stop_all_buzzers():
    critical_states["active_alarm"] = None
    try:
        buzzer.turn_off()
        print("[BUZZER] All buzzers stopped")
    except Exception as e:
        print(f"[BUZZER ERROR] Stopping buzzers: {str(e)}")

def monitor_environment():
    global system_warn
    
    try:
        temp_humid_sensor.init()
        print("[ENV] Temperature/Humidity sensor initialized")
    except Exception as e:
        print(f"[ENV ERROR] Sensor init: {str(e)}")
    
    try:
        buzzer.init()
        print("[BUZZER] Buzzer initialized")
        buzzer.beep(0.2, 0.1, 2)
        time.sleep(0.5)
        buzzer.turn_off()
    except Exception as e:
        print(f"[BUZZER ERROR] Init: {str(e)}")
    
    last_normal_time = 0
    
    while True:
        try:
            temp, humidity = temp_humid_sensor.read_temp_humidity()
            print(f"[ENV] Temp: {temp:.1f}°C, Humidity: {humidity:.1f}%")
            
            now = time.time()
            current_alarm = None
            
            if temp > OVERHEAT_THRESHOLD:
                current_alarm = "overheat"
                system_warn = f"OVERHEAT: {temp:.1f}C"
            elif humidity > HIGH_HUMIDITY_THRESHOLD:
                current_alarm = "high_humidity"
                system_warn = f"HIGH HUMID: {humidity:.1f}%"
            
            if current_alarm != critical_states["active_alarm"]:
                if current_alarm:
                    print(f"[CRITICAL] Starting {current_alarm} alarm")
                    stop_all_buzzers()
                    critical_states["active_alarm"] = current_alarm
                    
                    if current_alarm == "overheat":
                        threading.Thread(target=play_overheat_alarm, daemon=True).start()
                    else:
                        threading.Thread(target=play_humidity_alarm, daemon=True).start()
                else:
                    if critical_states["active_alarm"]:
                        if now - last_normal_time > MIN_CRITICAL_DURATION:
                            print("[ENV] Conditions normal - stopping alarm")
                            stop_all_buzzers()
                            system_warn = None
            
            if current_alarm is None:
                last_normal_time = now
                
        except Exception as e:
            print(f"[ENV ERROR] Monitoring error: {str(e)}")
        
        time.sleep(5)


def fetch_product_by_barcode(barcode):
    try:
        response = requests.get(f"{BASE_URL}/products/barcode/{barcode}")
        if response.status_code == 200:
            return response.json()  # Return the full product object
        return None
    except Exception as e:
        print(f"[ERROR] Using fallback products: {str(e)}")
        return {'name': FALLBACK_PRODUCTS[barcode][0], 
                'price': FALLBACK_PRODUCTS[barcode][1]} \
            if barcode in FALLBACK_PRODUCTS else None

def make_camera_or_none():
    try:
        cam = Picamera2()
        return cam
    except Exception as e:
        print(f"[CAMERA] Not available: {e}")
        return None

def scan_barcode(lcd):
    global total, items_scanned, scanned_items
    
    lcd.lcd_clear()
    lcd.lcd_display_string("Scanning...", 1)
    lcd.lcd_display_string("Point at barcode", 2)

    # Initialize camera
    picam2 = make_camera_or_none()
    config = picam2.create_preview_configuration(main={"size": (800, 600)})
    picam2.configure(config)
    picam2.start()
    time.sleep(1.5)  # Reduced sleep time for faster capture

    # Capture image
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image = picam2.capture_array()
    picam2.stop()
    picam2.close()

    # Convert to grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    
    # Enhance the image using CLAHE
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # Save the enhanced image for debugging
    debug_path = f"/home/pi/ET0735/debug_images/enhanced_{timestamp}.png"
    cv2.imwrite(debug_path, enhanced)
    print(f"[DEBUG] Saved enhanced image at: {debug_path}")

    # Try different rotations to improve barcode decoding
    rotations = {
        "0": enhanced,
        "90": cv2.rotate(enhanced, cv2.ROTATE_90_CLOCKWISE),
        "180": cv2.rotate(enhanced, cv2.ROTATE_180),
        "270": cv2.rotate(enhanced, cv2.ROTATE_90_COUNTERCLOCKWISE),
    }

    # Attempt barcode decoding on rotated images
    for angle, rotated in rotations.items():
        print(f"[INFO] Trying decode at {angle}° rotation")
        results = decode(rotated, symbols=[ZBarSymbol.EAN13, ZBarSymbol.CODE128])
        
        if results:
            for barcode in results:
                code = barcode.data.decode("utf-8")
                print(f"[INFO] Barcode found: {code}")
                
                product_info = fetch_product_by_barcode(code)
                if product_info:
                    product_name = product_info['name']
                    price = float(product_info['price'])
                    total += price
                    items_scanned += 1
                    scanned_items.append((product_name, price))
                    update_display(lcd, product_name, price, total)
                    time.sleep(1.5)
                    return
                else:
                    print(f"[ERROR] Product not found for barcode: {code}")
    
    # If no barcode found
    print("[FAIL] No barcode could be read.")
    invalid_barcode_display(lcd)

def display_order_items(lcd, items):
    lcd.lcd_clear()
    lcd.lcd_display_string("Order Items:", 1)
    time.sleep(1)
    
    for i, item in enumerate(items):
        lcd.lcd_clear()
        line1 = f"{i+1}/{len(items)}: {item['name'][:10]}"
        line2 = f"Qty: {item['quantity']}"
        lcd.lcd_display_string(line1, 1)
        lcd.lcd_display_string(line2, 2)
        time.sleep(2)

def scan_qr_code(lcd):
    lcd.lcd_clear()
    lcd.lcd_display_string("Scanning QR Code", 1)
    lcd.lcd_display_string("Please wait...", 2)
    print("[INFO] Initializing camera for QR...")

    picam2 = make_camera_or_none()
    config = picam2.create_preview_configuration(main={"size": (1296, 972)})  # Ensure the same size as no.1
    picam2.configure(config)
    picam2.start()
    time.sleep(2)

    # Capture image
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = f"/home/pi/ET0735/debug_images/qrcode_{timestamp}.jpg"
    picam2.capture_file(raw_path)
    picam2.stop()
    picam2.close()

    print(f"[INFO] QR Image captured: {raw_path}")
    image = cv2.imread(raw_path)
    if image is None:
        print("[ERROR] Cannot load QR image.")
        lcd.lcd_display_string("Camera Error", 1)
        return

    # Image Enhancement Step (same as in no.1)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    clahe_img = clahe.apply(gray)
    enhanced = cv2.resize(clahe_img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)

    # Rotate image for better decoding
    rotations = {
        "0": enhanced,
        "90": cv2.rotate(enhanced, cv2.ROTATE_90_CLOCKWISE),
        "180": cv2.rotate(enhanced, cv2.ROTATE_180),
        "270": cv2.rotate(enhanced, cv2.ROTATE_90_COUNTERCLOCKWISE),
    }

    for angle, rotated in rotations.items():
        print(f"[INFO] Trying decode at {angle}° rotation")
        results = decode(rotated, symbols=[ZBarSymbol.QRCODE])
        if results:
            for qr_code in results:
                qr_data = qr_code.data.decode('utf-8')
                print(f"[QR] Decoded data: {qr_data}")

                if "ORD-" in qr_data:
                    lcd.lcd_clear()
                    lcd.lcd_display_string("Order Collected!", 1)
                    lcd.lcd_display_string(qr_data[:16], 2)
                    print(f"[INFO] Order ID: {qr_data} is collected")
                else:
                    lcd.lcd_display_string("QR Invalid", 1)
                    lcd.lcd_display_string("Try again", 2)
                    print("[WARN] QR Code found, but not valid order ID")
                return

    # If no QR code found
    lcd.lcd_clear()
    lcd.lcd_display_string("No QR detected", 1)
    lcd.lcd_display_string("Try again", 2)
    print("[FAIL] QR code could not be read")
    time.sleep(2)


def scan_mode(lcd):
    global total, items_scanned, scanned_items, system_warning
    
    update_state('scanning', True)
    lcd.lcd_clear()
    
    # Display any system warnings first
    if system_warning:
        lcd.lcd_display_string("SYSTEM WARNING!", 1)
        lcd.lcd_display_string(system_warning[:16], 2)
        time.sleep(3)
        system_warning = None
        lcd.lcd_clear()
    
    # Main scanning loop
    while system_state['scanning']:
        # Display appropriate message
        if not scanned_items:
            lcd.lcd_display_string("Ready to scan", 1)
            lcd.lcd_display_string("1:Barcode 9:Done", 2)
        else:
            lcd.lcd_clear()
            lcd.lcd_display_string(f"Items: {items_scanned}", 1)
            lcd.lcd_display_string(f"Total: ${total:.2f}", 2)
            time.sleep(1)
            lcd.lcd_clear()
            lcd.lcd_display_string("1:New 9:Done", 2)
        
        # Handle keypad input
        if not shared_keypad_queue.empty():
            key = shared_keypad_queue.get()
            
            if key == 1:  # Barcode scan
                distance = usonic.get_distance()
                if distance < 40:
                    dc_motor.set_motor_speed(50)
                    time.sleep(2)
                    dc_motor.set_motor_speed(0)
                scan_barcode(lcd)
                
            elif key == 9:  # Done scanning
                update_state('scanning', False)
                if scanned_items:
                    return True
                else:
                    lcd.lcd_clear()
                    lcd.lcd_display_string("No items scanned", 1)
                    time.sleep(2)
                    return False
                

    
    

def qr_code_mode(lcd):
    lcd.lcd_clear()
    lcd.lcd_display_string("QR Code Mode", 1)
    lcd.lcd_display_string("0:Back 1:Scan", 2)
    
    while True:
        if not shared_keypad_queue.empty():
            key = shared_keypad_queue.get()
            
            if key == 0:  # Back to main menu
                return
                
            elif key == 1:  # Scan QR
                scan_qr_code(lcd) 

def handle_checkout(lcd):
    attempts = 0
    reader = rfid_reader.init() 
    while attempts < 3:
        lcd.lcd_clear()
        lcd.lcd_display_string("Checkout Options", 1)
        lcd.lcd_display_string("1:ATM 2:Paywave 0:Cancel", 2)
        
        if not shared_keypad_queue.empty():
            key = shared_keypad_queue.get()
            
            if key == 0:  # Cancel
                return False
                
            elif key == 1:  # ATM
                pin = read_pin_input(lcd)
                if pin == valid_pin:
                    lcd.lcd_clear()
                    lcd.lcd_display_string("Payment Approved", 1)
                    update_state('payment_success', True)
                    return True
                else:
                    attempts += 1
                    lcd.lcd_clear()
                    lcd.lcd_display_string(f"Invalid PIN ({3-attempts} left)", 1)
                    led.set_output(1, 1)
                    time.sleep(1)
                    led.set_output(1, 0)
                    continue
                    
            elif key == 2:  # Paywave
                lcd.lcd_clear()
                lcd.lcd_display_string("Tap your card", 1)
                id = reader.read_id() 
                if id is not None:
                    lcd.lcd_clear()
                    lcd.lcd_display_string("Payment Approved", 1)
                    update_state('payment_success', True)
                    return True
                else:
                    attempts += 1
                    lcd.lcd_clear()
                    lcd.lcd_display_string("Payment Declined", 1)
                    led.set_output(1, 1)
                    time.sleep(1)
                    led.set_output(1, 0)
                    continue
    
    return False

def device_on(lcd):
    global total, items_scanned, scanned_items
    
    while True:
        # Initial screen
        lcd.lcd_clear()
        lcd.lcd_display_string("1:Checkout 2:QR", 1)
        lcd.lcd_display_string("9:Power 0:Exit", 2)
        
        # Wait for user input
        while True:
            if not shared_keypad_queue.empty():
                key = shared_keypad_queue.get()
                
                if key == 0:  # Exit
                    power_off_display(lcd)
                    return
                elif key == 9:  # Power toggle
                    power_off_display(lcd)
                    # Wait for power on
                    while True:
                        if not shared_keypad_queue.empty():
                            power_key = shared_keypad_queue.get()
                            if power_key == '*':
                                power_on_display(lcd)
                                break
                    break
                elif key == 1:  # Checkout mode (barcodes)
                    if scan_mode(lcd):
                        if handle_checkout(lcd):
                            # Successful payment
                            lcd.lcd_clear()
                            lcd.lcd_display_string("Thank you!", 1)
                            lcd.lcd_display_string("Starting new session", 2)
                            time.sleep(2)
                            
                            # Reset for new customer
                            total = 0.0
                            items_scanned = 0
                            scanned_items = []
                            break
                        else:
                            break
                    break
                elif key == 2:  # QR Code mode
                    qr_code_mode(lcd)
                    break  # Return to main menu after QR mode


def test_db_connection():
    try:
        print("Testing database connection...")
        response = requests.get(f"{BASE_URL}/products")
        if response.status_code == 200:
            print(f"DB connection OK, found {len(response.json())} products")
        else:
            print(f"DB connection failed: {response.status_code}")
    except Exception as e:
        print(f"DB connection test failed: {str(e)}")

def main():
    # Initialize hardware
    keypad.init(key_pressed)
    keypad_thread = Thread(target=keypad.get_key)
    keypad_thread.daemon = True
    keypad_thread.start()
    env_thread = Thread(target=monitor_environment, daemon=True)
    env_thread.start()
    
    led.init()
    lcd = LCD.lcd()
    lcd.lcd_clear()
    
    rfid_reader.init()
    usonic.init()
    dc_motor.init()

    test_db_connection()
    
    # Start with power on
    power_on_display(lcd)
    
    # Main system loop
    device_on(lcd)
    

    # Cleanup
    lcd.lcd_clear()
    dc_motor.set_motor_speed(0)

if __name__ == "__main__":
    main()