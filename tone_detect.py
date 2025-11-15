"""
Tone Detection Module - FFT-based tone detection and alert system
"""
import numpy as np
import threading
import time
from typing import Optional, List
from echostream import (
    MAX_CHANNELS, MAX_TONE_DEFINITIONS, MAX_FILTERS, FFT_SIZE, 
    SAMPLE_RATE, FREQ_BINS, SAMPLES_PER_FRAME
)
from numpy.fft import rfft
import config
import mqtt

# Tone definition structure
class ToneDefinition:
    def __init__(self):
        self.tone_id = ""
        self.tone_a_freq = 0.0
        self.tone_b_freq = 0.0
        self.tone_a_length_ms = 0
        self.tone_b_length_ms = 0
        self.tone_a_range_hz = 0
        self.tone_b_range_hz = 0
        self.record_length_ms = 0
        self.detection_tone_alert = ""
        self.valid = False

# Filter definition
class FrequencyFilter:
    def __init__(self):
        self.filter_id = ""
        self.frequency = 0.0
        self.filter_range_hz = 0
        self.filter_type = ""  # "above", "below", "center"
        self.valid = False

# Tone detection state
class ToneDetectionState:
    def __init__(self):
        # Tone definitions and filters
        self.tone_definitions = [ToneDefinition() for _ in range(MAX_TONE_DEFINITIONS)]
        self.filters = [FrequencyFilter() for _ in range(MAX_FILTERS)]
        
        # Detection state
        self.current_tone_a_detected = False
        self.current_tone_b_detected = False
        self.tone_sequence_active = False
        self.recording_active = False
        self.recording_start_time = 0
        self.recording_duration_ms = 0
        
        # Passthrough state
        self.passthrough_tone_a_freq = 0.0
        self.passthrough_tone_b_freq = 0.0
        self.passthrough_active = False
        
        # Duration tracking
        self.tone_a_tracking = False
        self.tone_b_tracking = False
        self.tone_a_confirmed = False
        self.tone_b_confirmed = False
        self.tone_a_tracking_start = 0
        self.tone_b_tracking_start = 0
        
        # New tone detection
        self.detected_frequencies = [0.0] * 100
        self.detected_frequency_count = 0
        
        # Thread control
        self.active = False
        self.mutex = threading.Lock()
        
        # Statistics
        self.total_detections = 0
        self.tone_a_detections = 0
        self.tone_b_detections = 0
        self.new_tone_detections = 0
        
        # Audio buffer for sliding window analysis
        self.audio_buffer = []
        self.max_buffer_samples = int(SAMPLE_RATE * 10)  # 10 seconds max
        self.last_detect_time = 0

global_tone_detection = ToneDetectionState()

def init_tone_detection() -> bool:
    """Initialize tone detection system"""
    global global_tone_detection
    if global_tone_detection is None:
        global_tone_detection = ToneDetectionState()
    if not hasattr(global_tone_detection, 'audio_buffer'):
        global_tone_detection.audio_buffer = []
        global_tone_detection.max_buffer_samples = int(SAMPLE_RATE * 10)
        global_tone_detection.last_detect_time = 0
    print("[INFO] Tone detection system initialized")
    return True

def start_tone_detection() -> bool:
    """Start tone detection thread"""
    with global_tone_detection.mutex:
        global_tone_detection.active = True
    print("[INFO] Tone detection started")
    return True

def stop_tone_detection():
    """Stop tone detection and cleanup"""
    with global_tone_detection.mutex:
        global_tone_detection.active = False
    print("[INFO] Tone detection stopped")

def add_tone_definition(tone_id: str, tone_a_freq: float, tone_b_freq: float,
                       tone_a_length: int, tone_b_length: int,
                       tone_a_range: int, tone_b_range: int,
                       record_length: int, detection_tone_alert: Optional[str]) -> bool:
    """Add a tone definition to detection system"""
    for tone_def in global_tone_detection.tone_definitions:
        if not tone_def.valid:
            tone_def.tone_id = tone_id
            tone_def.tone_a_freq = tone_a_freq
            tone_def.tone_b_freq = tone_b_freq
            tone_def.tone_a_length_ms = tone_a_length
            tone_def.tone_b_length_ms = tone_b_length
            tone_def.tone_a_range_hz = tone_a_range
            tone_def.tone_b_range_hz = tone_b_range
            tone_def.record_length_ms = record_length
            tone_def.detection_tone_alert = detection_tone_alert or ""
            tone_def.valid = True
            print(f"Added tone definition: {tone_id}")
            return True
    return False

