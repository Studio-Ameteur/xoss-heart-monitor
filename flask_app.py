from flask import Flask, render_template, jsonify, request
import threading, time, csv, os, asyncio
from datetime import datetime

app = Flask(__name__)

LOG_INTERVAL = 1.0
log_data = []
logging_active = False
scan_results = []
scanning = False
sensors = {}
ble_loop = None
clients = {}

from bleak import BleakScanner, BleakClient

HEART_RATE_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

def get_ble_loop():
    global ble_loop
    if ble_loop is None or not ble_loop.is_running():
        ble_loop = asyncio.new_event_loop()
        t = threading.Thread(target=ble_loop.run_forever, daemon=True)
        t.start()
    return ble_loop

def run_ble(coro):
    return asyncio.run_coroutine_threadsafe(coro, get_ble_loop())

def make_sensor(sid, name, address):
    return {
        "id": sid,
        "name": name,
        "address": address,
        "hr": 0,
        "connected": False,
        "status": "disconnected"
    }

def hr_callback(sid):
    def callback(sender, data):
        flags = data[0]
        hr = int.from_bytes(data[1:3], "little") if flags & 0x01 else data[1]
        if sid in sensors:
            sensors[sid]["hr"] = hr
    return callback

async def connect_ble(sid, address):
    sensors[sid]["status"] = "connecting"
    try:
        client = BleakClient(address, timeout=10.0)
        await client.connect()
        clients[sid] = client
        sensors[sid]["connected"] = True
        sensors[sid]["status"] = "connected"
        await client.start_notify(HEART_RATE_UUID, hr_callback(sid))
        while sensors[sid]["connected"]:
            if not client.is_connected:
                break
            await asyncio.sleep(1)
        try:
            await client.stop_notify(HEART_RATE_UUID)
            await client.disconnect()
        except:
            pass
    except Exception as e:
        pass
    if sid in sensors:
        sensors[sid]["connected"] = False
        sensors[sid]["status"] = "disconnected"
        sensors[sid]["hr"] = 0
    if sid in clients:
        del clients[sid]

async def disconnect_ble(sid):
    sensors[sid]["connected"] = False
    client = clients.get(sid)
    if client:
        try:
            await client.stop_notify(HEART_RATE_UUID)
            await client.disconnect()
        except:
            pass
        del clients[sid]
    sensors[sid]["status"] = "disconnected"
    sensors[sid]["hr"] = 0

async def do_scan():
    global scan_results, scanning
    scanning = True
    scan_results = []
    try:
        devices = await BleakScanner.discover(timeout=6.0)
        xoss, other = [], []
        for d in devices:
            name = d.name or "Без имени"
            rssi = getattr(d, "rssi", None)
            entry = {"name": name, "address": d.address, "rssi": rssi}
            if any(k in name.upper() for k in ["XOSS", "HR", "HEART", "POLAR", "WAHOO", "GARMIN"]):
                xoss.append(entry)
            else:
                other.append(entry)
        scan_results = xoss + other
    except Exception as e:
        scan_results = []
    scanning = False

def write_txt_files():
    folder = os.path.dirname(os.path.abspath(__file__))
    for sid, s in sensors.items():
        if s["connected"]:
            path = os.path.join(folder, f"{s['name']}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(s["hr"]))

def logging_loop():
    global logging_active
    folder = os.path.dirname(os.path.abspath(__file__))
    fname = os.path.join(folder, f"журнал_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    ids = sorted(sensors.keys())
    with open(fname, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Дата", "Время"] + [sensors[i]["name"] for i in ids])
    while logging_active:
        ids = sorted(sensors.keys())
        now = datetime.now()
        row = [now.strftime("%d.%m.%Y"), now.strftime("%H:%M:%S")]
        row += [sensors[i]["hr"] if sensors[i]["connected"] else "" for i in ids]
        with open(fname, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(row)
        log_data.append(row)
        if len(log_data) > 100:
            log_data.pop(0)
        write_txt_files()
        time.sleep(LOG_INTERVAL)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/monitor")
def monitor():
    return render_template("monitor.html")

@app.route("/api/sensors")
def api_sensors():
    return jsonify(list(sensors.values()))

@app.route("/api/scan", methods=["POST"])
def api_scan():
    run_ble(do_scan())
    return jsonify({"status": "scanning"})

@app.route("/api/scan_results")
def api_scan_results():
    return jsonify({"results": scan_results, "scanning": scanning})

@app.route("/api/add_sensor", methods=["POST"])
def api_add_sensor():
    data = request.json
    sid = len(sensors)
    sensors[sid] = make_sensor(sid, data.get("name", f"Датчик {sid+1}"), data.get("address", ""))
    run_ble(connect_ble(sid, sensors[sid]["address"]))
    return jsonify({"status": "ok", "id": sid})

@app.route("/api/remove_sensor/<int:sid>", methods=["POST"])
def api_remove_sensor(sid):
    if sid in sensors:
        run_ble(disconnect_ble(sid))
        del sensors[sid]
    return jsonify({"status": "ok"})

@app.route("/api/connect_sensor/<int:sid>", methods=["POST"])
def api_connect_sensor(sid):
    if sid in sensors:
        run_ble(connect_ble(sid, sensors[sid]["address"]))
    return jsonify({"status": "ok"})

@app.route("/api/disconnect_sensor/<int:sid>", methods=["POST"])
def api_disconnect_sensor(sid):
    if sid in sensors:
        run_ble(disconnect_ble(sid))
    return jsonify({"status": "ok"})

@app.route("/api/start_log", methods=["POST"])
def api_start_log():
    global logging_active
    if not logging_active:
        logging_active = True
        threading.Thread(target=logging_loop, daemon=True).start()
    return jsonify({"status": "ok"})

@app.route("/api/stop_log", methods=["POST"])
def api_stop_log():
    global logging_active
    logging_active = False
    return jsonify({"status": "ok"})

@app.route("/api/log")
def api_log():
    return jsonify(log_data[-20:])

@app.route("/api/set_interval", methods=["POST"])
def api_set_interval():
    global LOG_INTERVAL
    LOG_INTERVAL = float(request.json.get("interval", 1.0))
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000, use_reloader=False)