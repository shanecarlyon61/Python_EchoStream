"""
Audio Module - Audio I/O, encoding/decoding, and passthrough
"""
import pyaudio
import opuslib
import numpy as np
import threading
import time
import subprocess
import os
from typing import Optional, List
from echostream import (
    MAX_CHANNELS, CHANNEL_ID_LEN, JITTER_BUFFER_SIZE, 
    SAMPLES_PER_FRAME, SAMPLE_RATE, global_channel_ids, global_channel_count
)
import crypto
import udp
import config
import tone_detect

# Audio structures
class AudioFrame:
    def __init__(self):
        self.samples = np.zeros(SAMPLES_PER_FRAME, dtype=np.float32)
        self.sample_count = 0
        self.valid = False

class JitterBuffer:
    def __init__(self):
        self.frames = [AudioFrame() for _ in range(JITTER_BUFFER_SIZE)]
        self.write_index = 0
        self.read_index = 0
        self.frame_count = 0
        self.mutex = threading.Lock()

class AudioStream:
    def __init__(self):
        self.input_stream: Optional[pyaudio.Stream] = None
        self.output_stream: Optional[pyaudio.Stream] = None
        self.encoder: Optional[opuslib.Encoder] = None
        self.decoder: Optional[opuslib.Decoder] = None
        self.key = [0] * 32
        self.transmitting = False
        self.gpio_active = False
        self.input_buffer: np.ndarray = np.zeros(4800, dtype=np.float32)
        self.output_jitter = JitterBuffer()
        self.buffer_size = 4800
        self.input_buffer_pos = 0
        self.current_output_frame_pos = 0
        self.device_index = -1
        self.channel_id = ""

class ChannelContext:
    def __init__(self):
        self.audio = AudioStream()
        self.thread: Optional[threading.Thread] = None
        self.active = False

# Global audio state
channels = [ChannelContext() for _ in range(MAX_CHANNELS)]
usb_devices = [-1] * MAX_CHANNELS
device_assigned = False

# Global shared audio buffer
class SharedAudioBuffer:
    def __init__(self):
        self.samples = np.zeros(SAMPLES_PER_FRAME, dtype=np.float32)
        self.sample_count = 0
        self.valid = False
        self.mutex = threading.Lock()
        self.data_ready = threading.Condition(self.mutex)

global_shared_buffer = SharedAudioBuffer()

# Global tone detection control
class ToneDetectControl:
    def __init__(self):
        self.enabled = True
        self.card1_input_enabled = True
        self.passthrough_mode = False
        self.mutex = threading.Lock()

global_tone_detect = ToneDetectControl()

# PyAudio instance
pa_instance: Optional[pyaudio.PyAudio] = None

def initialize_portaudio() -> bool:
    """Initialize PortAudio library (singleton pattern)"""
    global pa_instance
    if pa_instance is not None:
        return True
    
    try:
        pa_instance = pyaudio.PyAudio()
        return True
    except Exception as e:
        print(f"PortAudio initialization failed: {e}")
        return False

def setup_audio_for_channel(audio_stream: AudioStream) -> bool:
    """Setup Opus encoder/decoder and buffers for a channel"""
    try:
        # Setup Opus encoder (48kHz, mono, VOIP mode, 64kbps, VBR)
        audio_stream.encoder = opuslib.Encoder(SAMPLE_RATE, 1, opuslib.APPLICATION_VOIP)
        try:
            audio_stream.encoder.bitrate = 64000
            audio_stream.encoder.vbr = True
        except AttributeError:
            pass
        
        # Setup Opus decoder (48kHz, mono)
        audio_stream.decoder = opuslib.Decoder(SAMPLE_RATE, 1)
        
        # Initialize buffers (4800 samples = 100ms at 48kHz)
        audio_stream.input_buffer = np.zeros(4800, dtype=np.float32)
        audio_stream.input_buffer_pos = 0
        audio_stream.current_output_frame_pos = 0
        
        # Set encryption key (base64 encoded in code)
        key_b64 = "46dR4QR5KH7JhPyyjh/ZS4ki/3QBVwwOTkkQTdZQkC0="
        key_bytes = crypto.decode_base64(key_b64)
        if len(key_bytes) == 32:
            audio_stream.key = list(key_bytes)
        
        return True
    except Exception as e:
        print(f"Audio setup failed: {e}")
        return False

