"""
WebSocket Module - WebSocket communication with EchoStream server
"""
import json
import time
import threading
import websockets
import asyncio
from typing import Optional, Dict
from echostream import global_interrupted, global_channel_ids, global_channel_count
import audio
import udp
import crypto

# Global WebSocket state
global_ws_client: Optional[websockets.WebSocketClientProtocol] = None
global_ws_context: Optional[asyncio.AbstractEventLoop] = None

class ServerConfig:
    def __init__(self):
        self.udp_port = 0
        self.udp_host = ""
        self.websocket_id = 0

global_config = ServerConfig()
global_config_initialized = False

def parse_websocket_config(json_str: str, cfg: ServerConfig) -> bool:
    """
    Parse UDP configuration from JSON message
    
    Args:
        json_str: JSON string from server
        cfg: Output configuration structure
        
    Returns:
        True on success, False on failure
    """
    try:
        data = json.loads(json_str)
        cfg.udp_port = data.get('udp_port', 0)
        cfg.udp_host = data.get('udp_host', '')
        cfg.websocket_id = data.get('websocket_id', 0)
        
        print(f"UDP Port: {cfg.udp_port}")
        print(f"UDP Host: {cfg.udp_host}")
        print(f"WebSocket ID: {cfg.websocket_id}")
        
        return True
    except Exception as e:
        print(f"Failed to parse WebSocket config: {e}")
        return False

def send_websocket_transmit_event(channel_id: str, is_started: int):
    """
    Send transmit_started or transmit_ended event to server
    
    Args:
        channel_id: Channel identifier
        is_started: 1 for transmit_started, 0 for transmit_ended
    """
    global global_ws_client
    
    if global_ws_client is None:
        return
    
    try:
        event_type = "transmit_started" if is_started else "transmit_ended"
        now = int(time.time())
        
        transmit_msg = {
            event_type: {
                "affiliation_id": "12345",
                "user_name": "EchoStream",
                "agency_name": "TestAgency",
                "channel_id": channel_id,
                "time": now
            }
        }
        
        # Send via asyncio if we're in an async context
        if global_ws_context and global_ws_context.is_running():
            asyncio.run_coroutine_threadsafe(
                global_ws_client.send(json.dumps(transmit_msg)),
                global_ws_context
            )
    except Exception as e:
        print(f"Failed to send WebSocket message for channel {channel_id}: {e}")

async def websocket_handler():
    """WebSocket connection handler"""
    global global_ws_client, global_config, global_config_initialized
    
    ws_url = "wss://audio.redenes.org/ws/"
    print(f"Connecting to: {ws_url} for all channels")
    
    try:
        async with websockets.connect(ws_url) as ws:
            global_ws_client = ws
            print("[INFO] WebSocket connection established for all channels")
            
            # Register all active channels - send connect messages
            for i in range(global_channel_count):
                if audio.channels[i].active:
                    now = int(time.time())
                    connect_msg = {
                        "connect": {
                            "affiliation_id": "12345",
                            "user_name": "EchoStream",
                            "agency_name": "TestAgency",
                            "channel_id": audio.channels[i].audio.channel_id,
                            "time": now
                        }
                    }
                    await ws.send(json.dumps(connect_msg))
                    print(f"[INFO] Connect message sent for channel {audio.channels[i].audio.channel_id}")
            
            # Listen for messages
            async for message in ws:
                if global_interrupted.is_set():
                    break
                
                try:
                    if not message or len(message) == 0:
                        continue
                    
                    data = json.loads(message)
                    
                    # Check for UDP configuration message
                    if 'udp_host' in data and 'udp_port' in data and 'websocket_id' in data:
                        print("[WEBSOCKET] UDP Connection Info Received")
                        
                        # Parse the WebSocket configuration
                        if parse_websocket_config(message, global_config):
                            global_config_initialized = True
                            
                            # Setup UDP connection
                            config_dict = {
                                'udp_port': global_config.udp_port,
                                'udp_host': global_config.udp_host,
                                'websocket_id': global_config.websocket_id
                            }
                            
                            if udp.setup_global_udp(config_dict):
                                print("UDP connection established")
                                
                                # Set encryption keys and start transmission for all active channels
                                for i in range(global_channel_count):
                                    if audio.channels[i].active:
                                        key_b64 = "46dR4QR5KH7JhPyyjh/ZS4ki/3QBVwwOTkkQTdZQkC0="
                                        key_bytes = crypto.decode_base64(key_b64)
                                        if len(key_bytes) == 32:
                                            audio.channels[i].audio.key = list(key_bytes)
                                            print(f"AES key decoded for channel {audio.channels[i].audio.channel_id}")
                                        
                                        # If GPIO is already active at startup, send transmit_started immediately
                                        # This tells the server to start sending audio for this channel
                                        if audio.channels[i].audio.gpio_active:
                                            await ws.send(json.dumps({
                                                "transmit_started": {
                                                    "affiliation_id": "12345",
                                                    "user_name": "EchoStream",
                                                    "agency_name": "TestAgency",
                                                    "channel_id": audio.channels[i].audio.channel_id,
                                                    "time": int(time.time())
                                                }
                                            }))
                                            print(f"[INFO] Sent transmit_started for channel {audio.channels[i].audio.channel_id} (GPIO already active)")
                            
                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    print(f"[WEBSOCKET] Error processing message: {e}")
    
    except Exception as e:
        print(f"[ERROR] WebSocket connection error: {e}")
        global_ws_client = None
    finally:
        print("[WARNING] WebSocket closed for all channels")
        global_ws_client = None

def connect_global_websocket() -> bool:
    """
    Establish WebSocket connection to server
    
    Returns:
        True on success, False on failure
    """
    global global_ws_context
    
    if global_ws_client is not None:
        return True
    
    print("[INFO] Attempting WebSocket connection...")
    
    # Create new event loop for WebSocket
    global_ws_context = asyncio.new_event_loop()
    
    def run_websocket():
        global_ws_context.run_until_complete(websocket_handler())
    
    ws_thread = threading.Thread(target=run_websocket, daemon=True)
    ws_thread.start()
    
    # Give it a moment to connect
    time.sleep(1)
    
    return global_ws_client is not None

def global_websocket_thread(arg=None):
    """Background thread for WebSocket event processing"""
    print("Starting global WebSocket thread")
    
    while not global_interrupted.is_set():
        if global_ws_client is None:
            connect_global_websocket()
        time.sleep(1)
    
    # Cleanup: send transmit_ended for all channels
    for i in range(global_channel_count):
        if audio.channels[i].active:
            send_websocket_transmit_event(audio.channels[i].audio.channel_id, 0)
    
    print("[INFO] Global WebSocket thread terminated")
    return None

