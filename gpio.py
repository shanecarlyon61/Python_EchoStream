"""
GPIO Module - GPIO pin monitoring for Push-To-Talk (PTT) functionality
"""
import lgpio
import time
import threading
from typing import Optional
from echostream import global_interrupted, global_channel_ids, global_channel_count

# GPIO state variables
gpio_38_state = 0
gpio_40_state = 0
gpio_16_state = 0
gpio_18_state = 0
gpio_mutex = threading.Lock()

# GPIO chip handle
gpio_chip: Optional[int] = None

# GPIO Pin Mapping
# Physical Pin | GPIO Number | Channel | Function
# 38          | GPIO 20     | Channel 1 | PTT control
# 40          | GPIO 21     | Channel 2 | PTT control
# 16          | GPIO 23     | Channel 3 | PTT control
# 18          | GPIO 24     | Channel 4 | PTT control

def init_gpio_pin(pin: int) -> bool:
    """
    Initialize a GPIO pin as input with pull-up resistor
    
    Args:
        pin: GPIO pin number (not physical pin)
        
    Returns:
        True on success, False on failure
    """
    global gpio_chip
    
    if gpio_chip is None:
        gpio_chip = lgpio.gpiochip_open(0)
        if gpio_chip < 0:
            print(f"ERROR: Cannot open GPIO chip 0")
            return False
    
    # Set pin as input with pull-up (pinctrl for Raspberry Pi 5)
    try:
        lgpio.gpio_claim_input(gpio_chip, pin, lgpio.SET_PULL_UP)
        print(f"GPIO pin {pin} initialized successfully")
        return True
    except Exception as e:
        print(f"ERROR: Cannot configure GPIO pin {pin}: {e}")
        return False

def read_gpio_pin(pin: int) -> int:
    """
    Read current state of a GPIO pin
    
    Args:
        pin: GPIO pin number
        
    Returns:
        0: Pin is LOW (active/PTT pressed)
        1: Pin is HIGH (inactive/PTT released)
        -1: Error reading pin
    """
    global gpio_chip
    
    if gpio_chip is None:
        return -1
    
    try:
        value = lgpio.gpio_read(gpio_chip, pin)
        return value  # 0 = low, 1 = high
    except Exception:
        return -1

def cleanup_gpio(pin: int):
    """Cleanup GPIO pin (legacy function, not actively used)"""
    global gpio_chip
    
    if gpio_chip is None:
        return
    
    try:
        lgpio.gpio_free(gpio_chip, pin)
    except Exception:
        pass

