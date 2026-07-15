import json
import re
import requests
import numpy as np
import sounddevice as sd
import speech_recognition as sr
import openwakeword
from openwakeword.model import Model
import time

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

MATRIX_IP = "172.20.10.9"
PRESET_URL = f"http://{MATRIX_IP}/preset"
CLEAR_URL  = f"http://{MATRIX_IP}/clear"

LM_STUDIO_URL = "http://127.0.0.1:1234/v1/chat/completions"
MODEL_NAME = "google/gemma-4-e4b"

PRESETS = ["idle", "recording", "mute", "unmute", "deafen", "undeafen", "lights_on", "lights_off", "camera", "dnd", "available", "away"]
AVAILABLE_PRESETS = ", ".join(PRESETS)

SYSTEM_PROMPT = f"""
You are a helpful AI assistant connected to an LED matrix display.
You MUST return a valid JSON object containing exactly two keys: "reply" and "preset".
Available presets you can choose from: [{AVAILABLE_PRESETS}]
Example Output format:
{{
  "reply": "I am turning on the recording light now.",
  "preset": "recording"
}}
Do not include any extra text outside the JSON object.
"""

# ─────────────────────────────────────────────
# CROSS-PLATFORM BEEP (MAC COMPATIBLE)
# ─────────────────────────────────────────────

def play_beep(frequency, duration_ms):
    """Generates and plays a sine wave beep using sounddevice."""
    sample_rate = 44100
    duration_sec = duration_ms / 1000.0
    t = np.linspace(0, duration_sec, int(sample_rate * duration_sec), False)
    # Generate a comfortable sine wave tone
    wave = np.sin(frequency * t * 2 * np.pi)
    # Smooth the start and end slightly to prevent harsh clicking sounds
    fade = int(sample_rate * 0.01)  # 10ms fade
    fade_window = np.ones(len(wave))
    fade_window[:fade] = np.linspace(0, 1, fade)
    fade_window[-fade:] = np.linspace(1, 0, fade)
    wave = wave * fade_window
    
    # Play the audio and block until it finishes
    sd.play(wave.astype(np.float32), sample_rate)
    sd.wait()

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def extract_json_objects(text: str):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON found")
    clean = re.sub(r"//.*", "", match.group(0))
    return json.loads(clean)

def chat_turn(history, user_input):
    history.append({"role": "user", "content": user_input})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    r = requests.post(
        LM_STUDIO_URL,
        json={
            "model": MODEL_NAME,
            "messages": messages,
            "temperature": 0.3,
            "stream": False
        },
        timeout=200
    )
    r.raise_for_status()
    resp = r.json()
    raw_content = resp["choices"][0]["message"]["content"].strip()

    try:
        parsed = extract_json_objects(raw_content)
        bot_reply = parsed.get("reply", "...")
        chosen_preset = parsed.get("preset", "idle")
    except Exception:
        print("\n[Parse failed]\n", raw_content)
        bot_reply = "I couldn't format that response properly."
        chosen_preset = "idle"

    history.append({
        "role": "assistant",
        "content": json.dumps({"reply": bot_reply, "preset": chosen_preset})
    })

    return bot_reply, chosen_preset

def send_preset(preset_name):
    if preset_name not in PRESETS:
        return
    try:
        requests.post(PRESET_URL, json={"preset": preset_name}, timeout=5)
        print(f"[LED Matrix -> {preset_name}]")
    except Exception as e:
        print("[Matrix error]", e)

def clear_matrix():
    try:
        requests.get(CLEAR_URL, timeout=5)
        print("[Matrix cleared]")
    except Exception as e:
        print("[Matrix error]", e)

# ─────────────────────────────────────────────
# MAIN STREAM & WAKE WORD PIPELINE
# ─────────────────────────────────────────────

if __name__ == "__main__":
    chat_history = []
    recognizer = sr.Recognizer()
        
    print("Initializing Jarvis wake word engine...")
    openwakeword.utils.download_models() 
    
    oww_model = Model(wakeword_models=["jarvis"], inference_framework="onnx")
    
    FORMAT_SAMPLE_RATE = 16000 
    CHUNK_SIZE = 1280  
    
    print("\n🤖 Jarvis LED Matrix System Active.")
    print("Say 'Jarvis' out loud to trigger the AI...\n")

    audio_buffer = []
    last_callback_time = time.time()

    def audio_callback(indata, frames, time_info, status):
        global last_callback_time
        if status:
            print(f"[Stream Status Warning]: {status}")
        audio_buffer.append(indata.copy())
        last_callback_time = time.time()

    # Start streaming audio from your microphone
    with sd.InputStream(samplerate=FORMAT_SAMPLE_RATE, channels=1, dtype='int16', 
                        blocksize=CHUNK_SIZE, callback=audio_callback):
        while True:
            # 🛡️ STREAM GUARD: If the hardware stream goes silent/stalls for over 3 seconds, flush it
            if time.time() - last_callback_time > 3.0:
                if len(audio_buffer) > 0:
                    audio_buffer.clear()
                    oww_model.reset()

            # Prevent buffer buildup backlog from running away if processing falls behind
            if len(audio_buffer) > 20: 
                # Keep only the newest chunks so we don't lag behind real-time speech
                audio_buffer = audio_buffer[-5:]

            if len(audio_buffer) > 0:
                chunk = audio_buffer.pop(0).flatten()
                prediction = oww_model.predict(chunk)
                
                if prediction.get("jarvis", 0) > 0.6:
                    print("\n✨ [Wake Word Detected: Jarvis!]")
                    
                    # Custom Mac-friendly beep sound effect
                    play_beep(1500, 80)
                    play_beep(2000, 80)
                    
                    print("Listening for your command...")
                    DURATION = 5
                    command_audio = sd.rec(int(DURATION * FORMAT_SAMPLE_RATE), 
                                           samplerate=FORMAT_SAMPLE_RATE, 
                                           channels=1, dtype='int16')
                    sd.wait() 
                    print("Processing prompt...")
                    
                    try:
                        audio_bytes = command_audio.tobytes()
                        audio_data = sr.AudioData(audio_bytes, FORMAT_SAMPLE_RATE, sample_width=2)
                        
                        user_msg = recognizer.recognize_google(audio_data).strip()
                        print(f"You said: {user_msg}")
                        
                        if user_msg.lower() == "clear":
                            clear_matrix()
                        else:
                            reply, preset = chat_turn(chat_history, user_msg)
                            print(f"Jarvis: {reply}")
                            send_preset(preset)
                        
                    except sr.UnknownValueError:
                        print("[System did not understand the audio command]")
                    except Exception as e:
                        print(f"[Processing Error]: {e}")
                    
                    print("\nResetting wake-word listener...")
                    audio_buffer.clear()
                    oww_model.reset()
                    print("Listening for 'Jarvis'...")
            else:
                # Keep CPU usage tiny while waiting for data chunks
                time.sleep(0.01)