def auto_assign_usb_devices():
    """Automatically detect and assign USB audio devices to channels"""
    global device_assigned, usb_devices
    
    if device_assigned or pa_instance is None:
        return
    
    print("Scanning for USB audio devices...")
    
    num_devices = pa_instance.get_device_count()
    usb_count = 0
    
    # Scan for USB devices
    for i in range(num_devices):
        if usb_count >= MAX_CHANNELS:
            break
        
        try:
            device_info = pa_instance.get_device_info_by_index(i)
            if device_info['maxInputChannels'] > 0:
                name = device_info['name'].lower()
                # Look for USB audio devices
                if 'usb' in name or 'audio device' in name or 'headset' in name:
                    usb_devices[usb_count] = i
                    print(f"USB Device {i} assigned to slot {usb_count}: {device_info['name']}")
                    usb_count += 1
        except Exception:
            continue
    
    # Fallback to default device if no USB devices found
    if usb_count == 0:
        print("No USB audio devices found, using default input device")
        try:
            default_device = pa_instance.get_default_input_device_info()['index']
            for i in range(MAX_CHANNELS):
                usb_devices[i] = default_device
        except Exception:
            for i in range(MAX_CHANNELS):
                usb_devices[i] = 0
    elif usb_count < MAX_CHANNELS:
        # Share devices if fewer than 4 USB devices
        print(f"Only {usb_count} USB device(s) found, sharing devices")
        for i in range(usb_count, MAX_CHANNELS):
            usb_devices[i] = usb_devices[i % usb_count]
    
    device_assigned = True

def get_device_for_channel(channel: str) -> int:
    """Get the assigned audio device for a specific channel"""
    auto_assign_usb_devices()
    
    # Find channel index
    channel_index = -1
    for i in range(global_channel_count):
        if channel == global_channel_ids[i]:
            channel_index = i
            break
    
    if 0 <= channel_index < MAX_CHANNELS:
        return usb_devices[channel_index]
    
    return usb_devices[0] if usb_devices[0] >= 0 else 0

def start_transmission_for_channel(audio_stream: AudioStream) -> bool:
    """Open and start PortAudio streams for a channel"""
    if pa_instance is None:
        print("PortAudio not initialized")
        return False
    
    try:
        device_index = get_device_for_channel(audio_stream.channel_id)
        audio_stream.device_index = device_index
        
        # Open input stream (48kHz, 1024 frame buffer)
        audio_stream.input_stream = pa_instance.open(
            format=pyaudio.paFloat32,
            channels=1,
            rate=SAMPLE_RATE,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=1024,
            stream_callback=None
        )
        
        # Open output stream (48kHz, 1024 frame buffer)
        try:
            audio_stream.output_stream = pa_instance.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=SAMPLE_RATE,
                output=True,
                output_device_index=device_index,
                frames_per_buffer=1024,
                stream_callback=None
            )
        except Exception as e:
            print(f"Warning: Output stream failed for {audio_stream.channel_id}: {e}")
            audio_stream.output_stream = None
        
        # Start streams
        if audio_stream.input_stream:
            audio_stream.input_stream.start_stream()
        if audio_stream.output_stream:
            audio_stream.output_stream.start_stream()
        
        audio_stream.transmitting = True
        return True
        
    except Exception as e:
        print(f"Failed to start transmission for {audio_stream.channel_id}: {e}")
        return False

