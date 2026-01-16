#!/usr/bin/python3
"""
International 3-wire impulse clock controller + Web UI + FAST SET + STOP button.

Hardware model:
- C is common (not switched by the Pi).
- Relay A closes A->C when energized.
- Relay B closes B->C when energized.
- Clock cam selects A vs B internally near its :59; pulsing A+B ensures advancement.

Normal schedule:
- Each minute (second==0): A pulse always.
- Each minute 0..49: B pulse as well.
- At minute 59: 17 additional A pulses spaced 2 seconds apart (once per hour).

Web UI:
- View status
- Set dial reading (HH:MM)
- FAST SET (dynamic):
    * if dial behind system: advance with A+B pulses every 2 seconds until aligned
      (recomputes remaining each cycle so elapsed real time is accounted for)
    * if dial ahead: stall (skip normal pulses) until aligned
- STOP CORRECTION button cancels FAST SET immediately
- Pulse test buttons

GPIO:
- A relay: GPIO26
- B relay: GPIO20

Web UI:
- http://<pi-ip>:8081
"""

import datetime
import json
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass

import gpiozero

# ---- Web UI ----
WEB_BIND = "0.0.0.0"
WEB_PORT = 8081

try:
    from flask import Flask, request, redirect, url_for, render_template_string
    WEB_ENABLED = True
    FLASK_IMPORT_ERROR = ""
except Exception as e:
    WEB_ENABLED = False
    FLASK_IMPORT_ERROR = str(e)

# ---------------------------
# GPIO / TIMING CONFIG
# ---------------------------
A_GPIO = "GPIO26"
B_GPIO = "GPIO20"

RELAY_ACTIVE_HIGH = False  # Waveshare typically LOW=ON

# Normal minute pulse width (increase to 1.2/1.5 if it misses steps)
MINUTE_PULSE_WIDTH_SEC = 1.0

# :59 correction burst (A only)
CORRECTION_PULSES = 17
CORRECTION_INTERVAL_SEC = 2.0
CORRECTION_PULSE_WIDTH_SEC = 0.5  # must be < interval

# FAST SET behavior (A+B together)
FAST_SET_INTERVAL_SEC = 2.0            # pulse start-to-start spacing
FAST_SET_PULSE_WIDTH_SEC = 0.6         # relay ON time (keep < interval)
FAST_SET_MAX_PULSES = 400              # safety cap (you set this)
FAST_SET_MAX_SECONDS = 40 * 60         # safety cap on wall-clock time spent in fast set

STATE_FILE = os.path.expanduser("~/master-clock/international_state.json")
LOG_PREFIX = "InternationalClockWeb"

A_RELAY = gpiozero.OutputDevice(A_GPIO, active_high=RELAY_ACTIVE_HIGH, initial_value=False)
B_RELAY = gpiozero.OutputDevice(B_GPIO, active_high=RELAY_ACTIVE_HIGH, initial_value=False)


def log(msg: str) -> None:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{now}  {LOG_PREFIX}: {msg}", flush=True)


def all_relays_off() -> None:
    A_RELAY.off()
    B_RELAY.off()


def pulse(relay: gpiozero.OutputDevice, width_sec: float) -> None:
    relay.on()
    time.sleep(width_sec)
    relay.off()


def pulse_a(width_sec: float) -> None:
    pulse(A_RELAY, width_sec)


def pulse_b(width_sec: float) -> None:
    pulse(B_RELAY, width_sec)


def pulse_ab(width_sec: float) -> None:
    A_RELAY.on()
    B_RELAY.on()
    time.sleep(width_sec)
    A_RELAY.off()
    B_RELAY.off()


def to_12h_minutes(dt: datetime.datetime) -> int:
    return ((dt.hour * 60) + dt.minute) % 720


def parse_hhmm(s: str) -> tuple[int, int]:
    s = s.strip()
    if ":" not in s:
        raise ValueError("Time must be HH:MM")
    hh, mm = s.split(":", 1)
    h = int(hh)
    m = int(mm)
    if not (0 <= h <= 23):
        raise ValueError("HH must be 0..23")
    if not (0 <= m <= 59):
        raise ValueError("MM must be 0..59")
    return h, m


