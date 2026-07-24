import json
import re
import sys
import requests
import numpy as np
import sounddevice as sd
import time
import whisper
import difflib
import queue

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

MATRIX_IP = "192.168.2.2"
MATRIX_PRESET_URL = f"http://{MATRIX_IP}/preset"
MATRIX_CLEAR_URL  = f"http://{MATRIX_IP}/clear"

WHITEBOARD_IP = "192.168.2.3"
WHITEBOARD_PRESET_URL = f"http://{WHITEBOARD_IP}/preset"
WHITEBOARD_CLEAR_URL  = f"http://{WHITEBOARD_IP}/clear"

GLOBAL_TIMEOUT = (10, 15) 

IDLE_TIMEOUT_SECONDS = 60.0  
current_state = "idle"       
last_state_change_time = time.time()

LM_STUDIO_URL = "http://127.0.0.1:1234/v1/chat/completions"
MODEL_NAME = "nvidia/nemotron-3-nano-4b"

PRESETS = ["idle", "recording", "mute", "unmute", "deafen", "undeafen", "lights_on", "lights_off", "camera", "dnd", "available", "away", "custom"]
AVAILABLE_PRESETS = ", ".join(PRESETS)

SYSTEM_PROMPT = f"""
You are Jarvis, a precision home automation voice assistant. You control a connected LED Matrix and a Whiteboard Light Strip.

You MUST respond with a valid, clean JSON object containing exactly three keys: "reply", "preset", and "custom_rgb".

Rules for choosing values:
1. If the user asks for a specific room ambiance or direct color change (e.g., "turn the lights purple", "make it a warm yellow glow", "set color to neon cyan"):
   - Set "preset" to "custom"
   - Provide the approximate 8-bit color translation in "custom_rgb" as an integer array: [R, G, B] (values 0-255).
2. If no direct color control is asked, map the query to an abstract state and set "custom_rgb" to null:
   - "lights_on" / "available" -> System active / White layout.
   - "recording" / "mute" / "dnd" -> User busy; turn systems Red.
   - "deafen" / "away" -> Orange layout.
   - "idle" -> Standby soft blue layout.
   - "lights_off" -> Turn off displays.

Available presets choice: [{AVAILABLE_PRESETS}]
Do not add markdown styling, backticks, text, or explanations outside the JSON block.
"""

# --- HARDWARE ROUTING CONFIG ---
INPUT_DEVICE_INDEX = 1   
OUTPUT_DEVICE_INDEX = 2  
SAMPLE_RATE = 16000      
AUDIO_CHANNELS = 1       
WHISPER_MODEL_SIZE = "large-v3"

# --- TRIGGER SETTINGS ---
TARGET_WAKE_WORD = "blue"
FUZZY_MATCH_THRESHOLD = 0.76  

print("🧠 Loading Whisper Model...")
whisper_model = whisper.load_model(WHISPER_MODEL_SIZE)
print("✅ Whisper Loaded.")

audio_queue = queue.Queue()

def audio_callback(indata, frames, time_info, status):
    if status:
        print(f"⚠️ Audio Status: {status}", file=sys.stderr)
    audio_queue.put(indata.copy())

def play_beep(frequency, duration_ms):
    try:
        sample_rate = 44100
        duration_sec = duration_ms / 1000.0
        t = np.linspace(0, duration_sec, int(sample_rate * duration_sec), False)
        wave = np.sin(frequency * t * 2 * np.pi)
        
        fade = int(sample_rate * 0.01)
        fade_window = np.ones(len(wave))
        fade_window[:fade] = np.linspace(0, 1, fade)
        fade_window[-fade:] = np.linspace(1, 0, fade)
        wave = wave * fade_window
        
        sd.play(wave.astype(np.float32), sample_rate, device=OUTPUT_DEVICE_INDEX)
        sd.wait()
    except Exception:
        pass