def initialize_audio_devices() -> bool:
    """Initialize audio devices and kill interfering processes"""
    try:
        # Kill interfering audio processes
        try:
            subprocess.run(["pkill", "-9", "pulseaudio"], capture_output=True)
            subprocess.run(["pkill", "-9", "jack"], capture_output=True)
            subprocess.run(["pkill", "-9", "alsa"], capture_output=True)
        except Exception:
            pass
        
        # Wait for devices to stabilize
        time.sleep(1)
        
        # Verify PortAudio device enumeration
        if pa_instance:
            device_count = pa_instance.get_device_count()
            print(f"PortAudio initialized with {device_count} devices")
        
        return True
    except Exception as e:
        print(f"Audio device initialization failed: {e}")
        return False

def cleanup_audio_devices():
    """Restore audio devices to normal state"""
    try:
        # Restart PulseAudio
        subprocess.run(["pulseaudio", "--start"], capture_output=True)
    except Exception:
        pass

def init_tone_detect_control() -> bool:
    """Initialize tone detection control structure"""
    global_tone_detect.enabled = True
    global_tone_detect.card1_input_enabled = True
    global_tone_detect.passthrough_mode = False
    return True

def enable_tone_detection():
    """Enable tone detection mode"""
    with global_tone_detect.mutex:
        global_tone_detect.enabled = True
        global_tone_detect.card1_input_enabled = True
        global_tone_detect.passthrough_mode = False

def disable_tone_detection():
    """Disable tone detection mode"""
    with global_tone_detect.mutex:
        global_tone_detect.enabled = False
        global_tone_detect.card1_input_enabled = False
        global_tone_detect.passthrough_mode = False

def is_tone_detect_enabled() -> bool:
    """Check if tone detection is enabled"""
    with global_tone_detect.mutex:
        return global_tone_detect.enabled

def is_card1_input_enabled() -> bool:
    """Check if Card 1 input is enabled"""
    with global_tone_detect.mutex:
        return global_tone_detect.card1_input_enabled

def set_passthrough_output_mode(passthrough_mode: bool):
    """Switch between passthrough and EchoStream output modes"""
    with global_tone_detect.mutex:
        global_tone_detect.passthrough_mode = passthrough_mode

def init_shared_audio_buffer() -> bool:
    """Initialize shared audio buffer for passthrough"""
    global_shared_buffer.samples = np.zeros(SAMPLES_PER_FRAME, dtype=np.float32)
    global_shared_buffer.sample_count = 0
    global_shared_buffer.valid = False
    return True

def init_audio_passthrough() -> bool:
    """Initialize audio passthrough system"""
    return True

def start_audio_passthrough() -> bool:
    """Start audio passthrough (callback-based, no-op)"""
    return True

def stop_audio_passthrough():
    """Stop audio passthrough and cleanup"""
    pass

def is_configured_passthrough_channel_id(channel_id: str) -> bool:
    """Check if channel_id matches the configured passthrough target"""
    for i in range(MAX_CHANNELS):
        channel_config = config.get_channel_config(i)
        if channel_config and channel_config.valid and channel_config.tone_detect:
            tone_cfg = channel_config.tone_config
            if tone_cfg.tone_passthrough:
                # Map passthrough_channel to index
                passthrough_channel = tone_cfg.passthrough_channel
                idx = -1
                if passthrough_channel == "channel_four":
                    idx = 3
                elif passthrough_channel == "channel_three":
                    idx = 2
                elif passthrough_channel == "channel_two":
                    idx = 1
                elif passthrough_channel == "channel_one":
                    idx = 0
                
                if idx >= 0 and idx < global_channel_count:
                    return channel_id == global_channel_ids[idx]
    return False

