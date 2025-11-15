"""
UDP Module - UDP audio transmission and reception
"""
import socket
import threading
import json
import os
import time
import numpy as np
from typing import Optional, Dict, TYPE_CHECKING
from echostream import global_interrupted, MAX_CHANNELS, SAMPLES_PER_FRAME, JITTER_BUFFER_SIZE
import crypto

if TYPE_CHECKING:
    import audio

# Global UDP state
global_udp_socket: Optional[socket.socket] = None
global_server_addr: Optional[tuple] = None
heartbeat_thread: Optional[threading.Thread] = None
udp_listener_thread: Optional[threading.Thread] = None

# Statistics (per channel)
zero_key_warned = [False] * MAX_CHANNELS
jitter_drop_count = [0] * MAX_CHANNELS
decrypt_fail_count = [0] * MAX_CHANNELS

def udp_debug_enabled() -> bool:
    """Check if UDP debug is enabled via environment variable"""
    env = os.getenv("UDP_DEBUG")
    return env is not None and env != "0"

def setup_global_udp(config: Dict) -> bool:
    """
    Initialize UDP socket and connection to server
    
    Args:
        config: Server configuration (host, port, websocket_id)
        
    Returns:
        True on success, False on failure
    """
    global global_udp_socket, global_server_addr, heartbeat_thread, udp_listener_thread
    
    if global_udp_socket is not None:
        return True
    
    try:
        # Create UDP socket (SOCK_DGRAM)
        global_udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        global_udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        # Bind to a local port to receive packets from server
        # Bind to any available port - the server will send packets to the port we use when we send the first heartbeat
        try:
            global_udp_socket.bind(('0.0.0.0', 0))
            bound_port = global_udp_socket.getsockname()[1]
            print(f"UDP socket bound to local port {bound_port} (server will send packets to this port)")
        except Exception as bind_err:
            print(f"Failed to bind UDP socket: {bind_err}")
            global_udp_socket.close()
            global_udp_socket = None
            return False
        
        # Configure server address from config
        global_server_addr = (config['udp_host'], config['udp_port'])
        
        print(f"Global UDP socket configured for {config['udp_host']}:{config['udp_port']} (bound to local port {bound_port})")
        
        # Send initial heartbeat packet
        heartbeat_msg = b'{"type":"KEEP_ALIVE"}'
        try:
            global_udp_socket.sendto(heartbeat_msg, global_server_addr)
        except Exception as e:
            print(f"Initial heartbeat error: {e}")
        
        # Start heartbeat worker thread
        if heartbeat_thread is None or not heartbeat_thread.is_alive():
            heartbeat_thread = threading.Thread(target=heartbeat_worker, daemon=True)
            heartbeat_thread.start()
        
        # Start UDP listener worker thread
        if udp_listener_thread is None or not udp_listener_thread.is_alive():
            udp_listener_thread = threading.Thread(target=udp_listener_worker, daemon=True)
            udp_listener_thread.start()
        
        return True
    except Exception as e:
        print(f"UDP setup failed: {e}")
        return False

def heartbeat_worker(arg=None):
    """
    Background thread that sends keepalive packets
    
    Sends {"type":"KEEP_ALIVE"} every 10 seconds to maintain NAT mapping
    """
    global global_udp_socket, global_server_addr
    
    print("Heartbeat worker started")
    heartbeat_count = 0
    
    while not global_interrupted.is_set():
        if global_udp_socket is not None and global_server_addr is not None:
            heartbeat_msg = b'{"type":"KEEP_ALIVE"}'
            try:
                global_udp_socket.sendto(heartbeat_msg, global_server_addr)
                heartbeat_count += 1
                # Log every 60th heartbeat (~10 minutes)
                if heartbeat_count % 60 == 0:
                    print(f"Heartbeat sent (count: {heartbeat_count})")
            except Exception as e:
                print(f"Heartbeat error: {e}")
        
        # Sleep for 10 seconds
        for _ in range(100):
            if global_interrupted.is_set():
                break
            time.sleep(0.1)
    
    print("Heartbeat worker stopped")
    return None

