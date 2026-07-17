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
PRESET_URL = f"http://{MATRIX_IP}/preset"
CLEAR_URL  = f"http://{MATRIX_IP}/clear"
MATRIX_TIMEOUT = (10, 15) 

LM_STUDIO_URL = "http://127.0.0.1:1234/v1/chat/completions"
MODEL_NAME = "nvidia/nemotron-3-nano-4b"

PRESETS = ["idle", "recording", "mute", "unmute", "deafen", "undeafen", "lights_on", "lights_off", "camera", "dnd", "available", "away"]
AVAILABLE_PRESETS = ", ".join(PRESETS)

SYSTEM_PROMPT = f"""
You are a helpful AI assistant connected to an LED matrix display.
You MUST return a valid JSON object containing exactly two keys: "reply" and "preset".
Available presets you can choose from: [{AVAILABLE_PRESETS}]
Do not include any extra text outside the JSON object.
"""

# --- HARDWARE ROUTING CONFIG ---
INPUT_DEVICE_INDEX = 1   # reSpeaker XVF3800 4-Mic Array
OUTPUT_DEVICE_INDEX = 2  # Mac mini Speakers
SAMPLE_RATE = 16000      # Whisper optimal rate
AUDIO_CHANNELS = 1       # Mono stream
WHISPER_MODEL_SIZE = "large-v3-turbo"

# --- NEW TRIGGER SETTINGS ---
TARGET_WAKE_WORD = "blue"
FUZZY_MATCH_THRESHOLD = 0.80  # Forgiving phonetic score threshold

print("🧠 Loading Whisper Model...")
whisper_model = whisper.load_model(WHISPER_MODEL_SIZE)
print("✅ Whisper Loaded.")

# Thread-safe queue for raw audio blocks
audio_queue = queue.Queue()

def audio_callback(indata, frames, time_info, status):
    if status:
        print(f"⚠️ Audio Status: {status}", file=sys.stderr)
    audio_queue.put(indata.copy())

# ─────────────────────────────────────────────
# HARDWARE AUDIO UTILITIES
# ─────────────────────────────────────────────

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
    result = whisper_model.transcribe(
        audio_fp32, 
        fp16=False,
        language="en",
        temperature=0.0,
        no_speech_threshold=0.01,
        logprob_threshold=-3.0,
        suppress_tokens=""
    )
    return result["text"].strip()

def transcribe_audio_normal(audio_data):
    audio_fp32 = audio_data.flatten().astype(np.float32) / 32768.0
    result = whisper_model.transcribe(audio_fp32, fp16=False, language="en")
    return result["text"].strip()

# ─────────────────────────────────────────────
# CONTROL & DATA LOGIC
# ─────────────────────────────────────────────

def wait_for_matrix_connection():
    print("\n" + "═"*50)
    print(f"🔌 Initializing connection to LED Matrix ({MATRIX_IP})...")
    print("═"*50 + "\n")
    attempt = 1
    while True:
        try:
            r = requests.get(CLEAR_URL, headers={"Connection": "close"}, timeout=(3, 5))
            if r.status_code == 200:
                print(f"\n✅ Connection established successfully!")
                play_beep(1000, 150)
                return True
        except requests.exceptions.RequestException:
            pass
        print(f"   [Attempt {attempt}] Matrix is offline. Retrying...")
        attempt += 1
        time.sleep(3)

def extract_json_objects(text: str):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON found")
    return json.loads(re.sub(r"//.*", "", match.group(0)))

def chat_turn(history, user_input):
    history.append({"role": "user", "content": user_input})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    r = requests.post(LM_STUDIO_URL, json={"model": MODEL_NAME, "messages": messages, "temperature": 0.3, "stream": False}, timeout=200)
    r.raise_for_status()
    raw_content = r.json()["choices"][0]["message"]["content"].strip()
    try:
        parsed = extract_json_objects(raw_content)
        bot_reply = parsed.get("reply", "...")
        chosen_preset = parsed.get("preset", "idle")
    except Exception:
        bot_reply = "I couldn't format that response properly."
        chosen_preset = "idle"
    history.append({"role": "assistant", "content": json.dumps({"reply": bot_reply, "preset": chosen_preset})})
    return bot_reply, chosen_preset

def send_preset(preset_name):
    if preset_name not in PRESETS: return
    try:
        r = requests.post(PRESET_URL, json={"preset": preset_name}, headers={"Connection": "close"}, timeout=MATRIX_TIMEOUT)
        print(f"[LED Matrix -> {preset_name}] HTTP {r.status_code}")
    except Exception as e:
        print(f"[Matrix transmission failed]: {e}")

# ─────────────────────────────────────────────
# THREAD-SAFE STREAMING INTERACTIVE LOOP
# ─────────────────────────────────────────────

if __name__ == "__main__":
    try:
        wait_for_matrix_connection()
    except KeyboardInterrupt:
        sys.exit(0)

    chat_history = []
    print("="*50)
    print("🤖 ASYNCHRONOUS THREADED VOICE ENGINE.")
    print(f"Trigger word configured to: '{TARGET_WAKE_WORD}'")
    print("="*50 + "\n")

    WINDOW_DURATION = 2.5  
    window_samples = int(WINDOW_DURATION * SAMPLE_RATE)
    audio_buffer = np.zeros((window_samples, 1), dtype=np.int16)

    stream = sd.InputStream(
        device=INPUT_DEVICE_INDEX,
        channels=AUDIO_CHANNELS,
        samplerate=SAMPLE_RATE,
        dtype='int16',
        callback=audio_callback,
        blocksize=4000  
    )
    
    with stream:
        print(f"👂 Listening continuously for the word '{TARGET_WAKE_WORD}'...")
        while True:
            try:
                data_blocks = []
                while not audio_queue.empty():
                    data_blocks.append(audio_queue.get_nowait())
                
                if not data_blocks:
                    time.sleep(0.1)
                    continue
                
                new_data = np.concatenate(data_blocks, axis=0)
                
                # Apply aggressive far-field microphone array gain
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
                    print(f"\n🎯 Triggered! (Heard: '{best_matching_word}' | Match Score: {highest_match_score*100:.1f}%)")
                    
                    play_beep(1200, 70)
                    time.sleep(0.02)
                    play_beep(1500, 70)
                    
                    with audio_queue.mutex:
                        audio_queue.queue.clear()
                    
                    print("🎤 Listening for your command...")
                    
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
                        print(f"📋 Parsed Command: '{clean_command}'")
                        
                        if clean_command and len(clean_command) >= 2:
                            print("🧠 Querying Local LLM...")
                            reply, preset = chat_turn(chat_history, clean_command)
                            
                            play_beep(1400, 100)
                            print(f"\nJarvis: {reply}")
                            send_preset(preset)
                            print("-" * 50 + "\n")
                        else:
                            print("❌ Command empty. Returning to standby.\n")
                    
                    audio_buffer.fill(0)
                    with audio_queue.mutex:
                        audio_queue.queue.clear()
                    print(f"👂 Resuming monitoring for '{TARGET_WAKE_WORD}'...")

            except KeyboardInterrupt:
                print("\nShutting down hardware audio stream threads.")
                break
            except Exception as e:
                print(f"\n[Loop Error]: {e}\n")
                time.sleep(1)