def minutes_to_hhmm_12h(m: int) -> str:
    m = m % 720
    hh = (m // 60) % 12
    mm = m % 60
    display_h = 12 if hh == 0 else hh
    return f"{display_h:02d}:{mm:02d}"


@dataclass
class State:
    # dial_minutes = system_minutes + offset (mod 720)
    offset_minutes: int = 0
    has_offset: bool = False

    # guards
    last_minute_tick: tuple | None = None        # (Y,M,D,H,MIN)
    last_correction_hour: tuple | None = None    # (Y,M,D,H)

    # stall minutes remaining when dial is fast
    stall_remaining_minutes: int = 0

    # FAST SET control
    fast_set_requested: bool = False
    fast_set_running: bool = False
    fast_set_cancel_requested: bool = False
    fast_set_status: str = "Idle"


STATE = State()
LOCK = threading.Lock()


def persist_state() -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with LOCK:
            data = {
                "offset_minutes": STATE.offset_minutes,
                "has_offset": STATE.has_offset,
                "saved_at": datetime.datetime.now().isoformat(),
            }
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log(f"WARNING: persist failed: {e}")


def restore_state() -> None:
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        with LOCK:
            STATE.offset_minutes = int(data.get("offset_minutes", 0)) % 720
            STATE.has_offset = bool(data.get("has_offset", False))
        log(f"State restored: has_offset={STATE.has_offset}, offset_minutes={STATE.offset_minutes}")
    except FileNotFoundError:
        log("State file not found (first run). Use Web UI to set dial.")
    except Exception as e:
        log(f"WARNING: restore failed: {e}")


def current_estimated_dial_minutes(now: datetime.datetime | None = None) -> int:
    if now is None:
        now = datetime.datetime.now()
    sys_m = to_12h_minutes(now)
    with LOCK:
        if not STATE.has_offset:
            return sys_m
        return (sys_m + STATE.offset_minutes) % 720


def set_dial_reading(hh: int, mm: int) -> None:
    now = datetime.datetime.now()
    sys_m = to_12h_minutes(now)
    dial_m = ((hh * 60) + mm) % 720
    offset = (dial_m - sys_m) % 720
    with LOCK:
        STATE.offset_minutes = offset
        STATE.has_offset = True
        STATE.stall_remaining_minutes = 0
    persist_state()
    log(f"Dial set: {hh:02d}:{mm:02d} -> offset={offset} (dial = system + offset)")


def compute_fast_or_stall_plan() -> tuple[int, int]:
    """
    Returns (stall_minutes, advance_minutes) needed to align dial to system.
    - stall_minutes: dial is ahead by that amount (can't reverse)
    - advance_minutes: dial is behind by that amount (we can advance)
    """
    now = datetime.datetime.now()
    sys_m = to_12h_minutes(now)
    dial_m = current_estimated_dial_minutes(now)
    delta_forward = (sys_m - dial_m) % 720  # minutes dial must move forward to match system

    if delta_forward == 0:
        return (0, 0)

    # If delta_forward > 360, dial is actually ahead by (720-delta_forward), so we stall.
    if delta_forward > 360:
        return (720 - delta_forward, 0)
    else:
        return (0, delta_forward)


def update_offset_after_stall_or_advance(stalled: int, advanced: int) -> None:
    if stalled == 0 and advanced == 0:
        return
    with LOCK:
        if not STATE.has_offset:
            return
        STATE.offset_minutes = (STATE.offset_minutes - stalled + advanced) % 720
    persist_state()


def run_correction_burst_once_per_hour(now: datetime.datetime) -> None:
    if now.minute != 59:
        return
    hour_key = (now.year, now.month, now.day, now.hour)
    with LOCK:
        if STATE.last_correction_hour == hour_key:
            return
        STATE.last_correction_hour = hour_key

    log(f"Correction burst: {CORRECTION_PULSES} A-pulses every {CORRECTION_INTERVAL_SEC:.1f}s "
        f"(width {CORRECTION_PULSE_WIDTH_SEC:.1f}s)")
    start = time.monotonic()
    for i in range(CORRECTION_PULSES):
        target_t = start + (i * CORRECTION_INTERVAL_SEC)
        while True:
            dt = target_t - time.monotonic()
            if dt <= 0:
                break
            time.sleep(min(0.05, dt))
        pulse_a(CORRECTION_PULSE_WIDTH_SEC)
        log(f"Correction pulse {i+1}/{CORRECTION_PULSES} (A)")


def sleep_until_next_second() -> None:
    now = datetime.datetime.now()
    time.sleep((1_000_000 - now.microsecond) / 1_000_000.0)


def on_signal(sig, _frame) -> None:
    log(f"Caught signal {sig}; shutting down.")
    all_relays_off()
    sys.exit(0)


signal.signal(signal.SIGINT, on_signal)
signal.signal(signal.SIGTERM, on_signal)


def maybe_handle_fast_set() -> None:
    """
    FAST SET (dynamic):
    - If dial behind: advance with A+B pulses every 2 seconds until aligned.
      Recomputes remaining each pulse so elapsed wall time is automatically accounted for.
    - If dial ahead: set stall_remaining_minutes and exit fast-set.
    - STOP button cancels fast-set quickly.
    """
    with LOCK:
        if not STATE.fast_set_requested or STATE.fast_set_running:
            return
        STATE.fast_set_requested = False
        STATE.fast_set_running = True
        STATE.fast_set_cancel_requested = False
        STATE.fast_set_status = "Starting…"

    try:
        stall, adv = compute_fast_or_stall_plan()

        if stall > 0:
            with LOCK:
                STATE.stall_remaining_minutes = stall
                STATE.fast_set_status = f"Dial is {stall} min fast: will STALL until aligned."
            log(f"FAST SET: dial is {stall} minutes fast -> stalling (no pulses).")
            return

        if adv == 0:
            with LOCK:
                STATE.fast_set_status = "Already aligned (no action)."
            log("FAST SET: already aligned.")
            return

        log(f"FAST SET: dynamic mode (A+B every {FAST_SET_INTERVAL_SEC}s, width {FAST_SET_PULSE_WIDTH_SEC}s)")
        with LOCK:
            STATE.fast_set_status = "FAST SET running (dynamic)…"

        start = time.monotonic()
        pulses_sent = 0

        while True:
            # safety timeout
            if (time.monotonic() - start) > FAST_SET_MAX_SECONDS:
                raise RuntimeError("FAST SET timeout reached")

            # allow STOP button to cancel quickly
            with LOCK:
                if STATE.fast_set_cancel_requested:
                    STATE.fast_set_status = "FAST SET cancelled by user."
                    log("FAST SET cancelled by user.")
                    all_relays_off()
                    return

            stall, adv = compute_fast_or_stall_plan()

            # If dial is fast, we cannot reverse: switch to stall mode and exit fast-set
            if stall > 0:
                with LOCK:
                    STATE.stall_remaining_minutes = stall
                    STATE.fast_set_status = f"Dial is {stall} min fast: will STALL until aligned."
                log(f"FAST SET: dial is {stall} min fast -> stalling (no pulses).")
                return

            # Aligned
            if adv == 0:
                with LOCK:
                    STATE.fast_set_status = f"FAST SET complete (pulses sent: {pulses_sent})."
                log(f"FAST SET complete. Pulses sent={pulses_sent}")
                return

            # pulse cap
            if pulses_sent >= FAST_SET_MAX_PULSES:
                raise RuntimeError(f"FAST SET pulse cap reached ({FAST_SET_MAX_PULSES})")

            # One minute step forward
            pulse_ab(FAST_SET_PULSE_WIDTH_SEC)
            update_offset_after_stall_or_advance(stalled=0, advanced=1)
            pulses_sent += 1

            # spacing
            remaining = FAST_SET_INTERVAL_SEC - FAST_SET_PULSE_WIDTH_SEC
            if remaining > 0:
                time.sleep(remaining)

            if pulses_sent % 10 == 0:
                with LOCK:
                    STATE.fast_set_status = f"Advancing… pulses={pulses_sent}, remaining≈{adv}"
                log(f"FAST SET progress: pulses={pulses_sent}, remaining≈{adv}")

    except Exception as e:
        with LOCK:
            STATE.fast_set_status = f"FAST SET error: {e}"
        log(f"FAST SET error: {e}")
    finally:
        with LOCK:
            STATE.fast_set_running = False
            STATE.fast_set_cancel_requested = False
        all_relays_off()


def minute_tick_actions(now: datetime.datetime) -> None:
    """
    Runs once per minute at second==0 for normal cadence.
    If fast-set is running, skip normal tick to avoid double-driving.
    """
    minute_key = (now.year, now.month, now.day, now.hour, now.minute)
    with LOCK:
        if STATE.last_minute_tick == minute_key:
            return
        STATE.last_minute_tick = minute_key

        if STATE.fast_set_running:
            log(f"Minute tick {now.strftime('%H:%M')}: FAST SET running -> skipping normal tick.")
            return

        if STATE.stall_remaining_minutes > 0:
            STATE.stall_remaining_minutes -= 1
            update_offset_after_stall_or_advance(stalled=1, advanced=0)
            log(f"Minute tick {now.strftime('%H:%M')}: STALL (clock fast). No A/B pulses.")
            # Still run :59 burst if desired
            if now.minute == 59:
                time.sleep(0.2)
                run_correction_burst_once_per_hour(now)
            return

    # Normal minute pulses
    log(f"Minute tick {now.strftime('%H:%M')}: A pulse")
    pulse_a(MINUTE_PULSE_WIDTH_SEC)

    if 0 <= now.minute <= 49:
        log(" -> B pulse")
        pulse_b(MINUTE_PULSE_WIDTH_SEC)

    if now.minute == 59:
        time.sleep(0.2)
        run_correction_burst_once_per_hour(now)


def daemon_loop() -> None:
    log("Starting International clock daemon + Web UI + FAST SET.")
    log(f"GPIO: A={A_GPIO}, B={B_GPIO} | minute_width={MINUTE_PULSE_WIDTH_SEC}s")
    log(f"FAST SET: interval={FAST_SET_INTERVAL_SEC}s width={FAST_SET_PULSE_WIDTH_SEC}s "
        f"max_pulses={FAST_SET_MAX_PULSES} max_seconds={FAST_SET_MAX_SECONDS}s")

    if WEB_ENABLED:
        log(f"Web UI: http://<pi-ip>:{WEB_PORT}  (bind {WEB_BIND})")
    else:
        log(f"Web UI disabled: Flask import failed: {FLASK_IMPORT_ERROR}")

    restore_state()
    all_relays_off()

    while True:
        # Handle fast-set requests promptly (not just at minute boundaries)
        maybe_handle_fast_set()

        sleep_until_next_second()
        now = datetime.datetime.now()
        if now.second == 0:
            minute_tick_actions(now)


# ---------------------------
# Web UI
# ---------------------------
HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>International Clock</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; max-width: 900px; }
    .card { border: 1px solid #ccc; border-radius: 12px; padding: 16px; margin: 14px 0; }
    label { display: inline-block; width: 280px; }
    input { padding: 6px; font-size: 14px; }
    button { padding: 10px 14px; font-size: 14px; cursor: pointer; margin-right: 8px; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .warn { color: #7a4; }
    .stopbtn { background:#c00; color:white; border:none; }
  </style>
</head>
<body>
  <h2>International Clock Controller</h2>

  <div class="card">
    <h3>Status</h3>
    <p>System time: <span class="mono">{{ sys_time }}</span></p>
    <p>Estimated dial: <span class="mono">{{ dial_time }}</span></p>
    <p>Offset minutes (dial = system + offset): <span class="mono">{{ offset }}</span> (has_offset={{ has_offset }})</p>
    <p>Stall remaining: <span class="mono">{{ stall }}</span> min</p>
    <p>Fast set running: <span class="mono">{{ fs_running }}</span></p>
    <p>Fast set status: <span class="mono">{{ fs_status }}</span></p>
  </div>

  <div class="card">
    <h3>1) Enter what the dial currently reads</h3>
    <form method="POST" action="{{ url_for('set_dial') }}">
      <label>Dial time (HH:MM):</label>
      <input name="hhmm" placeholder="12:34" required>
      <button type="submit">Set Dial</button>
    </form>
    <p class="warn">
      Tip: After setting the dial reading, click <b>FAST SET</b> to advance quickly if slow.
      If the dial is fast, the controller will stall minute ticks until real time catches up.
    </p>
  </div>

  <div class="card">
    <h3>2) FAST SET</h3>
    <form method="POST" action="{{ url_for('fast_set') }}" style="display:inline;">
      <button type="submit">FAST SET (A+B every 2s)</button>
    </form>
    <form method="POST" action="{{ url_for('stop_correction') }}" style="display:inline;">
      <button class="stopbtn" type="submit">STOP CORRECTION</button>
    </form>
  </div>

  <div class="card">
    <h3>Pulse Test</h3>
    <form method="POST" action="{{ url_for('pulse_test') }}">
      <label>Pulse width (sec):</label>
      <input name="w" value="1.0">
      <button name="which" value="A" type="submit">Pulse A</button>
      <button name="which" value="B" type="submit">Pulse B</button>
      <button name="which" value="AB" type="submit">Pulse A+B</button>
      <br><br>
      <button name="which" value="CORR" type="submit">Run :59 A Burst (17)</button>
    </form>
  </div>

</body>
</html>
"""


def web_thread() -> None:
    app = Flask(__name__)

    @app.get("/")
    def index():
        now = datetime.datetime.now()
        dial_m = current_estimated_dial_minutes(now)
        with LOCK:
            offset = STATE.offset_minutes if STATE.has_offset else 0
            has_offset = STATE.has_offset
            stall = STATE.stall_remaining_minutes
            fs_running = STATE.fast_set_running
            fs_status = STATE.fast_set_status
        return render_template_string(
            HTML,
            sys_time=now.strftime("%Y-%m-%d %H:%M:%S"),
            dial_time=minutes_to_hhmm_12h(dial_m),
            offset=offset,
            has_offset=has_offset,
            stall=stall,
            fs_running=fs_running,
            fs_status=fs_status,
        )

    @app.post("/set")
    def set_dial():
        hhmm = request.form.get("hhmm", "").strip()
        h, m = parse_hhmm(hhmm)
        set_dial_reading(h, m)
        return redirect(url_for("index"))

    @app.post("/fastset")
    def fast_set():
        with LOCK:
            STATE.fast_set_requested = True
            STATE.fast_set_cancel_requested = False
            STATE.fast_set_status = "FAST SET requested…"
        log("Web: FAST SET requested.")
        return redirect(url_for("index"))

    @app.post("/stop")
    def stop_correction():
        with LOCK:
            STATE.fast_set_cancel_requested = True
            STATE.fast_set_requested = False
            STATE.fast_set_status = "STOP requested…"
        log("Web: STOP CORRECTION requested.")
        all_relays_off()
        return redirect(url_for("index"))

    @app.post("/pulse")
    def pulse_test():
        which = request.form.get("which", "")
        try:
            w = float(request.form.get("w", "1.0"))
        except Exception:
            w = 1.0
        w = max(0.05, min(5.0, w))

        if which == "A":
            log(f"Web test: pulse A for {w}s")
            pulse_a(w)
        elif which == "B":
            log(f"Web test: pulse B for {w}s")
            pulse_b(w)
        elif which == "AB":
            log(f"Web test: pulse A+B for {w}s")
            pulse_ab(w)
        elif which == "CORR":
            log("Web test: run :59 burst (17 A pulses)")
            run_correction_burst_once_per_hour(datetime.datetime.now().replace(minute=59))
        return redirect(url_for("index"))

    app.add_url_rule("/set", endpoint="set_dial", view_func=set_dial, methods=["POST"])
    app.add_url_rule("/fastset", endpoint="fast_set", view_func=fast_set, methods=["POST"])
    app.add_url_rule("/stop", endpoint="stop_correction", view_func=stop_correction, methods=["POST"])
    app.add_url_rule("/pulse", endpoint="pulse_test", view_func=pulse_test, methods=["POST"])

    log(f"Web thread starting on {WEB_BIND}:{WEB_PORT}")
    app.run(host=WEB_BIND, port=WEB_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    if WEB_ENABLED:
        threading.Thread(target=web_thread, daemon=True).start()
    else:
        log(f"Web UI disabled: Flask import failed: {FLASK_IMPORT_ERROR}")

    daemon_loop()


























(END)