def add_frequency_filter(filter_id: str, frequency: float, filter_range: int, filter_type: str) -> bool:
    """Add a frequency filter"""
    for filt in global_tone_detection.filters:
        if not filt.valid:
            filt.filter_id = filter_id
            filt.frequency = frequency
            filt.filter_range_hz = filter_range
            filt.filter_type = filter_type
            filt.valid = True
            print(f"Added frequency filter: {filter_id}")
            return True
    return False

def set_tone_config(channel_index: int, threshold: float, gain: float, db_threshold: int,
                   detect_new_tones: bool, new_tone_length: int, new_tone_range: int) -> bool:
    """Set tone detection configuration"""
    # Configuration is stored in config module
    print(f"Tone config set for channel {channel_index}: threshold={threshold}, gain={gain}, db={db_threshold}")
    return True

def parabolic(f, x):
    """Quadratic interpolation for estimating true position of inter-sample maximum"""
    if x == 0 or x == len(f) - 1:
        return float(x), float(f[x])
    xv = 1 / 2. * (f[x - 1] - f[x + 1]) / (f[x - 1] - 2 * f[x] + f[x + 1]) + x
    yv = f[x] - 1 / 4. * (f[x - 1] - f[x + 1]) * (xv - x)
    return xv, yv

def freq_from_fft(sig, fs=SAMPLE_RATE):
    """Estimate frequency from peak of FFT using parabolic interpolation"""
    if len(sig) < 2:
        return 0.0
    
    windowed = sig * np.hanning(len(sig))
    f = rfft(windowed)
    magnitudes = np.abs(f)
    i = np.argmax(magnitudes)
    
    if i == 0 or i >= len(magnitudes) - 1:
        return fs * i / len(windowed)
    
    try:
        true_i = parabolic(np.log(magnitudes + 1e-10), i)[0]
    except:
        true_i = float(i)
    
    return fs * true_i / len(windowed)

def is_frequency_in_range(detected_freq: float, target_freq: float, range_hz: int) -> bool:
    """Check if detected frequency is within range of target"""
    return abs(detected_freq - target_freq) <= range_hz

def trigger_tone_passthrough(tone_def: ToneDefinition):
    """Trigger audio passthrough for detected tone"""
    import audio
    
    # Enable passthrough mode
    with global_tone_detection.mutex:
        global_tone_detection.passthrough_active = True
        global_tone_detection.passthrough_tone_a_freq = tone_def.tone_a_freq
        global_tone_detection.passthrough_tone_b_freq = tone_def.tone_b_freq
    
    # Start recording timer
    if tone_def.record_length_ms > 0:
        start_recording_timer(tone_def.record_length_ms)
    
    # Enable passthrough mode in audio module
    audio.set_passthrough_output_mode(True)
    print(f"[PASSTHROUGH] Audio routing enabled for tone {tone_def.tone_id}")

def start_recording_timer(record_length_ms: int) -> bool:
    """Start recording timer after tone detection"""
    with global_tone_detection.mutex:
        global_tone_detection.recording_active = True
        global_tone_detection.recording_start_time = int(time.time() * 1000)
        global_tone_detection.recording_duration_ms = record_length_ms
    print(f"[INFO] Recording timer started: {record_length_ms} ms")
    return True

def is_recording_active() -> bool:
    """Check if recording is currently active"""
    with global_tone_detection.mutex:
        return global_tone_detection.recording_active

def get_recording_time_remaining_ms() -> int:
    """Get remaining recording time in milliseconds"""
    with global_tone_detection.mutex:
        if not global_tone_detection.recording_active:
            return 0
        elapsed = int((time.time() * 1000) - global_tone_detection.recording_start_time)
        remaining = global_tone_detection.recording_duration_ms - elapsed
        return max(0, remaining)

