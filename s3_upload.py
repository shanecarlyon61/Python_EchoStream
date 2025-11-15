"""
S3 Upload Module - Audio recording and uploading to AWS S3
"""
import os
import struct
import time
import threading
from typing import Optional
try:
    import boto3
    S3_AVAILABLE = True
except ImportError:
    S3_AVAILABLE = False
    print("[WARNING] boto3 not available, S3 upload functionality disabled")

# Audio recording context
class AudioRecordingContext:
    def __init__(self):
        self.recording_file: Optional[object] = None
        self.is_recording = False
        self.tone_a_hz = 0.0
        self.tone_b_hz = 0.0
        self.duration_ms = 0
        self.start_time_ms = 0
        self.filename = ""
        self.sample_rate = 48000
        self.bits_per_sample = 16
        self.channels = 1
        self.samples_written = 0
        self.mutex = threading.Lock()

recording_state = AudioRecordingContext()

def write_wav_header(file, sample_rate: int, bits_per_sample: int, channels: int, data_bytes: int):
    """Write WAV file header"""
    file.write(b'RIFF')
    file.write(struct.pack('<I', 36 + data_bytes))
    file.write(b'WAVE')
    file.write(b'fmt ')
    file.write(struct.pack('<I', 16))
    file.write(struct.pack('<H', 1))
    file.write(struct.pack('<H', channels))
    file.write(struct.pack('<I', sample_rate))
    file.write(struct.pack('<I', sample_rate * channels * bits_per_sample // 8))
    file.write(struct.pack('<H', channels * bits_per_sample // 8))
    file.write(struct.pack('<H', bits_per_sample))
    file.write(b'data')
    file.write(struct.pack('<I', data_bytes))

def start_new_tone_audio_recording(tone_a_hz: float, tone_b_hz: float, duration_ms: int) -> bool:
    """
    Start recording audio for new tone detection
    
    Args:
        tone_a_hz: Frequency of tone A
        tone_b_hz: Frequency of tone B
        duration_ms: Recording duration
        
    Returns:
        True on success, False on failure
    """
    global recording_state
    
    with recording_state.mutex:
        if recording_state.is_recording:
            return False
        
        recording_state.tone_a_hz = tone_a_hz
        recording_state.tone_b_hz = tone_b_hz
        recording_state.duration_ms = duration_ms
        recording_state.start_time_ms = int(time.time() * 1000)
        recording_state.samples_written = 0
        
        # Create filename with timestamp
        timestamp = int(time.time())
        recording_state.filename = f"/tmp/tone_recording_{timestamp}_{tone_a_hz}_{tone_b_hz}.wav"
        
        try:
            recording_state.recording_file = open(recording_state.filename, 'wb')
            # Write placeholder header (will be updated when recording stops)
            write_wav_header(recording_state.recording_file, 
                           recording_state.sample_rate,
                           recording_state.bits_per_sample,
                           recording_state.channels, 0)
            recording_state.is_recording = True
            print(f"[INFO] Started new tone recording: {recording_state.filename}")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to start recording: {e}")
            return False

def write_audio_samples_to_recording(samples, sample_count: int, sample_rate: int) -> bool:
    """
    Write audio samples to active recording
    
    Args:
        samples: Audio samples (float array)
        sample_count: Number of samples
        sample_rate: Sample rate (48000)
        
    Returns:
        True on success, False on failure
    """
    global recording_state
    
    with recording_state.mutex:
        if not recording_state.is_recording or recording_state.recording_file is None:
            return False
        
        try:
            # Convert float samples to int16
            int16_samples = []
            for sample in samples[:sample_count]:
                sample = max(-1.0, min(1.0, sample))
                int16_samples.append(int(sample * 32767))
            
            # Write samples
            for sample in int16_samples:
                recording_state.recording_file.write(struct.pack('<h', sample))
            
            recording_state.samples_written += len(int16_samples)
            return True
        except Exception as e:
            print(f"[ERROR] Failed to write samples: {e}")
            return False

def stop_new_tone_audio_recording():
    """Stop new tone audio recording and finalize WAV file"""
    global recording_state
    
    with recording_state.mutex:
        if not recording_state.is_recording:
            return
        
        try:
            if recording_state.recording_file:
                # Update WAV header with actual data size
                data_bytes = recording_state.samples_written * 2  # 16-bit = 2 bytes per sample
                recording_state.recording_file.seek(0)
                write_wav_header(recording_state.recording_file,
                               recording_state.sample_rate,
                               recording_state.bits_per_sample,
                               recording_state.channels,
                               data_bytes)
                recording_state.recording_file.close()
                recording_state.recording_file = None
            
            recording_state.is_recording = False
            print(f"[INFO] Stopped new tone recording: {recording_state.filename}")
        except Exception as e:
            print(f"[ERROR] Failed to stop recording: {e}")

def upload_audio_to_s3(file_path: str, tone_a_hz: float, tone_b_hz: float) -> bool:
    """
    Upload WAV file to S3
    
    Args:
        file_path: Path to WAV file
        tone_a_hz: Tone A frequency (for metadata)
        tone_b_hz: Tone B frequency (for metadata)
        
    Returns:
        True on success, False on failure
        
    S3 Path: s3://bucket-name/recordings/{timestamp}-{tone_a}-{tone_b}.wav
    """
    if not S3_AVAILABLE:
        print("[WARNING] S3 upload not available - boto3 library not installed")
        return False
    
    try:
        s3_client = boto3.client('s3')
        
        # Generate S3 key (bucket name should be from config)
        timestamp = int(time.time())
        s3_key = f"recordings/{timestamp}-{tone_a_hz}-{tone_b_hz}.wav"
        bucket_name = "echostream-recordings"  # Should be from config
        
        # Upload file
        s3_client.upload_file(file_path, bucket_name, s3_key)
        
        print(f"[INFO] Uploaded {file_path} to S3: s3://{bucket_name}/{s3_key}")
        return True
    except Exception as e:
        print(f"[ERROR] S3 upload failed: {e}")
        return False

