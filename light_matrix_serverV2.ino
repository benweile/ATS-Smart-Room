#include <FastLED.h>
#include <time.h>


// --- WiFi: automatically selects depending on your board ---
#ifdef ESP32
  #include <WiFi.h>
  #include <WebServer.h>
  WebServer server(80);
#else
  #include <ESP8266WiFi.h>
  #include <ESP8266WebServer.h>
  ESP8266WebServer server(80);
#endif

#include <ArduinoJson.h>

// ---- Configuration ----
#define LED_PIN     13
#define WIDTH       8
#define HEIGHT      8
#define NUM_LEDS    64
#define BRIGHTNESS  100
#define BUTTON_PIN 32
#define POT_PIN 33

bool displayEnabled = true;
bool lastButtonState = HIGH;
unsigned long lastDebounceTime = 0;

const char* WIFI_SSID = "UD Devices";
const char* WIFI_PASS = "";

CRGB leds[NUM_LEDS];
bool isIdle = false;
bool showingHours = true;
unsigned long lastClockUpdate = 0;


// Serpentine XY mapping matrix conversion
int XY(int x, int y)
{
  int rx = 7 - y;
  int ry = 7 - x;

  if (ry % 2 == 0)
    return ry * WIDTH + rx;
  else
    return ry * WIDTH + (WIDTH - 1 - rx);
}
// =====================================================
// ICON SUPPORT
// =====================================================

void drawBitmap(const uint8_t bitmap[8], CRGB color) {
  FastLED.clear();

  for (int y = 0; y < 8; y++) {
    for (int x = 0; x < 8; x++) {
      if (bitmap[y] & (1 << (7 - x))) {
        leds[XY(x, y)] = color;
      }
    }
  }

  FastLED.show();
}

// =====================================================
// COLOR SHORTCUTS
// =====================================================

#define K CRGB::Black
#define R CRGB::Red
#define G CRGB::Green
#define B CRGB::Blue
#define Y CRGB::Yellow
#define W CRGB::White
#define O CRGB::Orange

// =====================================================
// ICONS
// =====================================================

// Recording
const CRGB ICON_RECORDING[8][8] = {
{K,K,K,K,K,K,K,K},
{K,K,R,R,R,R,K,K},
{K,R,R,R,R,R,R,K},
{K,R,R,R,R,R,R,K},
{K,R,R,R,R,R,R,K},
{K,R,R,R,R,R,R,K},
{K,K,R,R,R,R,K,K},
{K,K,K,K,K,K,K,K}
};

// Microphone
const CRGB ICON_MIC[8][8] = {
{K,K,K,W,W,K,K,K},
{K,K,W,W,W,W,K,K},
{K,K,W,W,W,W,K,K},
{K,K,W,W,W,W,K,K},
{K,K,K,W,W,K,K,K},
{K,K,K,W,W,K,K,K},
{K,K,W,W,W,W,K,K},
{K,K,K,W,W,K,K,K}
};

// Muted Microphone
const CRGB ICON_MIC_MUTE[8][8] = {
{R,K,K,W,W,K,K,K},
{K,R,W,W,W,W,K,K},
{K,K,R,W,W,W,K,K},
{K,K,W,R,W,W,K,K},
{K,K,K,W,R,K,K,K},
{K,K,K,W,W,R,K,K},
{K,K,W,W,W,W,R,K},
{K,K,K,W,W,K,K,R}
};

// Speaker
const CRGB ICON_SPEAKER[8][8] = {
{K,K,K,W,K,K,K,K},
{K,K,W,W,K,K,K,K},
{K,W,W,W,W,K,K,K},
{W,W,W,W,W,W,K,K},
{W,W,W,W,W,W,K,K},
{K,W,W,W,W,K,K,K},
{K,K,W,W,K,K,K,K},
{K,K,K,W,K,K,K,K}
};

// Muted Speaker
const CRGB ICON_SPEAKER_MUTE[8][8] = {
{K,K,K,K,W,W,W,R},
{K,K,K,W,K,K,R,K},
{W,W,W,K,K,R,W,K},
{W,K,K,K,R,K,W,K},
{W,K,K,R,K,K,W,K},
{W,W,R,K,K,K,W,K},
{K,R,K,W,K,K,W,K},
{R,K,K,K,W,W,W,K}
};

// Light Bulb On
const CRGB ICON_LIGHT_ON[8][8] = {
{K,K,Y,Y,Y,Y,K,K},
{K,Y,Y,Y,Y,Y,Y,K},
{K,Y,Y,Y,Y,Y,Y,K},
{K,K,Y,Y,Y,Y,K,K},
{K,K,K,Y,Y,K,K,K},
{K,K,K,Y,Y,K,K,K},
{K,K,Y,Y,Y,Y,K,K},
{K,K,K,K,K,K,K,K}
};

