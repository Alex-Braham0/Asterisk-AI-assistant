# Gemini-SIP Gateway

A modular Python-based gateway that bridges **FreePBX/Asterisk 22** with the **Gemini 2.5 Multimodal Live API**. This system enables real-time, low-latency AI voice conversations over standard SIP extensions.

## 🛠 Tech Stack
- **Python:** 3.11.4 (Required for native `audioop` support)
- **Asterisk:** 22.6.0
- **AI Model:** `models/gemini-2.5-flash-native-audio-preview-12-2025`
- **Interface:** Asterisk REST Interface (ARI)

---

## 🚀 1. PBX Configuration (FreePBX GUI)

To route calls to this gateway, you must perform the following steps in your FreePBX interface:

### A. Enable ARI
1. Navigate to **Settings** > **Advanced Settings**.
2. Search for **Enable the Asterisk REST Interface** and set it to **Yes**.
3. Set **Asterisk REST Interface (ARI) Address** to `0.0.0.0:8088`.

### B. Create ARI User
1. Navigate to **Settings** > **Asterisk REST Interface Users**.
2. Add a new user (e.g., `alexpc`).
3. Set a strong password and ensure it matches your `config.json`.

### C. Setup Custom Destination
1. Navigate to **Admin** > **Custom Destinations**.
2. **Target:** `gemini-ai,s,1`
3. **Description:** `Gemini AI Assistant`
4. Click **Submit**.

### D. Map Feature Code (Dial 99)
1. Navigate to **Applications** > **Misc Applications**.
2. **Description:** `Call Gemini Assistant`
3. **Feature Code:** `99`
4. **Destination:** Choose **Custom Destinations** > **Gemini AI Assistant**.
5. Click **Submit** and **Apply Config**.

---

## 📞 2. Dialplan Configuration

Add the following logic to your custom dialplan file to handle the handoff.

**File:** `/etc/asterisk/extensions_custom.conf`
```ini
[gemini-ai]
exten => s,1,NoOp(Handoff to Gemini Stasis App)
 same => n,Answer()
 same => n,Stasis(my_audio_app)
 same => n,Hangup()
