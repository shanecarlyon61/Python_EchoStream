"""
Configuration Module - JSON configuration loading and parsing
"""
import json
import os
from typing import List, Optional, Dict, Any
from echostream import MAX_CHANNELS, CHANNEL_ID_LEN

# Configuration structures
class ToneDetectConfig:
    def __init__(self):
        self.tone_passthrough = False
        self.passthrough_channel = ""
        self.threshold = 0.5
        self.gain = 1.0
        self.db_threshold = 30
        self.detect_new_tones = False
        self.new_tone_length_ms = 500
        self.new_tone_range_hz = 50
        self.valid = False

class ChannelConfig:
    def __init__(self):
        self.channel_id = ""
        self.input_low_one = False
        self.input_low_two = False
        self.input_high_one = False
        self.input_high_two = False
        self.tone_detect = False
        self.tone_config = ToneDetectConfig()
        self.valid = False

class GlobalConfig:
    def __init__(self):
        self.channels = [ChannelConfig() for _ in range(MAX_CHANNELS)]
        self.valid = False

# Global configuration
global_config = GlobalConfig()

CONFIG_PATH = "/home/will/.an/config.json"

def load_channel_config(channel_ids: List[str]) -> int:
    """
    Load channel IDs from configuration file
    
    Args:
        channel_ids: Output array for channel IDs
        
    Returns:
        Number of channels loaded (0-4)
    """
    if not os.path.exists(CONFIG_PATH):
        print(f"Warning: Config file not found: {CONFIG_PATH}")
        return 0
    
    try:
        with open(CONFIG_PATH, 'r') as f:
            config_data = json.load(f)
        
        # Navigate to shadow.state.desired.software_configuration[0]
        config_obj = config_data.get("shadow", {}).get("state", {}).get("desired", {}).get("software_configuration", [])
        if not config_obj:
            print("Warning: software_configuration not found in config")
            return 0
        
        config_obj = config_obj[0]
        
        # Extract channel IDs
        count = 0
        channel_names = ["channel_one", "channel_two", "channel_three", "channel_four"]
        
        for i, channel_name in enumerate(channel_names):
            if channel_name in config_obj:
                channel_obj = config_obj[channel_name]
                channel_id = channel_obj.get("channel_id", "")
                if channel_id:
                    channel_ids[i] = channel_id
                    count += 1
        
        return count
    except Exception as e:
        print(f"Error loading channel config: {e}")
        return 0