def process_received_audio(audio_stream: 'audio.AudioStream', opus_data: bytes, channel_id: str, channel_index: int):
    """
    Process received audio: decode Opus and add to jitter buffer
    
    Args:
        audio_stream: Audio stream for the channel
        opus_data: Decrypted Opus-encoded audio data
        channel_id: Channel identifier
        channel_index: Channel index
    """
    try:
        import audio  # Import here to avoid circular import
        
        # Track reception count
        static_receive_count = getattr(process_received_audio, '_receive_count', {})
        if channel_id not in static_receive_count:
            static_receive_count[channel_id] = 0
        static_receive_count[channel_id] += 1
        process_received_audio._receive_count = static_receive_count
        
        # Log first few packets and then occasionally
        if static_receive_count[channel_id] <= 5 or static_receive_count[channel_id] % 500 == 0:
            print(f"[UDP AUDIO] Channel {channel_id}: Received audio packet #{static_receive_count[channel_id]} ({len(opus_data)} bytes)")
        
        if audio_stream.decoder is None:
            if static_receive_count[channel_id] <= 5:
                print(f"[UDP ERROR] Channel {channel_id}: Decoder is None!")
            return
        
        # Decode Opus to PCM (1920 samples)
        pcm_bytes = audio_stream.decoder.decode(opus_data, SAMPLES_PER_FRAME)
        if not pcm_bytes:
            if static_receive_count[channel_id] <= 5:
                print(f"[UDP ERROR] Channel {channel_id}: Opus decode returned empty")
            return
        
        # Convert int16 PCM to float samples
        pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
        samples = pcm.astype(np.float32) / 32767.0
        
        # Apply gain boost (20x for received audio)
        samples *= 20.0
        samples = np.clip(samples, -1.0, 1.0)
        
        # Add frame to jitter buffer
        with audio_stream.output_jitter.mutex:
            buffer_before = audio_stream.output_jitter.frame_count
            
            # Check if buffer is full
            if audio_stream.output_jitter.frame_count >= JITTER_BUFFER_SIZE:
                # Drop oldest frame (circular buffer)
                old_read_idx = audio_stream.output_jitter.read_index
                audio_stream.output_jitter.read_index = (
                    audio_stream.output_jitter.read_index + 1
                ) % JITTER_BUFFER_SIZE
                audio_stream.output_jitter.frame_count -= 1
                
                jitter_drop_count[channel_index] += 1
                if jitter_drop_count[channel_index] % 100 == 0:
                    print(f"[JITTER DROP] Channel {channel_id}: Dropped frame at idx {old_read_idx} (total drops: {jitter_drop_count[channel_index]})")
            
            # Add new frame
            write_idx = audio_stream.output_jitter.write_index
            frame = audio_stream.output_jitter.frames[write_idx]
            frame.samples[:len(samples)] = samples
            frame.sample_count = len(samples)
            frame.valid = True
            
            audio_stream.output_jitter.write_index = (
                audio_stream.output_jitter.write_index + 1
            ) % JITTER_BUFFER_SIZE
            audio_stream.output_jitter.frame_count += 1
            
            # Log when buffer transitions from empty to having data
            if buffer_before == 0 and audio_stream.output_jitter.frame_count > 0:
                print(f"[JITTER RECOVERY] Channel {channel_id}: Buffer refilled! frames={audio_stream.output_jitter.frame_count}, "
                      f"write_idx={audio_stream.output_jitter.write_index}, read_idx={audio_stream.output_jitter.read_index}")
            
            # Log buffer status occasionally
            if static_receive_count[channel_id] <= 5 or static_receive_count[channel_id] % 500 == 0:
                print(f"[JITTER WRITE] Channel {channel_id}: Added frame at idx {write_idx}, "
                      f"frames={audio_stream.output_jitter.frame_count}/{JITTER_BUFFER_SIZE}, "
                      f"read_idx={audio_stream.output_jitter.read_index}, packets_received={static_receive_count[channel_id]}")
    
    except Exception as e:
        print(f"[UDP ERROR] Error processing received audio for channel {channel_id}: {e}")
        import traceback
        traceback.print_exc()