def transcribe_audio_hyper_sensitive(audio_data):
    audio_fp32 = audio_data.flatten().astype(np.float32) / 32768.0
    result = whisper_model.transcribe(audio_fp32, fp16=False, language="en", temperature=0.0, no_speech_threshold=0.01, logprob_threshold=-3.0, suppress_tokens="")
    return result["text"].strip()

def transcribe_audio_normal(audio_data):
    audio_fp32 = audio_data.flatten().astype(np.float32) / 32768.0
    result = whisper_model.transcribe(audio_fp32, fp16=False, language="en")
    return result["text"].strip()

def wait_for_hardware_connections():
    print("\n" + "═"*50)
    print(f"🔌 Initializing precision dual connections...")
    print("═"*50 + "\n")
    matrix_online = False
    whiteboard_online = False
    attempt = 1
    while not (matrix_online and whiteboard_online):
        if not matrix_online:
            try:
                r = requests.get(MATRIX_CLEAR_URL, headers={"Connection": "close"}, timeout=(2, 3))
                if r.status_code == 200:
                    print("✅ LED Matrix connected.")
                    matrix_online = True
            except requests.exceptions.RequestException:
                pass
        if not whiteboard_online:
            try:
                r = requests.get(WHITEBOARD_CLEAR_URL, headers={"Connection": "close"}, timeout=(2, 3))
                if r.status_code == 200:
                    print("✅ Whiteboard Lights connected.")
                    whiteboard_online = True
            except requests.exceptions.RequestException:
                pass
        if not (matrix_online and whiteboard_online):
            print(f"   [Attempt {attempt}] Peripherals offline. Retrying...")
            attempt += 1
            time.sleep(3)
    print("\n🚀 System Ready!")
    play_beep(1000, 150)
    return True

def extract_json_objects(text: str):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON found")
    return json.loads(re.sub(r"//.*", "", match.group(0)))

def chat_turn(history, user_input):
    history.append({"role": "user", "content": user_input})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    r = requests.post(LM_STUDIO_URL, json={"model": MODEL_NAME, "messages": messages, "temperature": 0.2, "stream": False}, timeout=200)
    r.raise_for_status()
    raw_content = r.json()["choices"][0]["message"]["content"].strip()
    try:
        parsed = extract_json_objects(raw_content)
        bot_reply = parsed.get("reply", "...")
        chosen_preset = parsed.get("preset", "idle")
        custom_rgb = parsed.get("custom_rgb", None)
    except Exception:
        bot_reply = "Formatting error."
        chosen_preset = "idle"
        custom_rgb = None
    history.append({"role": "assistant", "content": json.dumps({"reply": bot_reply, "preset": chosen_preset, "custom_rgb": custom_rgb})})
    return bot_reply, chosen_preset, custom_rgb

def broadcast_hardware_command(preset_name, custom_rgb=None):
    global current_state, last_state_change_time
    if preset_name not in PRESETS: return
    
    current_state = preset_name
    last_state_change_time = time.time()
    
    payload = {}
    if preset_name == "custom" and isinstance(custom_rgb, list) and len(custom_rgb) == 3:
        payload = {"r": int(custom_rgb[0]), "g": int(custom_rgb[1]), "b": int(custom_rgb[2])}
    else:
        payload = {"preset": preset_name}
    
    # Send to Matrix
    try:
        requests.post(MATRIX_PRESET_URL, json=payload, headers={"Connection": "close"}, timeout=GLOBAL_TIMEOUT)
    except Exception as e:
        print(f"[Matrix transmission failed]: {e}")
        
    # Send to Whiteboard Frame
    try:
        requests.post(WHITEBOARD_PRESET_URL, json=payload, headers={"Connection": "close"}, timeout=GLOBAL_TIMEOUT)
    except Exception as e:
        print(f"[Whiteboard transmission failed]: {e}")

