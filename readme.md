# International 3-Wire Impulse Clock Controller (A/B/C) with Web UI

Raspberry Pi–based controller for a vintage **International (pre-IBM) 3-wire impulse clock** movement.

This project drives the traditional **A / B / C** system using two relays and provides a built-in **web interface** for monitoring, setting the dial, and performing fast correction.

---

## Features

- Correct **International 3-wire impulse logic**
- Minute-accurate, NTP-disciplined timing
- Automatic **:59 correction burst**
- Web UI for:
  - Live status
  - Setting the dial reading
  - **FAST SET** (advance quickly if slow)
  - **STOP CORRECTION** (immediate cancel)
  - Manual pulse testing
- Persistent dial offset storage
- Designed for unattended, always-on operation

Web UI:


http://<raspberry-pi-ip>:8081


---

## How the clock logic works

### Normal operation
- Every minute at `second == 0`
  - **A pulse** is sent
- On minutes `00–49`
  - **B pulse** is also sent
- At minute `59`
  - **17 additional A pulses**, spaced **2 seconds apart**
  - This occurs **once per hour**

The **C wire** is the common return and is **not switched** by the Pi.

---

## FAST SET behavior (Web UI)

FAST SET dynamically aligns the dial with system time:

- **If the dial is behind**
  - Advances using **A + B together** every 2 seconds
  - Recomputes remaining offset each pulse so elapsed real time is accounted for
- **If the dial is ahead**
  - The clock cannot reverse
  - Controller **STALLS** (skips normal minute pulses) until time catches up
- **STOP CORRECTION**
  - Immediately cancels FAST SET
  - De-energizes all relays

The controller tracks the dial using an **offset in minutes**:



dial_minutes = system_minutes + offset_minutes (mod 720)


Offset is saved to:


~/master-clock/international_state.json


---

## Hardware

### Required
- Raspberry Pi (Pi 3 / 4 / 5 recommended)
- Relay board (Waveshare or similar)
- Clock power supply (commonly 24 VDC — verify your movement)

### GPIO mapping (default)
- **Relay A** → GPIO26
- **Relay B** → GPIO20
- **C** → common return (not switched)

Most Waveshare relay boards are **active-low**  
(GPIO LOW = relay ON).  
This is supported in software.

---

## Electrical notes (important)

- You are switching **inductive loads**
- Use:
  - Proper enclosure
  - Strain relief
  - Appropriately fused supply
- If you see EMI issues or contact wear:
  - Add inductive suppression (diode / TVS as appropriate)
- Raspberry Pi and clock coils should ideally use **separate power supplies**

This project is intended for experienced hobbyists or professionals familiar with relay-driven electromechanical systems.

---
# International 3-Wire Impulse Clock Controller (A/B/C) with Web UI

Raspberry Pi–based controller for vintage **International (pre-IBM) 3-wire impulse clocks**.

This project reproduces the original International master clock logic using modern hardware, driving **A / B / C** impulse movements via relays and providing a built-in **web interface** for monitoring and correction.

---

## Features

- Correct International **A / B / C** impulse behavior
- NTP-disciplined minute timing
- Automatic **:59 correction burst**
- Web UI for:
  - Live status display
  - Setting the dial reading
  - **FAST SET** (advance quickly if slow)
  - **STOP CORRECTION** (immediate cancel)
  - Manual pulse testing
- Persistent dial offset storage
- Designed for unattended, always-on operation

Web UI:
http://<raspberry-pi-ip>:8081

markdown
Copy code

---

## How the clock logic works

### Normal operation
- Every minute at `second == 0`
  - **A pulse** is sent
- On minutes `00–49`
  - **B pulse** is also sent
- At minute `59`
  - **17 additional A pulses**, spaced **2 seconds apart**
  - This occurs **once per hour**

The **C wire** is the common return and is **not switched** by the Raspberry Pi.

This behavior mirrors the original electromechanical International master clocks and mechanical correction cams.

---

## FAST SET behavior (Web UI)

FAST SET dynamically aligns the clock dial with system time:

- **If the dial is behind**
  - Advances using **A + B together** every 2 seconds
  - Remaining offset is recalculated each pulse so elapsed real time is accounted for
- **If the dial is ahead**
  - The clock cannot reverse
  - The controller **STALLS** (skips normal minute pulses) until real time catches up
- **STOP CORRECTION**
  - Immediately cancels FAST SET
  - De-energizes all relays

The controller tracks the dial using an offset:

dial_minutes = system_minutes + offset_minutes (mod 720)

csharp
Copy code

Offset state is stored in:
~/master-clock/international_state.json

yaml
Copy code

---

## Hardware

### Required
- Raspberry Pi (Pi 3 / 4 / 5 recommended)
- Relay board (Waveshare or similar)
- Clock power supply (commonly 24 VDC — verify your movement)

### GPIO mapping (default)
- **Relay A** → GPIO26
- **Relay B** → GPIO20
- **C** → common return (not switched)

Most Waveshare relay boards are **active-low**  
(GPIO LOW = relay ON), which is supported by the software.

---

## Electrical & safety notes

- This system switches **inductive loads**
- Use:
  - Proper enclosure
  - Strain relief
  - Appropriately fused supply
- For EMI or contact wear issues:
  - Add inductive suppression (diode / TVS as appropriate)
- Raspberry Pi and clock coils should ideally use **separate power supplies**

This project assumes familiarity with relay-driven electromechanical systems.

---

## Software setup

Install dependencies:
```bash
pip install -r requirements.txt
Run manually:

bash
Copy code
python international_clock_daemon_web.py
Then open:

cpp
Copy code
http://<raspberry-pi-ip>:8081
Running as a systemd service (optional)
This repository includes a generic systemd unit file for permanent installations.

Notes
The service assumes installation in:

bash
Copy code
/opt/international-impulse-clock
The service runs as a dedicated system user:

lua
Copy code
international-clock
You may edit the service file to suit your system.

Example installation
bash
Copy code
sudo useradd --system --no-create-home international-clock
sudo mkdir /opt/international-impulse-clock
sudo cp -r * /opt/international-impulse-clock
sudo chown -R international-clock:international-clock /opt/international-impulse-clock

sudo cp systemd/international-clock.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable international-clock.service
sudo systemctl start international-clock.service
View logs:

bash
Copy code
journalctl -u international-clock.service -f
GPIO permissions may require adding the service user to the gpio group or running as root, depending on OS configuration.

Repository layout
lua
Copy code
international-impulse-clock/
├── international_clock_daemon_web.py
├── README.md
├── LICENSE
├── requirements.txt
└── systemd/
    └── international-clock.service
License
MIT License — see LICENSE

Notes
This controller was designed specifically for International / IBM-era impulse movements using the A/B/C system and real mechanical correction cams.
Behavior intentionally mirrors the original electromechanical master clocks.