def udp_listener_worker(arg=None):
    """
    Background thread that receives and processes audio packets
    
    Receives UDP packets, parses JSON, decrypts audio, decodes Opus, and adds to jitter buffer
    """
    global global_udp_socket
    
    print("UDP listener worker started")
    
    if global_udp_socket is None:
        print("UDP Listener: ERROR - Invalid socket")
        return None
    
    packet_count = 0
    
    while not global_interrupted.is_set():
        try:
            # Set socket timeout to allow checking global_interrupted
            global_udp_socket.settimeout(0.1)
            
            buffer, client_addr = global_udp_socket.recvfrom(8192)
            
            packet_count += 1
            
            if buffer:
                try:
                    data_str = buffer.decode('utf-8')
                    json_data = json.loads(data_str)
                    
                    channel_id = json_data.get('channel_id', '')
                    msg_type = json_data.get('type', '')
                    data = json_data.get('data', '')
                    
                    if msg_type == 'audio':
                        # Import here to avoid circular import
                        import audio
                        
                        # Find the channel
                        target_stream = None
                        target_index = -1
                        
                        for i in range(MAX_CHANNELS):
                            if audio.channels[i].active and audio.channels[i].audio.channel_id == channel_id:
                                target_stream = audio.channels[i].audio
                                target_index = i
                                break
                        
                        if not target_stream:
                            # Log missing channel occasionally
                            static_missing_count = getattr(udp_listener_worker, '_missing_channel_count', {})
                            if channel_id not in static_missing_count:
                                static_missing_count[channel_id] = 0
                            static_missing_count[channel_id] += 1
                            udp_listener_worker._missing_channel_count = static_missing_count
                            
                            if static_missing_count[channel_id] <= 5 or static_missing_count[channel_id] % 500 == 0:
                                active_channels = [audio.channels[i].audio.channel_id 
                                                 for i in range(MAX_CHANNELS) if audio.channels[i].active]
                                print(f"[UDP ERROR] Channel {channel_id} not found! Active channels: {active_channels}")
                            continue
                        
                        # Decode base64 data
                        encrypted_data = crypto.decode_base64(data)
                        
                        if len(encrypted_data) == 0:
                            static_b64_count = getattr(udp_listener_worker, '_b64_fail_count', {})
                            if channel_id not in static_b64_count:
                                static_b64_count[channel_id] = 0
                            static_b64_count[channel_id] += 1
                            udp_listener_worker._b64_fail_count = static_b64_count
                            
                            if static_b64_count[channel_id] <= 5:
                                print(f"[UDP ERROR] Channel {channel_id}: Base64 decode failed")
                            continue
                        
                        # Check if key is zero
                        key_is_zero = all(b == 0 for b in target_stream.key)
                        
                        if not key_is_zero:
                            zero_key_warned[target_index] = False
                        
                        # Decrypt the data
                        decrypted = crypto.decrypt_data(encrypted_data, bytes(target_stream.key))
                        
                        if decrypted:
                            # Decode Opus audio and add to jitter buffer
                            process_received_audio(target_stream, decrypted, channel_id, target_index)
                        else:
                            if key_is_zero and target_index >= 0:
                                if not zero_key_warned[target_index]:
                                    print(f"[UDP ERROR] Channel {channel_id}: AES key not set (all zeros)")
                                    zero_key_warned[target_index] = True
                            else:
                                if target_index >= 0:
                                    decrypt_fail_count[target_index] += 1
                                    if decrypt_fail_count[target_index] == 1 or decrypt_fail_count[target_index] % 50 == 0:
                                        print(f"[UDP ERROR] Channel {channel_id}: Decryption failed (count: {decrypt_fail_count[target_index]})")
                
                except json.JSONDecodeError:
                    if udp_debug_enabled():
                        print("UDP Listener: Failed to parse JSON")
                except Exception as e:
                    if udp_debug_enabled():
                        print(f"UDP Listener: Error processing message: {e}")
        
        except socket.timeout:
            # Timeout is expected, continue loop
            continue
        except Exception as e:
            if not global_interrupted.is_set():
                print(f"[UDP ERROR] Receive error: {e}")
            time.sleep(0.1)
            continue
    
    print("UDP listener worker stopped")
    return None

