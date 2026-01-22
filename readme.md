# Gemini-SIP Gateway

A modular Python gateway that connects **FreePBX/Asterisk 22** to the **Gemini 2.5 Multimodal Live API**. This project facilitates real-time, low-latency AI voice conversations over standard SIP hardware.

## 🛠 Tech Stack
- **Python:** 3.11.4 (Utilizes native `audioop`)
- **Asterisk:** 22.6.0
- **AI Model:** `models/gemini-2.5-flash-native-audio-preview-12-2025`
- **Libraries:** `websockets` v16.0, `requests` v2.32.5

---

## 🚀 System Setup

### 1. Python Environment
Ensure you are using Python 3.11.4 within your virtual environment.
```bash
# Activate your venv
.\venv\Scripts\activate

# Install locked dependencies
pip install -r requirements.txt