def gpio_monitor_worker(arg=None):
    """
    Main worker thread that monitors GPIO pins
    
    Monitors four GPIO pins every 100ms, detects state changes,
    updates gpio_active flag for affected channel, and sends WebSocket transmit events
    """
    global gpio_38_state, gpio_40_state, gpio_16_state, gpio_18_state
    
    # GPIO pin numbers (not physical pins)
    gpio_pin_38 = 20   # GPIO 20 (physical pin 38)
    gpio_pin_40 = 21   # GPIO 21 (physical pin 40)
    gpio_pin_16 = 23   # GPIO 23 (physical pin 16)
    gpio_pin_18 = 24   # GPIO 24 (physical pin 18)
    
    print("GPIO monitor worker started")
    
    # Initialize all four GPIO pins
    if not (init_gpio_pin(gpio_pin_38) and
            init_gpio_pin(gpio_pin_40) and
            init_gpio_pin(gpio_pin_16) and
            init_gpio_pin(gpio_pin_18)):
        print("Failed to initialize one or more GPIO pins")
        return None
    
    # Read initial states
    with gpio_mutex:
        gpio_38_state = read_gpio_pin(gpio_pin_38)
        gpio_40_state = read_gpio_pin(gpio_pin_40)
        gpio_16_state = read_gpio_pin(gpio_pin_16)
        gpio_18_state = read_gpio_pin(gpio_pin_18)
    
    print(f"PIN 38 initial state: {'ACTIVE' if gpio_38_state == 0 else 'INACTIVE'}")
    print(f"PIN 40 initial state: {'ACTIVE' if gpio_40_state == 0 else 'INACTIVE'}")
    print(f"PIN 16 initial state: {'ACTIVE' if gpio_16_state == 0 else 'INACTIVE'}")
    print(f"PIN 18 initial state: {'ACTIVE' if gpio_18_state == 0 else 'INACTIVE'}")
    
    # Import modules at runtime to avoid circular dependencies
    import audio
    import websocket
    import mqtt
    import tone_detect
    
    # Set gpio_active flag for channels with active pins at startup
    if gpio_38_state == 0:
        for i in range(global_channel_count):
            if audio.channels[i].active and audio.channels[i].audio.channel_id == global_channel_ids[0]:
                audio.channels[i].audio.gpio_active = True
                print(f"Channel {global_channel_ids[0]} audio ENABLED (PIN 38 was already active)")
                break
    
    if gpio_40_state == 0:
        for i in range(global_channel_count):
            if audio.channels[i].active and audio.channels[i].audio.channel_id == global_channel_ids[1]:
                audio.channels[i].audio.gpio_active = True
                print(f"Channel {global_channel_ids[1]} audio ENABLED (PIN 40 was already active)")
                break
    
    if gpio_16_state == 0:
        for i in range(global_channel_count):
            if audio.channels[i].active and audio.channels[i].audio.channel_id == global_channel_ids[2]:
                audio.channels[i].audio.gpio_active = True
                print(f"Channel {global_channel_ids[2]} audio ENABLED (PIN 16 was already active)")
                break
    
    if gpio_18_state == 0 and global_channel_count > 3:
        for i in range(global_channel_count):
            if audio.channels[i].active and audio.channels[i].audio.channel_id == global_channel_ids[3]:
                audio.channels[i].audio.gpio_active = True
                print(f"Channel {global_channel_ids[3]} audio ENABLED (PIN 18 was already active)")
                break
    
    status_counter = 0
    mqtt_keepalive_counter = 0
    
    # Monitoring loop
    while not global_interrupted.is_set():
        # Read all four pins every 100ms
        curr_val_38 = read_gpio_pin(gpio_pin_38)
        curr_val_40 = read_gpio_pin(gpio_pin_40)
        curr_val_16 = read_gpio_pin(gpio_pin_16)
        curr_val_18 = read_gpio_pin(gpio_pin_18)
        
        # Call MQTT keepalive every 1 second (10 iterations)
        mqtt_keepalive_counter += 1
        if mqtt_keepalive_counter >= 10:
            mqtt.mqtt_keepalive()
            mqtt_keepalive_counter = 0
        
        with gpio_mutex:
            # Detect state changes and update gpio_active flag
            if curr_val_38 != gpio_38_state and curr_val_38 != -1:
                gpio_38_state = curr_val_38
                print(f"PIN 38: {'ACTIVE' if curr_val_38 == 0 else 'INACTIVE'}")
                
                # Find channel and set gpio_active flag
                for i in range(global_channel_count):
                    if audio.channels[i].active and audio.channels[i].audio.channel_id == global_channel_ids[0]:
                        audio.channels[i].audio.gpio_active = (curr_val_38 == 0)
                        print(f"Channel {global_channel_ids[0]} audio {'ENABLED' if audio.channels[i].audio.gpio_active else 'DISABLED'}")
                        break
                
                # Send WebSocket transmit event
                websocket.send_websocket_transmit_event(global_channel_ids[0], 1 if curr_val_38 == 0 else 0)
            
            if curr_val_40 != gpio_40_state and curr_val_40 != -1:
                gpio_40_state = curr_val_40
                print(f"PIN 40: {'ACTIVE' if curr_val_40 == 0 else 'INACTIVE'}")
                
                for i in range(global_channel_count):
                    if audio.channels[i].active and audio.channels[i].audio.channel_id == global_channel_ids[1]:
                        audio.channels[i].audio.gpio_active = (curr_val_40 == 0)
                        print(f"Channel {global_channel_ids[1]} audio {'ENABLED' if audio.channels[i].audio.gpio_active else 'DISABLED'}")
                        break
                
                websocket.send_websocket_transmit_event(global_channel_ids[1], 1 if curr_val_40 == 0 else 0)
            
            if curr_val_16 != gpio_16_state and curr_val_16 != -1:
                gpio_16_state = curr_val_16
                print(f"PIN 16: {'ACTIVE' if curr_val_16 == 0 else 'INACTIVE'}")
                
                for i in range(global_channel_count):
                    if audio.channels[i].active and audio.channels[i].audio.channel_id == global_channel_ids[2]:
                        audio.channels[i].audio.gpio_active = (curr_val_16 == 0)
                        print(f"Channel {global_channel_ids[2]} audio {'ENABLED' if audio.channels[i].audio.gpio_active else 'DISABLED'}")
                        break
                
                websocket.send_websocket_transmit_event(global_channel_ids[2], 1 if curr_val_16 == 0 else 0)
            
            if curr_val_18 != gpio_18_state and curr_val_18 != -1:
                gpio_18_state = curr_val_18
                print(f"PIN 18: {'ACTIVE' if curr_val_18 == 0 else 'INACTIVE'}")
                
                if global_channel_count > 3:
                    for i in range(global_channel_count):
                        if audio.channels[i].active and audio.channels[i].audio.channel_id == global_channel_ids[3]:
                            audio.channels[i].audio.gpio_active = (curr_val_18 == 0)
                            print(f"Channel {global_channel_ids[3]} audio {'ENABLED' if audio.channels[i].audio.gpio_active else 'DISABLED'}")
                            break
                    
                    websocket.send_websocket_transmit_event(global_channel_ids[3], 1 if curr_val_18 == 0 else 0)
            
            # Display status every 10 seconds (100 iterations)
            status_counter += 1
            if status_counter >= 100:
                print("\n=== GPIO Status Report (every 10 seconds) ===")
                print(f"PIN 38 (GPIO 20): {'ACTIVE' if curr_val_38 == 0 else 'INACTIVE'} (Channel: {global_channel_ids[0]})")
                print(f"PIN 40 (GPIO 21): {'ACTIVE' if curr_val_40 == 0 else 'INACTIVE'} (Channel: {global_channel_ids[1]})")
                print(f"PIN 16 (GPIO 23): {'ACTIVE' if curr_val_16 == 0 else 'INACTIVE'} (Channel: {global_channel_ids[2]})")
                if global_channel_count > 3:
                    print(f"PIN 18 (GPIO 24): {'ACTIVE' if curr_val_18 == 0 else 'INACTIVE'} (Channel: {global_channel_ids[3]})")
                else:
                    print(f"PIN 18 (GPIO 24): {'ACTIVE' if curr_val_18 == 0 else 'INACTIVE'} (Channel: N/A)")
                
                # Add recording status
                if tone_detect.global_tone_detection.recording_active:
                    remaining = tone_detect.get_recording_time_remaining_ms()
                    print(f"🎙️  RECORDING: Active ({remaining} ms remaining)")
                else:
                    print("🎙️  RECORDING: Inactive")
                print("==========================================\n")
                status_counter = 0
        
        time.sleep(0.1)  # 100ms poll
    
    print("GPIO monitor worker stopped")
    return None

