import numpy as np

class AudioProcessor:
    @staticmethod
    def process_downlink_audio(buffer_24k: bytearray) -> tuple[bytes, bytearray]:
        """
        Extracts the largest chunk of 24kHz audio that is a multiple of 6 bytes,
        downsamples it to 8kHz, and returns the 8kHz bytes along with the remaining buffer.
        """
        # 16-bit Mono = 2 bytes per frame. 
        # Downsampling 24kHz to 8kHz means taking the mean of 3 frames (6 bytes).
        chunk_size = len(buffer_24k) - (len(buffer_24k) % 6)
        
        if chunk_size == 0:
            return b'', buffer_24k
            
        chunk_24k = bytes(buffer_24k[:chunk_size])
        remaining_buffer = bytearray(buffer_24k[chunk_size:])
        
        # Convert to numpy array
        audio_24k = np.frombuffer(chunk_24k, dtype=np.int16)
        
        # Prevent clipping (Volume adjustment)
        audio_24k = np.clip(audio_24k * 0.8, -32768, 32767).astype(np.int16)
        
        # Downsample 24kHz to 8kHz by taking the mean of every 3 frames
        audio_8k = audio_24k.reshape(-1, 3).mean(axis=1).astype(np.int16)
        
        return audio_8k.tobytes(), remaining_buffer