def audio_input_worker(audio_stream: AudioStream):
    """Audio input worker thread - captures audio and sends it"""
    from echostream import global_interrupted
    
    print(f"[AUDIO] Input worker started for channel {audio_stream.channel_id}")
    
    # Check if this channel has tone detection enabled
    channel_has_tone_detect = False
    for i in range(MAX_CHANNELS):
        channel_config = config.get_channel_config(i)
        if channel_config and channel_config.valid and channel_config.tone_detect:
            if channel_config.channel_id == audio_stream.channel_id:
                channel_has_tone_detect = True
                break
    
    while not global_interrupted.is_set() and audio_stream.transmitting:
        if not audio_stream.gpio_active:
            time.sleep(0.1)
            continue
        
        if audio_stream.input_stream is None:
            time.sleep(0.1)
            continue
        
        try:
            # Read audio data (1024 frames)
            data = audio_stream.input_stream.read(1024, exception_on_overflow=False)
            if len(data) == 0:
                time.sleep(0.01)
                continue
            
            audio_data = np.frombuffer(data, dtype=np.float32)
            
            # Update shared buffer for tone detection (if enabled)
            if channel_has_tone_detect and is_tone_detect_enabled() and is_card1_input_enabled():
                with global_shared_buffer.mutex:
                    sample_count = min(len(audio_data), SAMPLES_PER_FRAME)
                    global_shared_buffer.samples[:sample_count] = audio_data[:sample_count]
                    global_shared_buffer.sample_count = sample_count
                    global_shared_buffer.valid = True
                    global_shared_buffer.data_ready.notify_all()
            
            # Accumulate samples for EchoStream transmission
            for sample in audio_data:
                if audio_stream.input_buffer_pos >= 4800:
                    audio_stream.input_buffer_pos = 0
                
                audio_stream.input_buffer[audio_stream.input_buffer_pos] = sample
                audio_stream.input_buffer_pos += 1
                
                # Encode when we have enough samples (1920 samples = 40ms at 48kHz)
                if audio_stream.input_buffer_pos >= 1920:
                    # Convert to int16 PCM
                    pcm = (audio_stream.input_buffer[:1920] * 32767.0).astype(np.int16)
                    
                    # Encode with Opus
                    if audio_stream.encoder:
                        pcm_bytes = pcm.tobytes()
                        opus_data = audio_stream.encoder.encode(pcm_bytes, 1920)
                        
                        if opus_data and len(opus_data) > 0:
                            # Encrypt
                            encrypted = crypto.encrypt_data(opus_data, bytes(audio_stream.key))
                            if encrypted:
                                # Base64 encode
                                b64_data = crypto.encode_base64(encrypted)
                                
                                # Send via UDP
                                if udp.global_udp_socket and udp.global_server_addr:
                                    msg = f'{{"channel_id":"{audio_stream.channel_id}","type":"audio","data":"{b64_data}"}}'
                                    try:
                                        udp.global_udp_socket.sendto(
                                            msg.encode('utf-8'),
                                            udp.global_server_addr
                                        )
                                    except Exception:
                                        pass
                    
                    # Reset buffer position
                    audio_stream.input_buffer_pos = 0
                    
        except Exception as e:
            if not global_interrupted.is_set():
                print(f"[AUDIO] Input error for {audio_stream.channel_id}: {e}")
            time.sleep(0.1)
    
    print(f"[AUDIO] Input worker stopped for channel {audio_stream.channel_id}")