def process_audio_python_approach(samples: np.ndarray, sample_count: int) -> bool:
    """
    Process audio samples for tone detection (Python-style approach)
    
    Uses sliding window approach with FFT analysis
    """
    if not global_tone_detection.active:
        return False
    
    current_time_ms = int(time.time() * 1000)
    
    # Add samples to sliding window buffer
    with global_tone_detection.mutex:
        global_tone_detection.audio_buffer.extend(samples[:sample_count])
        if len(global_tone_detection.audio_buffer) > global_tone_detection.max_buffer_samples:
            global_tone_detection.audio_buffer = global_tone_detection.audio_buffer[-global_tone_detection.max_buffer_samples:]
        buffer_len = len(global_tone_detection.audio_buffer)
    
    # Need at least some audio
    if buffer_len < SAMPLE_RATE:  # Need at least 1 second
        return False
    
    # Get unique length groups from tone definitions
    lengths = []
    for tone_def in global_tone_detection.tone_definitions:
        if tone_def.valid:
            l_a = tone_def.tone_a_length_ms / 1000.0
            l_b = tone_def.tone_b_length_ms / 1000.0
            lengths.append((l_a, l_b))
    
    unique_lengths = sorted(list(set(lengths)), key=lambda x: x[0] + x[1], reverse=True)
    
    if not unique_lengths:
        return False
    
    # Process each unique length group
    for l_a, l_b in unique_lengths:
        required_samples = int((l_a + l_b) * SAMPLE_RATE)
        if len(global_tone_detection.audio_buffer) < required_samples:
            continue
        
        # Extract tone A and tone B segments
        buf_array = np.array(global_tone_detection.audio_buffer)
        start_idx = int((l_a + l_b) * SAMPLE_RATE)
        end_idx = int(l_b * SAMPLE_RATE)
        
        if start_idx <= 0 or end_idx <= 0 or start_idx <= end_idx:
            continue
        if len(buf_array) < start_idx:
            continue
        
        tone_a_segment = buf_array[-start_idx:-end_idx] if end_idx > 0 else buf_array[-start_idx:]
        tone_b_segment = buf_array[-end_idx:]
        
        if len(tone_a_segment) < int(SAMPLE_RATE * 0.1) or len(tone_b_segment) < int(SAMPLE_RATE * 0.1):
            continue
        
        # Detect frequencies using FFT
        try:
            a_tone_freq = freq_from_fft(tone_a_segment, SAMPLE_RATE)
            b_tone_freq = freq_from_fft(tone_b_segment, SAMPLE_RATE)
        except Exception:
            continue
        
        # Check against tone definitions
        tolerance = 10
        detected = False
        
        for tone_def in global_tone_detection.tone_definitions:
            if not tone_def.valid:
                continue
            
            # Check if lengths match
            if abs(tone_def.tone_a_length_ms / 1000.0 - l_a) > 0.1 or \
               abs(tone_def.tone_b_length_ms / 1000.0 - l_b) > 0.1:
                continue
            
            # Check if frequencies match
            a_match = abs(tone_def.tone_a_freq - a_tone_freq) <= max(tone_def.tone_a_range_hz, tolerance)
            b_match = abs(tone_def.tone_b_freq - b_tone_freq) <= max(tone_def.tone_b_range_hz, tolerance)
            
            # Prevent duplicate detections
            time_since_last = current_time_ms - global_tone_detection.last_detect_time
            max_tone_len_ms = max(tone_def.tone_a_length_ms, tone_def.tone_b_length_ms)
            
            if a_match and b_match and time_since_last > max_tone_len_ms:
                print("=" * 60)
                print("[🎵 TONE SEQUENCE DETECTED! 🎵]")
                print(f"  Tone ID: {tone_def.tone_id}")
                print(f"  Tone A: {a_tone_freq:.1f} Hz")
                print(f"  Tone B: {b_tone_freq:.1f} Hz")
                print("=" * 60)
                
                global_tone_detection.last_detect_time = current_time_ms
                global_tone_detection.total_detections += 1
                
                # Trigger passthrough
                trigger_tone_passthrough(tone_def)
                detected = True
                break
    
    # Check if recording timer expired
    if global_tone_detection.recording_active:
        remaining = get_recording_time_remaining_ms()
        if remaining <= 0:
            # Recording timer expired - stop passthrough
            with global_tone_detection.mutex:
                global_tone_detection.passthrough_active = False
                global_tone_detection.recording_active = False
            import audio
            audio.set_passthrough_output_mode(False)
            print("[PASSTHROUGH] Audio routing disabled")
    
    return global_tone_detection.passthrough_active

def is_tone_detect_enabled() -> bool:
    """Check if tone detection is enabled"""
    with global_tone_detection.mutex:
        return global_tone_detection.active