if __name__ == "__main__":
    try:
        wait_for_hardware_connections()
    except KeyboardInterrupt:
        sys.exit(0)

    chat_history = []
    WINDOW_DURATION = 2.5  
    window_samples = int(WINDOW_DURATION * SAMPLE_RATE)
    audio_buffer = np.zeros((window_samples, 1), dtype=np.int16)

    stream = sd.InputStream(device=INPUT_DEVICE_INDEX, channels=AUDIO_CHANNELS, samplerate=SAMPLE_RATE, dtype='int16', callback=audio_callback, blocksize=4000)
    
    with stream:
        print(f"👂 Listening continuously for '{TARGET_WAKE_WORD}'...")
        while True:
            try:
                # ─── AUTO-IDLE CONTROLLER ───
                if current_state != "idle" and current_state != "recording":
                    if time.time() - last_state_change_time > IDLE_TIMEOUT_SECONDS:
                        print(f"\n⏳ Auto-reverting displays to standby 'idle'...")
                        broadcast_hardware_command("idle")

                data_blocks = []
                while not audio_queue.empty():
                    data_blocks.append(audio_queue.get_nowait())
                
                if not data_blocks:
                    time.sleep(0.1)
                    continue
                
                new_data = np.concatenate(data_blocks, axis=0)
                new_data_boosted = np.clip(new_data.astype(np.float32) * 6.0, -32768.0, 32767.0).astype(np.int16)
                
                num_new_samples = len(new_data_boosted)
                if num_new_samples >= window_samples:
                    audio_buffer = new_data_boosted[-window_samples:]
                else:
                    audio_buffer = np.roll(audio_buffer, -num_new_samples, axis=0)
                    audio_buffer[-num_new_samples:] = new_data_boosted
                
                raw_text = transcribe_audio_hyper_sensitive(audio_buffer).lower().strip(".,!? ")
                if not raw_text:
                    continue
                
                words_heard = raw_text.split()
                highest_match_score = 0.0
                best_matching_word = ""
                
                for word in words_heard:
                    score = difflib.SequenceMatcher(None, word, TARGET_WAKE_WORD).ratio()
                    if score > highest_match_score:
                        highest_match_score = score
                        best_matching_word = word
                
                if highest_match_score >= FUZZY_MATCH_THRESHOLD:
                    print(f"\n🎯 Triggered! ({best_matching_word})")
                    play_beep(1200, 70)
                    time.sleep(0.02)
                    play_beep(1500, 70)
                    
                    with audio_queue.mutex:
                        audio_queue.queue.clear()
                    
                    print("🎤 Listening...")
                    command_samples_needed = int(6.0 * SAMPLE_RATE)
                    command_blocks = []
                    samples_collected = 0
                    start_time = time.time()
                    
                    while samples_collected < command_samples_needed:
                        if time.time() - start_time > 8.0:
                            break
                        try:
                            block = audio_queue.get(timeout=0.5)
                            command_blocks.append(block)
                            samples_collected += len(block)
                        except queue.Empty:
                            continue
                    
                    if command_blocks:
                        command_audio = np.concatenate(command_blocks, axis=0)
                        command_audio = np.clip(command_audio.astype(np.float32) * 3.5, -32768.0, 32767.0).astype(np.int16)
                        clean_command = transcribe_audio_normal(command_audio).strip()
                        print(f"📋 Command: '{clean_command}'")
                        
                        if clean_command and len(clean_command) >= 2:
                            reply, preset, custom_rgb = chat_turn(chat_history, clean_command)
                            play_beep(1400, 100)
                            print(f"\nJarvis: {reply}")
                            broadcast_hardware_command(preset, custom_rgb)
                        else:
                            print("❌ Command empty.\n")
                    
                    audio_buffer.fill(0)
                    with audio_queue.mutex:
                        audio_queue.queue.clear()

            except KeyboardInterrupt:
                break
            except Exception as e:
                time.sleep(1)
