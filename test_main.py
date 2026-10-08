import asyncio
import wave
import math
import struct
from telephony.engine import MediaEngine

class DummyConfig:
    pass

def generate_beep_frame(frequency=440, sample_rate=8000, frame_size=160, t_offset=0):
    """Generates a 20ms frame of a sine wave (test tone)."""
    frame = bytearray()
    for i in range(frame_size):
        t = (t_offset + i) / sample_rate
        # 16-bit PCM, half volume (0.5)
        sample = int(32767.0 * 0.5 * math.sin(2.0 * math.pi * frequency * t))
        frame.extend(struct.pack('<h', sample))
    return bytes(frame), t_offset + frame_size

async def record_task(engine, call_instance):
    """Pulls audio from the engine queue and saves it to a WAV file."""
    print(">>> [Audio Test] 🔴 Recording started: saving to 'test_recording.wav'")
    with wave.open('test_recording.wav', 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2) # 16-bit
        wav_file.setframerate(8000)
        
        while not call_instance.ended_event.is_set():
            try:
                # Use a timeout so we can periodically check if the call ended
                chunk = await asyncio.wait_for(engine.pbx_to_ai_queue.get(), timeout=0.1)
                wav_file.writeframes(chunk)
            except asyncio.TimeoutError:
                continue
    print(">>> [Audio Test] ⏹️ Recording saved and closed.")

async def playback_task(engine, call_instance):
    """Injects a continuous 440Hz beep into the engine for the caller to hear."""
    print(">>> [Audio Test] 🔊 Playing test tone to caller...")
    t_offset = 0
    # Pre-fill the buffer with an initial 100ms safety margin
    for _ in range(5):
        frame, t_offset = generate_beep_frame(440, 8000, 160, t_offset)
        engine.inject_audio(frame)
        
    while not call_instance.ended_event.is_set():
        chunk = bytearray()
        # Generate 100ms (5 frames of 20ms) at a time
        for _ in range(5):
            frame, t_offset = generate_beep_frame(440, 8000, 160, t_offset)
            chunk.extend(frame)
            
        engine.inject_audio(bytes(chunk))
        await asyncio.sleep(0.1) # Sleep for 100ms instead of 20ms

async def handle_call_flow(engine, call_instance):
    caller = call_instance.request.headers['From']['caller']
    print(f"\n>>> [TestMain] 📞 INCOMING CALL DETECTED from: {caller}")
    print(">>> [TestMain] Attempting to answer...")
    
    # 1. Answer the call
    await engine.answer_call()
    
    # 2. Wait for Baresip to confirm the audio stream is established
    await call_instance.answered_event.wait()
    print(">>> [TestMain] 🟢 Call Answered! Audio stream established.")
    
    # 3. Fire up our two-way audio tests concurrently
    record = asyncio.create_task(record_task(engine, call_instance))
    play = asyncio.create_task(playback_task(engine, call_instance))
    
    # 4. Wait for the caller to hang up
    await call_instance.ended_event.wait()
    print("\n>>> [TestMain] 🔴 Call ended by caller.")
    
    # Ensure tasks clean up gracefully
    await asyncio.gather(record, play)

def on_inbound(engine, call_instance):
    # Route the callback into a full async flow
    asyncio.create_task(handle_call_flow(engine, call_instance))

async def main():
    loop = asyncio.get_running_loop()
    engine = MediaEngine(config=DummyConfig(), loop=loop, on_inbound_callback=on_inbound)
    
    print("Starting System...")
    engine.start()
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down test layer...")
        engine.stop()

if __name__ == "__main__":
    asyncio.run(main())