def load_complete_config() -> bool:
    """
    Load complete configuration including tone detection settings
    
    Returns:
        True on success, False on failure
    """
    import tone_detect
    
    if not os.path.exists(CONFIG_PATH):
        print(f"Warning: Config file not found: {CONFIG_PATH}")
        return False
    
    try:
        with open(CONFIG_PATH, 'r') as f:
            config_data = json.load(f)
        
        # Navigate to shadow.state.desired.software_configuration[0]
        config_obj = config_data.get("shadow", {}).get("state", {}).get("desired", {}).get("software_configuration", [])
        if not config_obj:
            print("Warning: software_configuration not found in config")
            return False
        
        config_obj = config_obj[0]
        
        # Process each channel
        channel_names = ["channel_one", "channel_two", "channel_three", "channel_four"]
        
        for i, channel_name in enumerate(channel_names):
            if channel_name not in config_obj:
                continue
            
            channel_obj = config_obj[channel_name]
            channel_config = global_config.channels[i]
            
            # Load basic channel config
            channel_config.channel_id = channel_obj.get("channel_id", "")
            channel_config.input_low_one = channel_obj.get("input_low_one", False)
            channel_config.input_low_two = channel_obj.get("input_low_two", False)
            channel_config.input_high_one = channel_obj.get("input_high_one", False)
            channel_config.input_high_two = channel_obj.get("input_high_two", False)
            channel_config.tone_detect = channel_obj.get("tone_detect", False)
            
            if not channel_config.channel_id:
                continue
            
            # Load tone detection configuration
            if channel_config.tone_detect:
                tone_cfg_obj = channel_obj.get("tone_detect_configuration", {})
                tone_config = channel_config.tone_config
                
                tone_config.tone_passthrough = tone_cfg_obj.get("tone_passthrough", False)
                tone_config.passthrough_channel = tone_cfg_obj.get("passthrough_channel", "")
                
                # Load alert details
                alert_details = tone_cfg_obj.get("alert_details", {})
                tone_config.threshold = float(alert_details.get("threshold", "0.5"))
                tone_config.gain = float(alert_details.get("gain", "1.0"))
                tone_config.db_threshold = alert_details.get("db", 30)
                tone_config.detect_new_tones = alert_details.get("detect_new_tones", False)
                tone_config.new_tone_length_ms = alert_details.get("new_tone_length", 500)
                tone_config.new_tone_range_hz = alert_details.get("new_tone_range", 50)
                
                # Load tone definitions
                alert_tones = tone_cfg_obj.get("alert_tones", [])
                for tone_def in alert_tones:
                    tone_id = tone_def.get("tone_id", "")
                    tone_a = float(tone_def.get("tone_a", "0"))
                    tone_b = float(tone_def.get("tone_b", "0"))
                    tone_a_length = float(tone_def.get("tone_a_length", 0.5))
                    tone_b_length = float(tone_def.get("tone_b_length", 0.5))
                    tone_a_range = tone_def.get("tone_a_range", 10)
                    tone_b_range = tone_def.get("tone_b_range", 10)
                    record_length = tone_def.get("record_length", 30)
                    detection_tone_alert = tone_def.get("detection_tone_alert", "")
                    
                    # Convert lengths from seconds to milliseconds
                    tone_a_length_ms = int(tone_a_length * 1000)
                    tone_b_length_ms = int(tone_b_length * 1000)
                    record_length_ms = int(record_length * 1000)
                    
                    tone_detect.add_tone_definition(
                        tone_id, tone_a, tone_b,
                        tone_a_length_ms, tone_b_length_ms,
                        tone_a_range, tone_b_range,
                        record_length_ms, detection_tone_alert
                    )
                
                # Load frequency filters
                filters = tone_cfg_obj.get("filter_frequencies", [])
                for filter_def in filters:
                    filter_id = filter_def.get("filter_id", "")
                    frequency = float(filter_def.get("frequency", "0"))
                    filter_range = filter_def.get("filter_range", 100)
                    filter_type = filter_def.get("type", "center")
                    
                    tone_detect.add_frequency_filter(filter_id, frequency, filter_range, filter_type)
                
                # Apply tone configuration
                tone_detect.set_tone_config(
                    i,
                    tone_config.threshold,
                    tone_config.gain,
                    tone_config.db_threshold,
                    tone_config.detect_new_tones,
                    tone_config.new_tone_length_ms,
                    tone_config.new_tone_range_hz
                )
                
                tone_config.valid = True
            
            channel_config.valid = True
        
        global_config.valid = True
        return True
        
    except Exception as e:
        print(f"Error loading complete config: {e}")
        import traceback
        traceback.print_exc()
        return False

def get_channel_config(channel_index: int) -> Optional[ChannelConfig]:
    """
    Get channel configuration by index
    
    Args:
        channel_index: Channel index (0-3)
        
    Returns:
        Channel config or None if invalid
    """
    if 0 <= channel_index < MAX_CHANNELS:
        cfg = global_config.channels[channel_index]
        if cfg.valid:
            return cfg
    return None

def get_tone_detect_config(channel_index: int) -> Optional[ToneDetectConfig]:
    """
    Get tone detection configuration for a channel
    
    Args:
        channel_index: Channel index (0-3)
        
    Returns:
        Tone detect config or None if not available
    """
    cfg = get_channel_config(channel_index)
    if cfg and cfg.tone_detect and cfg.tone_config.valid:
        return cfg.tone_config
    return None

def get_device_id_from_config() -> str:
    """
    Get device ID from configuration
    
    Returns:
        Device ID string
    """
    if not os.path.exists(CONFIG_PATH):
        return ""
    
    try:
        with open(CONFIG_PATH, 'r') as f:
            config_data = json.load(f)
        
        return config_data.get("unique_id", "")
    except Exception:
        return ""