// Light Bulb Off
const CRGB ICON_LIGHT_OFF[8][8] = {
{R,K,Y,Y,Y,Y,K,K},
{K,R,Y,Y,Y,Y,Y,K},
{K,Y,R,Y,Y,Y,Y,K},
{K,K,Y,R,Y,Y,K,K},
{K,K,K,Y,R,K,K,K},
{K,K,K,Y,Y,R,K,K},
{K,K,Y,Y,Y,Y,R,K},
{K,K,K,K,K,K,K,R}
};

// Camera
const CRGB ICON_CAMERA[8][8] = {
{K,K,W,W,W,W,K,K},
{W,W,W,K,K,W,W,W},
{W,K,K,W,W,K,K,W},
{W,K,W,B,B,W,K,W},
{W,K,W,B,B,W,K,W},
{W,K,K,W,W,K,K,W},
{W,K,K,K,K,K,K,W},
{W,W,W,W,W,W,W,W}
};

// Do Not Disturb
const CRGB ICON_DND[8][8] = {
{K,K,R,G,K,G,K,K},
{K,R,G,K,G,G,G,G},
{K,K,W,W,G,G,G,G},
{K,R,W,W,G,G,G,G},
{K,K,R,W,W,W,W,K},
{G,R,G,G,W,W,G,K},
{K,G,G,G,W,W,G,G},
{K,K,R,R,K,R,R,K}
};

// Available
const CRGB ICON_AVAILABLE[8][8] = {
{K,K,K,K,K,K,K,K},
{K,K,K,K,K,K,G,K},
{K,K,K,K,K,G,G,K},
{K,K,K,K,G,G,K,K},
{G,K,K,G,G,K,K,K},
{K,G,G,G,K,K,K,K},
{K,K,G,K,K,K,K,K},
{K,K,K,K,K,K,K,K}
};

// Away
const CRGB ICON_AWAY[8][8] = {
{O,K,K,K,K,K,K,O},
{K,O,K,K,K,K,O,K},
{K,K,O,K,K,O,K,K},
{K,K,K,O,O,K,K,K},
{K,K,K,O,O,K,K,K},
{K,K,O,K,K,O,K,K},
{K,O,K,K,K,K,O,K},
{O,K,K,K,K,K,K,O}
};

// ─────────────────────────────────────────────
// HARDCODED PRESET DESIGNS
// ─────────────────────────────────────────────
void drawIcon(const CRGB icon[8][8]) {

  for (int y = 0; y < 8; y++) {
    for (int x = 0; x < 8; x++) {
      leds[XY(x, y)] = icon[y][x];
    }
  }

  FastLED.show();
}
const uint8_t FONT_3x5[10][5] = {
  {0b111,0b101,0b101,0b101,0b111},
  {0b010,0b110,0b010,0b010,0b111},
  {0b111,0b001,0b111,0b100,0b111},
  {0b111,0b001,0b111,0b001,0b111},
  {0b101,0b101,0b111,0b001,0b001},
  {0b111,0b100,0b111,0b001,0b111},
  {0b111,0b100,0b111,0b101,0b111},
  {0b111,0b001,0b010,0b010,0b010},
  {0b111,0b101,0b111,0b101,0b111},
  {0b111,0b101,0b111,0b001,0b111}
};

void drawDigit(int digit, int xOffset, CRGB color)
{
  for (int y = 0; y < 5; y++) {
    for (int x = 0; x < 3; x++) {
      if (FONT_3x5[digit][y] & (1 << (2 - x))) {
        leds[XY(x + xOffset, y + 1)] = color;
      }
    }
  }
}

void displayClock()
{
  struct tm timeinfo;

  if (!getLocalTime(&timeinfo)) {
    FastLED.clear();
    FastLED.show();
    return;
  }

  int value = showingHours ? timeinfo.tm_hour : timeinfo.tm_min;

  FastLED.clear();

  int tens = value / 10;
  int ones = value % 10;

  drawDigit(tens, 0, CRGB::Blue);
  drawDigit(ones, 5, CRGB::Blue);

  FastLED.show();
}

void displayPreset(String presetName) {

  if (presetName == "idle") {
    isIdle = true;
    showingHours = true;
    lastClockUpdate = 0;
    displayClock();
    return;
}

isIdle = false;

  if (presetName == "recording") {
    drawIcon(ICON_RECORDING);
  }

  else if (presetName == "mute") {
    drawIcon(ICON_MIC_MUTE);
  }

  else if (presetName == "unmute") {
    drawIcon(ICON_MIC);
  }

  else if (presetName == "deafen") {
    drawIcon(ICON_SPEAKER_MUTE);
  }

  else if (presetName == "undeafen") {
    drawIcon(ICON_SPEAKER);
  }

  else if (presetName == "lights_on") {
    drawIcon(ICON_LIGHT_ON);
  }

  else if (presetName == "lights_off") {
    drawIcon(ICON_LIGHT_OFF);
  }

  else if (presetName == "camera") {
    drawIcon(ICON_CAMERA);
  }

  else if (presetName == "dnd") {
    drawIcon(ICON_DND);
  }

  else if (presetName == "available") {
    drawIcon(ICON_AVAILABLE);
  }

  else if (presetName == "away") {
    drawIcon(ICON_AWAY);
  }

  else {
    FastLED.clear();
    FastLED.show();
  }
}

