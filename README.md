# RESQ — Emergency Support & Quick-Response Network

RESQ is a self-contained hackathon prototype that connects an emergency reporter to nearby available responders, open hospitals and a live control room.

## Main innovation points

### 1. 🧑‍🚒 Dynamic Help Radius
RESQ starts with an **800 m** help radius around the incident. It checks registered available responders inside that area. When enough responders are not found, the radius grows in **800 m steps**, up to the configurable demo limit (5 km by default). Responders are ordered using distance plus relevant skills rather than being notified randomly.

### 2. 🎙 Voice-first emergency report
A reporter can **type the emergency or record a voice message**. The browser recorder stores the audio with the incident. Chrome/Edge can also run speech recognition and place the spoken words into the report box, so the backend can classify the report. When transcription is unavailable, the reporter can select the emergency type and send the recorded audio anyway; responders can play it from their interface.

### 3. 🤖 AI-style Emergency Classification
The prototype uses transparent keyword-based automation rather than an LLM or medical model. A description such as:

> “A person has fallen from a bike and is bleeding.”

can be classified as:

- Category: Road accident
- Reported condition: Bleeding reported, Injury reported
- Priority: High

The system explicitly labels this as a report-based classification and **does not diagnose a medical condition**.

### 4. 🚑 Smart Route Calculation
RESQ calculates estimated travel distance and ETA for:

- Victim → responder
- Victim → open hospital

The default prototype route uses a clearly labelled **SIMULATED TRAFFIC** estimate. Optional live road routing can be enabled with OSRM by setting `ENABLE_LIVE_ROUTING=true` in `.env`.

### 5. 📡 Low-bandwidth emergency mode
The **Send essentials** button posts only essential emergency information: SOS confirmation, coordinates, selected emergency type and the persistent RESQ user ID. The project additionally sends the phone number when a callback is requested, because the system cannot place the promised call without a destination number.

### 6. 📞 Immediate control-station phone callback
After an SOS is created, RESQ can immediately call the phone number entered by the reporter using **Twilio Programmable Voice**. The call is made server-side using the configured `TWILIO_FROM_NUMBER` as the control-station caller ID and TwiML to speak the incident message. Twilio's current Calls REST API supports creating an outbound call with `POST /Accounts/{AccountSid}/Calls.json` and a `Twiml` body. See the official documentation linked in the project notes below.

Without Twilio credentials, the rest of the demo still works and the UI clearly shows that the live phone callback is not configured.

## Run on Windows

1. Install **Python 3.10+**.
2. Open a terminal in the `RESQ` folder.
3. Optionally copy `.env.example` to `.env` and add Twilio credentials.
4. Run:

```text
python app.py
```

or double-click:

```text
START_RESQ.bat
```

5. Open `http://127.0.0.1:5000/` in Chrome or Edge.

No pip packages are required; the backend uses Python standard-library modules only.

## Twilio phone-call setup

To receive the automatic callback on a real mobile phone:

1. Create a Twilio account and obtain a **voice-enabled Twilio phone number**.
2. Put these values in `.env`:

```text
CALL_ON_SOS=true
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_FROM_NUMBER=+1xxxxxxxxxx
CONTROL_STATION_NAME=RESQ Control Station
```

3. Enter the reporter's number in E.164 format, for example `+919876543210`.
4. Send SOS.
5. RESQ creates the incident, then requests an outbound call immediately. The caller ID is the configured control-station number.

For Twilio trial accounts, the destination may need to be verified according to Twilio's account requirements. A real phone call also depends on the telephony provider, phone-network availability and account permissions; the application cannot guarantee delivery when those external conditions fail.

## Demo flow

1. Open the reporter page.
2. Press **Use my location** or keep the Kozhikode demo location.
3. Enter a phone number for the callback.
4. Type or speak: **“A person has fallen from a bike and is bleeding.”**
5. Press **SOS**.
6. Show the returned category, condition, priority, dynamic help radius, responders, hospital routes and control-call status.
7. Open **Control Room** to show the incident and changing radius on the map.
8. Open **Responder**, select an available profile, then **ACCEPT**.
9. Move the status through **ON THE WAY → ARRIVED → RESOLVED**.
10. Open **Hospital** to show hospital pre-alerts.

## Reset the demo database

```text
python reset_demo.py
```

The next start of `app.py` recreates the demo data.

## Project structure

```text
RESQ/
├── app.py
├── schema.sql
├── reset_demo.py
├── requirements.txt
├── .env.example
├── .gitignore
├── START_RESQ.bat
├── README.md
├── data/
│   └── voice_notes/
├── templates/
│   ├── index.html
│   ├── control_room.html
│   ├── responder.html
│   └── hospital.html
└── static/
    ├── app.js
    └── style.css
```

## Prototype limits

Classification is transparent keyword-based automation, not a medical assessment or LLM diagnosis. Simulated routes are for demonstration and are not turn-by-turn directions. The database contains demonstration responder/hospital records. Browser speech recognition is browser-dependent. RESQ is not a certified emergency-dispatch system; real deployment requires validated emergency-service integrations, privacy/security review, telecom compliance, operational monitoring and human oversight. In an actual emergency, contact local emergency services.

## Useful route endpoint

The backend also exposes:

```text
/api/route?from_lat=11.2588&from_lon=75.7804&to_lat=11.2567&to_lon=75.7818&mode=live
```

When live routing is enabled in `.env`, this uses the configured OSRM service; otherwise it falls back to the simulated estimator.

## External references

Twilio's official Voice API documentation describes outbound calls through the Calls REST resource and supports supplying `twiml` directly in the create-call request.
