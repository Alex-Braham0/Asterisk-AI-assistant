import subprocess
import sounddevice as sd

class PulseAudioRouter:
    @staticmethod
    def initialize_cables(channel_count: int):
        print("[AudioRouter] Purging existing Baresip virtual cables...")
        # Clean up dangling sinks from previous crashes
        subprocess.run("pactl list short modules | grep null-sink | cut -f1 | xargs -L1 pactl unload-module", shell=True, stderr=subprocess.DEVNULL)
        
        cables = []
        for i in range(channel_count):
            tx_name = f"Baresip_Tx_{i}"
            rx_name = f"Baresip_Rx_{i}"
            
            subprocess.run(["pactl", "load-module", "module-null-sink", f"sink_name={tx_name}", f"sink_properties=device.description={tx_name}"])
            subprocess.run(["pactl", "load-module", "module-null-sink", f"sink_name={rx_name}", f"sink_properties=device.description={rx_name}"])
            
            cables.append({"tx": tx_name, "rx": f"{rx_name}.monitor"})
            print(f"[AudioRouter] Allocated isolated cable pair for Channel {i}")
            
        return cables

    @staticmethod
    def get_sd_device_indices(tx_name: str, rx_monitor_name: str) -> tuple[int, int]:
        devices = sd.query_devices()
        in_idx = out_idx = None
        
        for idx, dev in enumerate(devices):
            if rx_monitor_name in dev['name'] and dev['max_input_channels'] > 0:
                in_idx = idx
            if tx_name in dev['name'] and dev['max_output_channels'] > 0:
                out_idx = idx
                
        if in_idx is None or out_idx is None:
            raise RuntimeError(f"PulseAudio devices {rx_monitor_name}/{tx_name} not found by sounddevice.")
            
        return in_idx, out_idx