// ─────────────────────────────────────────────
// HTTP HANDLERS
// ─────────────────────────────────────────────

// POST /preset
// Body JSON format: { "preset": "recording" }
void handlePreset() {
  if (server.method() != HTTP_POST) {
    server.send(405, "text/plain", "Method not allowed");
    return;
  }

  String body = server.arg("plain");
  StaticJsonDocument<256> doc; // Small buffer size since strings are tiny
  DeserializationError err = deserializeJson(doc, body);
  
  if (err) {
    server.send(400, "text/plain", String("JSON error: ") + err.c_str());
    return;
  }

  const char* preset = doc["preset"];
  if (!preset) {
    server.send(400, "text/plain", "Missing 'preset' key in JSON body");
    return;
  }

  displayPreset(String(preset));
  server.send(200, "text/plain", "Preset applied");
}

// POST /draw (Maintained for backward compatibility and raw overrides)
void handleDraw() {
  if (server.method() != HTTP_POST) {
    server.send(405, "text/plain", "Method not allowed");
    return;
  }

  String body = server.arg("plain");
  StaticJsonDocument<4096> doc;
  DeserializationError err = deserializeJson(doc, body);
  if (err) {
    server.send(400, "text/plain", String("JSON error: ") + err.c_str());
    return;
  }

  JsonArray pixels = doc["pixels"];
  if (pixels.isNull() || pixels.size() < NUM_LEDS) {
    server.send(400, "text/plain", "Need 64 [r,g,b] entries in 'pixels'");
    return;
  }

  for (int i = 0; i < NUM_LEDS; i++) {
    JsonArray rgb = pixels[i];
    int r = constrain((int)rgb, 0, 255);
    int g = constrain((int)rgb, 0, 255);
    int b = constrain((int)rgb, 0, 255);
    int x = i % WIDTH;
    int y = i / WIDTH;
    leds[XY(x, y)] = CRGB(r, g, b);
  }
  FastLED.show();
  server.send(200, "text/plain", "OK");
}

// GET /clear
void handleClear() {
  FastLED.clear();
  FastLED.show();
  server.send(200, "text/plain", "Cleared");
}

// GET /
void handleRoot() {
  server.send(200, "text/plain",
    String("LED Matrix Server\nIP: ") + WiFi.localIP().toString());
}

void handleControls()
{
  // ----- Brightness from potentiometer -----
  int potValue = analogRead(POT_PIN);

  int brightness = map(potValue, 0, 4095, 0, 255);

  FastLED.setBrightness(brightness);

  if (displayEnabled)
  {
    FastLED.show();
  }

  // ----- Button toggle -----
  bool buttonState = digitalRead(BUTTON_PIN);

  if (buttonState == LOW &&
      lastButtonState == HIGH &&
      millis() - lastDebounceTime > 250)
  {
    displayEnabled = !displayEnabled;

    if (!displayEnabled)
    {
      FastLED.clear();
      FastLED.show();
    }
    else
    {
      displayPreset("idle");
    }

    lastDebounceTime = millis();
  }

  lastButtonState = buttonState;
}

// ─────────────────────────────────────────────
// SETUP & LOOP
// ─────────────────────────────────────────────

void setup() {
  Serial.begin(115200);

  FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(POT_PIN, INPUT);
  FastLED.setBrightness(BRIGHTNESS);
  FastLED.clear();
  FastLED.show();

  WiFi.begin(WIFI_SSID);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Connected! IP: ");
  Serial.println(WiFi.localIP());
  
  configTime(
  -5 * 3600,   // EST offset
  3600,        // DST
  "pool.ntp.org",
  "time.nist.gov"
);

Serial.println("Waiting for NTP time...");

struct tm timeinfo;
if (!getLocalTime(&timeinfo, 10000)) {
  Serial.println("Failed to get time!");
} else {
  Serial.println(&timeinfo, "%A, %B %d %Y %H:%M:%S");
}

  // Quick flash check
  fill_solid(leds, NUM_LEDS, CRGB::Green);
  FastLED.show();
  delay(500);
  displayPreset("idle"); // Boot up into the standard idle state

  // Route API targets
  server.on("/",       HTTP_GET,  handleRoot);
  server.on("/draw",   HTTP_POST, handleDraw);
  server.on("/preset", HTTP_POST, handlePreset);
  server.on("/clear",  HTTP_GET,  handleClear);
  
  server.begin();
  Serial.println("HTTP server started on port 80");
}

void loop()
{
  server.handleClient();

  handleControls();

  if (isIdle && displayEnabled)
  {
    if (millis() - lastClockUpdate > 2000)
    {
      showingHours = !showingHours;
      displayClock();
      lastClockUpdate = millis();
    }
  }
}