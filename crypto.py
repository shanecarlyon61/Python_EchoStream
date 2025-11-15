"""
Crypto Module - Encryption, decryption, and base64 encoding/decoding
"""
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend
import os
from typing import Optional

def encode_base64(data: bytes) -> str:
    """
    Encode binary data to base64 string
    
    Args:
        data: Binary data to encode
        
    Returns:
        Base64 string
    """
    return base64.b64encode(data).decode('utf-8')

def decode_base64(input_str: str) -> bytes:
    """
    Decode base64 string to binary data
    
    Args:
        input_str: Base64 string to decode
        
    Returns:
        Decoded bytes
    """
    try:
        return base64.b64decode(input_str)
    except Exception:
        return b''

def decode_base64_len(input_str: str) -> bytes:
    """
    Decode base64 string and return decoded data
    (Same as decode_base64 but named for compatibility)
    
    Args:
        input_str: Base64 string to decode
        
    Returns:
        Decoded bytes
    """
    return decode_base64(input_str)

def encrypt_data(data: bytes, key: bytes) -> Optional[bytes]:
    """
    Encrypt data using AES-256-GCM
    
    Args:
        data: Plaintext data to encrypt
        key: 32-byte encryption key (AES-256)
        
    Returns:
        Encrypted data (IV + ciphertext + tag) or None on error
        Format: IV (12 bytes) + Ciphertext + Tag (16 bytes)
    """
    if len(key) != 32:
        print(f"Error: Key must be 32 bytes, got {len(key)}")
        return None
    
    try:
        # Generate random IV (12 bytes for GCM)
        iv = os.urandom(12)
        
        # Create AESGCM cipher
        aesgcm = AESGCM(key)
        
        # Encrypt data (returns ciphertext + tag)
        ciphertext_with_tag = aesgcm.encrypt(iv, data, None)
        
        # Return IV + ciphertext + tag
        return iv + ciphertext_with_tag
    except Exception as e:
        print(f"Encryption error: {e}")
        return None

def decrypt_data(data: bytes, key: bytes) -> Optional[bytes]:
    """
    Decrypt data using AES-256-GCM
    
    Args:
        data: Encrypted data (IV + ciphertext + tag)
        key: 32-byte decryption key (must match encryption key)
        
    Returns:
        Decrypted data or None on error
    """
    if len(data) < 28:  # Minimum: IV(12) + Tag(16)
        print(f"Error: Encrypted data too short: {len(data)} bytes")
        return None
    
    if len(key) != 32:
        print(f"Error: Key must be 32 bytes, got {len(key)}")
        return None
    
    try:
        # Extract IV (first 12 bytes) and ciphertext+tag (rest)
        iv = data[:12]
        ciphertext_with_tag = data[12:]
        
        # Create AESGCM cipher
        aesgcm = AESGCM(key)
        
        # Decrypt data (verifies tag automatically)
        plaintext = aesgcm.decrypt(iv, ciphertext_with_tag, None)
        return plaintext
    except Exception as e:
        print(f"Decryption error: {e}")
        return None

