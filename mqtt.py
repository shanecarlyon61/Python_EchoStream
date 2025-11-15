"""
MQTT Module - MQTT publishing for tone detection events
"""
import json
import os
import threading
import time
from typing import Optional
try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False
    print("[WARNING] paho-mqtt not available, MQTT functionality disabled")

# Global MQTT state
class MQTTState:
    def __init__(self):
        self.client: Optional[mqtt.Client] = None
        self.device_id = ""
        self.broker_host = ""
        self.broker_port = 8883
        self.connected = False
        self.initialized = False
        self.ca_cert_path = ""
        self.client_cert_path = ""
        self.client_key_path = ""

global_mqtt = MQTTState()
mqtt_mutex = threading.Lock()

def init_mqtt(device_id: str, broker_host: str, broker_port: int) -> bool:
    """
    Initialize MQTT connection to broker
    
    Args:
        device_id: Device identifier
        broker_host: Broker hostname (ignored, uses AWS IoT)
        broker_port: Broker port (ignored, uses 8883)
        
    Returns:
        True on success, False on failure
    """
    global global_mqtt
    
    if not MQTT_AVAILABLE:
        print("[WARNING] MQTT not available - paho-mqtt library not installed")
        return False
    
    with mqtt_mutex:
        if global_mqtt.initialized:
            return True
        
        try:
            global_mqtt.device_id = device_id
            
            # AWS IoT endpoint (hardcoded)
            aws_endpoint = "a1d6e0zlehb0v9-ats.iot.us-west-2.amazonaws.com"
            global_mqtt.broker_host = aws_endpoint
            global_mqtt.broker_port = 8883
            
            # Find certificates in /home/will/.an/
            cert_dir = "/home/will/.an"
            global_mqtt.ca_cert_path = os.path.join(cert_dir, "AmazonRootCA1.pem")
            global_mqtt.client_cert_path = os.path.join(cert_dir, "certificate.pem.crt")
            global_mqtt.client_key_path = os.path.join(cert_dir, "private.pem.key")
            
            # Create MQTT client
            global_mqtt.client = mqtt.Client(client_id=device_id)
            
            # Setup TLS if certificates are available
            if os.path.exists(global_mqtt.ca_cert_path) and \
               os.path.exists(global_mqtt.client_cert_path) and \
               os.path.exists(global_mqtt.client_key_path):
                global_mqtt.client.tls_set(
                    ca_certs=global_mqtt.ca_cert_path,
                    certfile=global_mqtt.client_cert_path,
                    keyfile=global_mqtt.client_key_path
                )
            
            # Connect to broker
            global_mqtt.client.connect(global_mqtt.broker_host, global_mqtt.broker_port, 60)
            global_mqtt.client.loop_start()
            
            global_mqtt.initialized = True
            global_mqtt.connected = True
            print(f"MQTT initialized and connected to {global_mqtt.broker_host}:{global_mqtt.broker_port}")
            return True
        except Exception as e:
            print(f"MQTT initialization failed: {e}")
            return False

def mqtt_publish(topic: str, payload: str) -> bool:
    """
    Publish message to MQTT topic
    
    Args:
        topic: MQTT topic string
        payload: JSON payload string
        
    Returns:
        True on success, False on failure
    """
    global global_mqtt
    
    if not MQTT_AVAILABLE or not global_mqtt.client:
        return False
    
    try:
        result = global_mqtt.client.publish(topic, payload, qos=1)
        return result.rc == mqtt.MQTT_ERR_SUCCESS
    except Exception as e:
        print(f"MQTT publish failed: {e}")
        return False

def mqtt_keepalive():
    """Keep MQTT connection alive (call periodically)"""
    global global_mqtt
    
    if not MQTT_AVAILABLE or not global_mqtt.client:
        return
    
    try:
        if not global_mqtt.client.is_connected():
            # Try to reconnect
            global_mqtt.client.reconnect()
    except Exception:
        pass

def cleanup_mqtt():
    """Cleanup MQTT connection"""
    global global_mqtt
    
    if not MQTT_AVAILABLE or not global_mqtt.client:
        return
    
    try:
        global_mqtt.client.loop_stop()
        global_mqtt.client.disconnect()
        global_mqtt.connected = False
        global_mqtt.initialized = False
        print("MQTT cleaned up")
    except Exception as e:
        print(f"MQTT cleanup error: {e}")

def publish_new_tone_detection(frequency: float, duration_ms: int, range_hz: int) -> bool:
    """
    Publish single tone detection event
    
    Topic: from/device/{device_id}/tone_detection
    """
    global global_mqtt
    
    if not global_mqtt.connected:
        return False
    
    try:
        device_id = global_mqtt.device_id or "echostream_device"
        topic = f"from/device/{device_id}/tone_detection"
        
        message = {
            "message_id": f"tone_{int(time.time())}",
            "timestamp": int(time.time()),
            "device_id": device_id,
            "event_type": "new_tone_detected",
            "tone_details": {
                "frequency_hz": frequency,
                "duration_ms": duration_ms,
                "range_hz": range_hz
            }
        }
        
        return mqtt_publish(topic, json.dumps(message))
    except Exception as e:
        print(f"Failed to publish new tone detection: {e}")
        return False

def publish_new_tone_pair(tone_a_hz: float, tone_b_hz: float) -> bool:
    """
    Publish two-tone pair detection
    
    Topic: from/device/{device_id}/tone_detection
    """
    global global_mqtt
    
    if not global_mqtt.connected:
        return False
    
    try:
        device_id = global_mqtt.device_id or "echostream_device"
        topic = f"from/device/{device_id}/tone_detection"
        
        message = {
            "message_id": f"tone_{int(time.time())}",
            "timestamp": int(time.time()),
            "device_id": device_id,
            "event_type": "new_tone_detected",
            "tone_details": {
                "tone_a": tone_a_hz,
                "tone_b": tone_b_hz
            }
        }
        
        return mqtt_publish(topic, json.dumps(message))
    except Exception as e:
        print(f"Failed to publish new tone pair: {e}")
        return False