def audio_output_worker(audio_stream: AudioStream):
    """Audio output worker thread - plays audio from jitter buffer or passthrough"""
    from echostream import global_interrupted
    
    print(f"[AUDIO] Output worker started for channel {audio_stream.channel_id}")
    
    is_passthrough_target = is_configured_passthrough_channel_id(audio_stream.channel_id)
    
    while not global_interrupted.is_set() and audio_stream.transmitting:
        if audio_stream.output_stream is None:
            time.sleep(0.1)
            continue
        
        try:
            samples_to_play = None
            
            # Check if this is passthrough target and passthrough is active
            if is_passthrough_target and tone_detect.global_tone_detection.passthrough_active:
                # Read from shared buffer (input audio from source channel)
                with global_shared_buffer.mutex:
                    if global_shared_buffer.valid and global_shared_buffer.sample_count > 0:
                        take = min(1024, global_shared_buffer.sample_count)
                        samples_to_play = global_shared_buffer.samples[:take].copy()
                        # Shift remaining samples
                        if global_shared_buffer.sample_count > take:
                            remaining = global_shared_buffer.sample_count - take
                            global_shared_buffer.samples[:remaining] = global_shared_buffer.samples[take:take+remaining]
                            global_shared_buffer.sample_count = remaining
                        else:
                            global_shared_buffer.valid = False
                            global_shared_buffer.sample_count = 0
                        
                        # Apply gain boost (15x for passthrough)
                        samples_to_play *= 15.0
                        samples_to_play = np.clip(samples_to_play, -1.0, 1.0)
            
            # If not passthrough or no passthrough data, get from jitter buffer
            if samples_to_play is None:
                output_buffer = np.zeros(1024, dtype=np.float32)
                frames_filled = 0
                
                with audio_stream.output_jitter.mutex:
                    while frames_filled < 1024:
                        if audio_stream.output_jitter.frame_count > 0:
                            frame = audio_stream.output_jitter.frames[audio_stream.output_jitter.read_index]
                            
                            if frame.valid:
                                # Calculate how many samples we can copy
                                remaining_in_frame = frame.sample_count - audio_stream.current_output_frame_pos
                                frames_to_copy = min(1024 - frames_filled, remaining_in_frame)
                                
                                # Copy samples with gain boost (1.5x for EchoStream)
                                output_gain = 1.5
                                for i in range(frames_to_copy):
                                    sample = frame.samples[audio_stream.current_output_frame_pos + i] * output_gain
                                    output_buffer[frames_filled + i] = np.clip(sample, -1.0, 1.0)
                                
                                frames_filled += frames_to_copy
                                audio_stream.current_output_frame_pos += frames_to_copy
                                
                                # Check if we finished this frame
                                if audio_stream.current_output_frame_pos >= frame.sample_count:
                                    frame.valid = False
                                    audio_stream.output_jitter.read_index = (
                                        audio_stream.output_jitter.read_index + 1
                                    ) % JITTER_BUFFER_SIZE
                                    audio_stream.output_jitter.frame_count -= 1
                                    audio_stream.current_output_frame_pos = 0
                            else:
                                # Frame invalid, skip it
                                audio_stream.output_jitter.read_index = (
                                    audio_stream.output_jitter.read_index + 1
                                ) % JITTER_BUFFER_SIZE
                                audio_stream.output_jitter.frame_count -= 1
                                audio_stream.current_output_frame_pos = 0
                        else:
                            # Buffer empty, fill with silence
                            while frames_filled < 1024:
                                output_buffer[frames_filled] = 0.0
                                frames_filled += 1
                            break
                
                samples_to_play = output_buffer
            
            # Write to output stream
            if samples_to_play is not None and len(samples_to_play) > 0:
                audio_stream.output_stream.write(samples_to_play.tobytes(), exception_on_underflow=False)
            
            time.sleep(0.01)  # Small delay to avoid busy waiting
            
        except Exception as e:
            if not global_interrupted.is_set():
                print(f"[AUDIO] Output error for {audio_stream.channel_id}: {e}")
            time.sleep(0.1)
    
    print(f"[AUDIO] Output worker stopped for channel {audio_stream.channel_id}")

def setup_channel(channel_context: ChannelContext, channel_id: str) -> bool:
    """Setup a channel with audio streams and worker threads"""
    audio_stream = channel_context.audio
    audio_stream.channel_id = channel_id
    
    # Setup audio encoder/decoder
    if not setup_audio_for_channel(audio_stream):
        return False
    
    # Start transmission
    if not start_transmission_for_channel(audio_stream):
        return False
    
    # Start input worker thread
    input_thread = threading.Thread(
        target=audio_input_worker,
        args=(audio_stream,),
        daemon=True
    )
    input_thread.start()
    
    # Start output worker thread
    output_thread = threading.Thread(
        target=audio_output_worker,
        args=(audio_stream,),
        daemon=True
    )
    output_thread.start()
    
    channel_context.active = True
    return True

