import sys
import threading
import asyncio
import time
import csv
import os
import json
import colorsys
import traceback
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QDialog,
    QListWidget, QListWidgetItem, QSpinBox, QFrame, QScrollArea,
    QGridLayout, QHeaderView, QInputDialog, QMessageBox, QLineEdit
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QPainter, QPen, QColor, QFont, QIcon

try:
    from bleak import BleakScanner, BleakClient
    BLE_AVAILABLE = True
except ImportError:
    BLE_AVAILABLE = False

HEART_RATE_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
RECONNECT_DELAY_SEC = 5
BATTERY_POLL_TICKS = 30

def crash_log_path():
    folder = os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.join(folder, "crash_log.txt")

def log_crash(source, exc_type, exc_value, exc_tb):
    try:
        with open(crash_log_path(), "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{source}] ===\n")
            f.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    except Exception:
        pass

def install_crash_logging():
    def qt_excepthook(exc_type, exc_value, exc_tb):
        log_crash("Qt main thread", exc_type, exc_value, exc_tb)
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = qt_excepthook

    def thread_excepthook(args):
        log_crash(f"thread:{args.thread.name}", args.exc_type, args.exc_value, args.exc_traceback)
    threading.excepthook = thread_excepthook

    def asyncio_exception_handler(loop, context):
        exc = context.get("exception")
        if exc is not None:
            log_crash("asyncio", type(exc), exc, exc.__traceback__)
        else:
            try:
                with open(crash_log_path(), "a", encoding="utf-8") as f:
                    f.write(f"\n=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [asyncio] ===\n")
                    f.write(str(context) + "\n")
            except Exception:
                pass
    return asyncio_exception_handler

asyncio_exception_handler = install_crash_logging()

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

WIN2K = """
    QWidget {
        background: #d4d0c8;
        font-family: Tahoma, Arial;
        font-size: 11px;
        color: #000;
    }
    QPushButton {
        background: #d4d0c8;
        border-top: 2px solid #fff;
        border-left: 2px solid #fff;
        border-right: 2px solid #404040;
        border-bottom: 2px solid #404040;
        padding: 3px 10px;
        min-width: 70px;
    }
    QPushButton:pressed {
        border-top: 2px solid #404040;
        border-left: 2px solid #404040;
        border-right: 2px solid #fff;
        border-bottom: 2px solid #fff;
    }
    QPushButton:disabled { color: #808080; }
    QSpinBox {
        background: #fff;
        border-top: 2px solid #808080;
        border-left: 2px solid #808080;
        border-right: 2px solid #fff;
        border-bottom: 2px solid #fff;
        padding: 1px 4px;
        min-width: 50px;
    }
    QLineEdit {
        background: #fff;
        border-top: 2px solid #808080;
        border-left: 2px solid #808080;
        border-right: 2px solid #fff;
        border-bottom: 2px solid #fff;
        padding: 2px 4px;
    }
    QTableWidget {
        background: #fff;
        gridline-color: #d4d0c8;
        border-top: 2px solid #808080;
        border-left: 2px solid #808080;
        border-right: 2px solid #fff;
        border-bottom: 2px solid #fff;
    }
    QTableWidget::item:selected { background: #000080; color: #fff; }
    QHeaderView::section {
        background: #000080;
        color: #fff;
        padding: 3px 6px;
        border: 1px solid #404040;
        font-weight: bold;
    }
    QListWidget {
        background: #fff;
        border-top: 2px solid #808080;
        border-left: 2px solid #808080;
        border-right: 2px solid #fff;
        border-bottom: 2px solid #fff;
    }
    QListWidget::item:selected { background: #000080; color: #fff; }
    QDialog { background: #d4d0c8; }
    QLabel { background: transparent; }
    QScrollBar:vertical {
        background: #d4d0c8;
        width: 16px;
        border: 1px solid #808080;
    }
    QScrollBar::handle:vertical {
        background: #d4d0c8;
        border-top: 2px solid #fff;
        border-left: 2px solid #fff;
        border-right: 2px solid #404040;
        border-bottom: 2px solid #404040;
        min-height: 20px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        background: #d4d0c8;
        height: 16px;
        border-top: 2px solid #fff;
        border-left: 2px solid #fff;
        border-right: 2px solid #404040;
        border-bottom: 2px solid #404040;
    }
"""

sensors = {}
ble_loop = None
clients = {}

def config_path():
    folder = os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.join(folder, "sensors_config.json")

def save_sensors_config():
    try:
        data = [
            {"name": s["name"], "address": s["address"], "color": s["color"]}
            for s in sensors.values()
        ]
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def load_sensors_config():
    path = config_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def next_sensor_id():
    return max(sensors.keys(), default=-1) + 1

def color_for_index(index):
    hue = (index * 0.6180339887498949) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.75, 1.0)
    return QColor(int(r * 255), int(g * 255), int(b * 255)).name()

def get_ble_loop():
    global ble_loop
    if ble_loop is None or not ble_loop.is_running():
        ble_loop = asyncio.new_event_loop()
        ble_loop.set_exception_handler(asyncio_exception_handler)
        t = threading.Thread(target=ble_loop.run_forever, daemon=True)
        t.start()
    return ble_loop

def run_ble(coro):
    return asyncio.run_coroutine_threadsafe(coro, get_ble_loop())

ble_op_lock = asyncio.Lock()

def parse_hr_measurement(data: bytes) -> int:
    """
    Parse Heart Rate Measurement characteristic (0x2A37).
    Supports all standard BLE HR sensor formats:
      - 8-bit or 16-bit HR value
      - with or without Energy Expended
      - with or without RR intervals
    Returns heart rate as integer, or 0 on error.
    """
    if not data or len(data) < 2:
        return 0
    flags = data[0]
    hr_format_16bit = flags & 0x01  # bit 0: 0=uint8, 1=uint16
    try:
        if hr_format_16bit:
            if len(data) < 3:
                return 0
            hr = int.from_bytes(data[1:3], "little")
        else:
            hr = data[1]
    except Exception:
        return 0
    # Sanity check: valid HR range 20-250 bpm
    if hr < 20 or hr > 250:
        return 0
    return hr

def hr_callback(sid, signal):
    def callback(sender, data):
        hr = parse_hr_measurement(bytes(data))
        if hr > 0 and sid in sensors:
            sensors[sid]["hr"] = hr
            signal.emit(sid, hr)
    return callback

def battery_callback(sid, signal):
    def callback(sender, data):
        if data and sid in sensors:
            level = data[0]
            if 0 <= level <= 100:
                sensors[sid]["battery"] = level
                signal.emit(sid, level)
    return callback

async def try_read_battery(sid, client, signal):
    try:
        data = await client.read_gatt_char(BATTERY_LEVEL_UUID)
        if data and sid in sensors:
            level = data[0]
            if 0 <= level <= 100:
                sensors[sid]["battery"] = level
                signal.emit(sid, level)
    except Exception:
        pass

async def connect_ble(sid, address, signal, battery_signal, disconnect_signal):
    while True:
        if sid not in sensors:
            return
        if not sensors[sid].get("auto_reconnect", True):
            return
        sensors[sid]["status"] = "connecting"
        signal.emit(sid, 0)
        try:
            client = BleakClient(address, timeout=10.0)
            async with ble_op_lock:
                await client.connect()
            clients[sid] = client
            sensors[sid]["connected"] = True
            sensors[sid]["status"] = "connected"
            sensors[sid]["reconnect_attempts"] = 0
            signal.emit(sid, sensors[sid]["hr"])
            await client.start_notify(HEART_RATE_UUID, hr_callback(sid, signal))
            await try_read_battery(sid, client, battery_signal)
            try:
                await client.start_notify(BATTERY_LEVEL_UUID, battery_callback(sid, battery_signal))
            except Exception:
                pass
            tick = 0
            while sensors.get(sid, {}).get("connected"):
                if not client.is_connected:
                    break
                await asyncio.sleep(1)
                tick += 1
                if tick % BATTERY_POLL_TICKS == 0:
                    await try_read_battery(sid, client, battery_signal)
            try:
                await client.stop_notify(HEART_RATE_UUID)
            except Exception:
                pass
            try:
                await client.stop_notify(BATTERY_LEVEL_UUID)
            except Exception:
                pass
            try:
                async with ble_op_lock:
                    await client.disconnect()
            except Exception:
                pass
        except Exception:
            pass

        if sid in clients:
            del clients[sid]
        if sid not in sensors:
            return

        user_initiated = not sensors[sid].get("auto_reconnect", True)
        sensors[sid]["connected"] = False
        sensors[sid]["hr"] = 0
        sensors[sid]["battery"] = None

        if user_initiated:
            sensors[sid]["status"] = "disconnected"
            signal.emit(sid, 0)
            return

        was_reconnecting = sensors[sid].get("reconnect_attempts", 0) > 0
        sensors[sid]["status"] = "reconnecting"
        sensors[sid]["reconnect_attempts"] = sensors[sid].get("reconnect_attempts", 0) + 1
        signal.emit(sid, 0)
        if not was_reconnecting:
            disconnect_signal.emit(sid, sensors[sid]["name"])
        await asyncio.sleep(RECONNECT_DELAY_SEC)

async def disconnect_ble(sid):
    if sid in sensors:
        sensors[sid]["auto_reconnect"] = False
        sensors[sid]["connected"] = False
        sensors[sid]["status"] = "disconnected"
        sensors[sid]["hr"] = 0
        sensors[sid]["battery"] = None
    client = clients.get(sid)
    if client:
        try:
            await client.stop_notify(HEART_RATE_UUID)
        except Exception:
            pass
        try:
            await client.stop_notify(BATTERY_LEVEL_UUID)
        except Exception:
            pass
        try:
            async with ble_op_lock:
                await client.disconnect()
        except Exception:
            pass
        if sid in clients:
            del clients[sid]

class Signals(QObject):
    hr_updated = pyqtSignal(int, int)
    scan_done = pyqtSignal(list)
    sensor_renamed = pyqtSignal(int, str)
    battery_updated = pyqtSignal(int, int)
    sensor_disconnected = pyqtSignal(int, str)

signals = Signals()

class EcgWidget(QWidget):
    HISTORY = 150

    def __init__(self, sid, parent=None):
        super().__init__(parent)
        self.sid = sid
        self.history = [0] * self.HISTORY
        self.setMinimumHeight(80)

    def push(self, hr):
        self.history.append(hr)
        if len(self.history) > self.HISTORY:
            self.history.pop(0)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        W, H = self.width(), self.height()

        painter.setPen(QPen(QColor(0, 26, 0), 1))
        for i in range(1, 10):
            painter.drawLine(int(W * i / 10), 0, int(W * i / 10), H)
        for i in range(1, 5):
            painter.drawLine(0, int(H * i / 5), W, int(H * i / 5))

        s = sensors.get(self.sid, {})
        if not s.get("connected"):
            painter.setPen(QPen(QColor(0, 51, 0), 1))
            painter.drawLine(0, H // 2, W, H // 2)
            painter.end()
            return

        vals = [v for v in self.history if v > 0]
        if not vals:
            painter.end()
            return
        mn = min(vals) - 10
        mx = max(vals) + 10
        rng = max(mx - mn, 20)
        step = W / (self.HISTORY - 1)

        base = QColor(s.get("color", "#00ff00"))
        layers = [
            (QColor(base.red(), base.green(), base.blue(), 20), 6),
            (QColor(base.red(), base.green(), base.blue(), 60), 3),
            (QColor(base.red(), base.green(), base.blue(), 255), 1),
        ]
        for color, width in layers:
            pen = QPen(color, width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            pts = []
            for i, v in enumerate(self.history):
                hr = v if v > 0 else (mn + rng / 2)
                x = int(i * step)
                y = int(H - ((hr - mn) / rng) * H * 0.8 - H * 0.1)
                pts.append((x, y))
            for i in range(1, len(pts)):
                painter.drawLine(pts[i-1][0], pts[i-1][1], pts[i][0], pts[i][1])
        painter.end()

class SensorCard(QFrame):
    def __init__(self, sid, parent=None):
        super().__init__(parent)
        self.sid = sid
        self._set_style(False)
        layout = QVBoxLayout(self)
        layout.setSpacing(2)
        layout.setContentsMargins(6, 6, 6, 6)

        s = sensors[sid]
        self.color = s.get("color", "#000080")

        self.color_bar = QFrame()
        self.color_bar.setFixedHeight(4)
        self.color_bar.setStyleSheet(f"background: {self.color}; border: none;")

        self.name_label = QLabel(s["name"])
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setFont(QFont("Tahoma", 9, QFont.Weight.Bold))
        self.name_label.setStyleSheet(f"color: {self.color};")

        self.hr_label = QLabel("---")
        self.hr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hr_label.setFont(QFont("Courier New", 28, QFont.Weight.Bold))
        self.hr_label.setStyleSheet("color: #808080;")

        self.unit_label = QLabel("")
        self.unit_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.unit_label.setStyleSheet("color: #404040; font-size: 10px;")

        self.battery_label = QLabel("")
        self.battery_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.battery_label.setStyleSheet("color: #404040; font-size: 10px;")

        self.status_label = QLabel("Отключен")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #808080; font-size: 10px;")

        btn_row = QHBoxLayout()
        self.conn_btn = QPushButton("Подкл")
        self.conn_btn.setMaximumWidth(55)
        self.conn_btn.clicked.connect(self.toggle_connection)
        self.remove_btn = QPushButton("Удалить")
        self.remove_btn.setMaximumWidth(55)
        btn_row.addWidget(self.conn_btn)
        btn_row.addWidget(self.remove_btn)

        layout.addWidget(self.color_bar)
        layout.addWidget(self.name_label)
        layout.addWidget(self.hr_label)
        layout.addWidget(self.unit_label)
        layout.addWidget(self.battery_label)
        layout.addWidget(self.status_label)
        layout.addLayout(btn_row)

    def _set_style(self, connected):
        bg = "#f0f4f0" if connected else "#d4d0c8"
        self.setStyleSheet(f"""
            QFrame {{
                background: {bg};
                border-top: 2px solid #fff;
                border-left: 2px solid #fff;
                border-right: 2px solid #404040;
                border-bottom: 2px solid #404040;
            }}
        """)

    def toggle_connection(self):
        s = sensors.get(self.sid, {})
        if s.get("connected") or s.get("status") in ("connecting", "reconnecting"):
            run_ble(disconnect_ble(self.sid))
        else:
            s["auto_reconnect"] = True
            run_ble(connect_ble(self.sid, s["address"], signals.hr_updated, signals.battery_updated, signals.sensor_disconnected))

    def update_name(self, name):
        self.name_label.setText(name)

    def update_display(self):
        s = sensors.get(self.sid, {})
        if not s:
            return
        self.name_label.setText(s["name"])
        battery = s.get("battery")
        battery_text = f"Заряд: {battery}%" if battery is not None else ""
        if s.get("connected"):
            self.hr_label.setText(str(s["hr"]))
            self.hr_label.setStyleSheet("color: #800000;")
            self.unit_label.setText("уд/мин")
            self.battery_label.setText(battery_text)
            self.status_label.setText("Подключен")
            self.status_label.setStyleSheet("color: #006400; font-size: 10px;")
            self.conn_btn.setText("Откл")
            self._set_style(True)
        elif s.get("status") == "connecting":
            self.hr_label.setText("---")
            self.hr_label.setStyleSheet("color: #808000;")
            self.battery_label.setText("")
            self.status_label.setText("Подключение...")
            self.status_label.setStyleSheet("color: #808000; font-size: 10px;")
            self.conn_btn.setText("Откл")
            self._set_style(False)
        elif s.get("status") == "reconnecting":
            self.hr_label.setText("---")
            self.hr_label.setStyleSheet("color: #808000;")
            self.battery_label.setText("")
            self.status_label.setText("Переподключение...")
            self.status_label.setStyleSheet("color: #cc6600; font-size: 10px;")
            self.conn_btn.setText("Откл")
            self._set_style(False)
        else:
            self.hr_label.setText("---")
            self.hr_label.setStyleSheet("color: #808080;")
            self.unit_label.setText("")
            self.battery_label.setText("")
            self.status_label.setText("Отключен")
            self.status_label.setStyleSheet("color: #808080; font-size: 10px;")
            self.conn_btn.setText("Подкл")
            self._set_style(False)

class AddSensorDialog(QDialog):
    def __init__(self, device_info, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить датчик")
        self.setFixedSize(320, 180)
        self.setStyleSheet(WIN2K)
        self.device_info = device_info
        self.result_name = None

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        info_lbl = QLabel(f"Устройство: {device_info['name']}\nАдрес: {device_info['address']}")
        info_lbl.setStyleSheet("color: #000; font-size: 11px;")
        layout.addWidget(info_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("border-top: 1px solid #808080; border-bottom: 1px solid #fff;")
        layout.addWidget(sep)

        name_lbl = QLabel("Введите имя датчика (например: ips1, dge2):")
        layout.addWidget(name_lbl)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Имя датчика...")
        self.name_input.setText(device_info.get("name", ""))
        layout.addWidget(self.name_input)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Добавить")
        ok_btn.clicked.connect(self.on_ok)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def on_ok(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Ошибка", "Введите имя датчика!")
            return
        self.result_name = name
        self.accept()

class RenameDialog(QDialog):
    def __init__(self, sid, parent=None):
        super().__init__(parent)
        self.sid = sid
        self.setWindowTitle("Переименовать датчик")
        self.setFixedSize(300, 140)
        self.setStyleSheet(WIN2K)
        self.result_name = None

        s = sensors[sid]
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        info_lbl = QLabel(f"Датчик: {s['name']}\nАдрес: {s['address']}")
        layout.addWidget(info_lbl)

        self.name_input = QLineEdit()
        self.name_input.setText(s["name"])
        layout.addWidget(self.name_input)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Переименовать")
        ok_btn.clicked.connect(self.on_ok)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def on_ok(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Ошибка", "Введите имя!")
            return
        self.result_name = name
        self.accept()

class DeviceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Управление датчиками")
        self.setMinimumSize(480, 420)
        self.setStyleSheet(WIN2K)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(10, 10, 10, 10)

        scan_lbl = QLabel("Поиск устройств Bluetooth:")
        scan_lbl.setFont(QFont("Tahoma", 9, QFont.Weight.Bold))
        layout.addWidget(scan_lbl)

        scan_row = QHBoxLayout()
        self.scan_btn = QPushButton("Начать поиск")
        self.scan_btn.clicked.connect(self.start_scan)
        self.scan_status = QLabel("Нажмите 'Начать поиск'")
        self.scan_status.setStyleSheet("color: #808080;")
        scan_row.addWidget(self.scan_btn)
        scan_row.addWidget(self.scan_status)
        scan_row.addStretch()
        layout.addLayout(scan_row)

        self.scan_list = QListWidget()
        self.scan_list.setMinimumHeight(130)
        layout.addWidget(self.scan_list)

        add_btn = QPushButton("Добавить выбранное устройство")
        add_btn.clicked.connect(self.add_selected)
        layout.addWidget(add_btn)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("border-top: 1px solid #808080; border-bottom: 1px solid #fff;")
        layout.addWidget(sep)

        added_lbl = QLabel("Добавленные датчики:")
        added_lbl.setFont(QFont("Tahoma", 9, QFont.Weight.Bold))
        layout.addWidget(added_lbl)

        self.added_list = QListWidget()
        self.added_list.setMinimumHeight(100)
        layout.addWidget(self.added_list)

        btn_row = QHBoxLayout()
        rename_btn = QPushButton("Переименовать")
        rename_btn.clicked.connect(self.rename_selected)
        remove_btn = QPushButton("Удалить")
        remove_btn.clicked.connect(self.remove_selected)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(rename_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        signals.scan_done.connect(self.on_scan_done)
        self.refresh_added()

    def start_scan(self):
        self.scan_btn.setEnabled(False)
        self.scan_status.setText("Поиск устройств...")
        self.scan_list.clear()
        if BLE_AVAILABLE:
            run_ble(self.do_scan())
        else:
            signals.scan_done.emit([
                {"name": "XOSS Pro 1", "address": "AA:BB:CC:DD:EE:01", "rssi": -55},
                {"name": "XOSS Pro 2", "address": "AA:BB:CC:DD:EE:02", "rssi": -63},
                {"name": "XOSS Pro 3", "address": "AA:BB:CC:DD:EE:03", "rssi": -71},
            ])

    # FIX: do_scan is now correctly indented as a method of DeviceDialog
    async def do_scan(self):
        results = []
        try:
            devices = await BleakScanner.discover(timeout=6.0)
            hr_keywords = [
                "XOSS", "HR", "HEART", "POLAR", "WAHOO", "GARMIN",
                "SUUNTO", "COROS", "MAGENE", "SCOSCHE", "WHOOP",
                "FITBIT", "XIAOMI", "HUAWEI", "SAMSUNG", "AMAZFIT",
                "BRYTON", "PULSE", "CARDIO", "COOSPO"
            ]
            priority, other = [], []
            for d in devices:
                name = d.name or "Без имени"
                rssi = getattr(d, "rssi", None)
                entry = {"name": name, "address": d.address, "rssi": rssi}
                if any(k in name.upper() for k in hr_keywords):
                    priority.append(entry)
                else:
                    other.append(entry)
            results = priority + other
        except Exception:
            pass
        signals.scan_done.emit(results)

    def on_scan_done(self, results):
        self.scan_list.clear()
        self.scan_btn.setEnabled(True)
        if not results:
            self.scan_status.setText("Устройства не найдены")
            return
        self.scan_status.setText(f"Найдено устройств: {len(results)}")
        for d in results:
            rssi = d.get("rssi")
            if rssi is not None:
                if rssi >= -55: sig = "Отличный"
                elif rssi >= -65: sig = "Хороший"
                elif rssi >= -75: sig = "Слабый"
                else: sig = "Очень слабый"
                label = f"{d['name']}  |  {d['address']}  |  Сигнал: {sig} ({rssi} дБм)"
            else:
                label = f"{d['name']}  |  {d['address']}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, d)
            self.scan_list.addItem(item)

    def add_selected(self):
        item = self.scan_list.currentItem()
        if not item:
            QMessageBox.information(self, "Выбор", "Выберите устройство из списка!")
            return
        d = item.data(Qt.ItemDataRole.UserRole)
        dlg = AddSensorDialog(d, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            sid = next_sensor_id()
            sensors[sid] = {
                "id": sid,
                "name": dlg.result_name,
                "address": d["address"],
                "hr": 0,
                "battery": None,
                "connected": False,
                "status": "disconnected",
                "auto_reconnect": True,
                "color": color_for_index(sid),
            }
            save_sensors_config()
            run_ble(connect_ble(sid, d["address"], signals.hr_updated, signals.battery_updated, signals.sensor_disconnected))
            self.refresh_added()

    def rename_selected(self):
        item = self.added_list.currentItem()
        if not item:
            QMessageBox.information(self, "Выбор", "Выберите датчик из списка!")
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        if sid not in sensors:
            return
        dlg = RenameDialog(sid, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            old_name = sensors[sid]["name"]
            new_name = dlg.result_name
            sensors[sid]["name"] = new_name
            self.rename_txt_file(old_name, new_name)
            save_sensors_config()
            signals.sensor_renamed.emit(sid, new_name)
            self.refresh_added()

    def rename_txt_file(self, old_name, new_name):
        folder = os.path.dirname(os.path.abspath(sys.argv[0]))
        old_path = os.path.join(folder, f"{old_name}.txt")
        new_path = os.path.join(folder, f"{new_name}.txt")
        if os.path.exists(old_path):
            try:
                os.rename(old_path, new_path)
            except Exception:
                pass

    def remove_selected(self):
        item = self.added_list.currentItem()
        if not item:
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        if sid in sensors:
            sensors[sid]["auto_reconnect"] = False
            run_ble(disconnect_ble(sid))
            del sensors[sid]
            save_sensors_config()
        self.refresh_added()
        signals.sensor_renamed.emit(-1, "")

    def refresh_added(self):
        self.added_list.clear()
        for sid, s in sensors.items():
            label = f"{s['name']}  |  {s['address']}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, sid)
            item.setForeground(QColor(s.get("color", "#000080")))
            self.added_list.addItem(item)

class MonitorWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Монитор пульса — XOSS Heart Monitor")
        icon_path = resource_path("icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setStyleSheet("background: #000;")
        self.setMinimumSize(800, 500)
        self.ecg_widgets = {}
        self.hr_labels = {}
        self.status_labels = {}
        self.name_labels = {}
        self.battery_labels = {}
        self.cards = {}

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        header = QWidget()
        header.setFixedHeight(28)
        header.setStyleSheet("background: #0a0a0a; border-bottom: 1px solid #1a1a1a;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(10, 0, 10, 0)
        title = QLabel("XOSS HEART MONITOR")
        title.setStyleSheet("color: #00cc00; font-weight: bold; font-size: 12px; letter-spacing: 2px; background: transparent;")
        title.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        self.header_status = QLabel("Нет датчиков")
        self.header_status.setStyleSheet("color: #cc8800; font-size: 11px; background: transparent;")
        self.header_time = QLabel("")
        self.header_time.setStyleSheet("color: #006600; font-size: 11px; background: transparent;")
        h_layout.addWidget(title)
        h_layout.addStretch()
        h_layout.addWidget(self.header_status)
        h_layout.addSpacing(20)
        h_layout.addWidget(self.header_time)
        main_layout.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: #000; border: none;")
        self.inner = QWidget()
        self.inner.setStyleSheet("background: #000;")
        self.grid = QGridLayout(self.inner)
        self.grid.setSpacing(6)
        self.grid.setContentsMargins(6, 6, 6, 6)
        self.scroll.setWidget(self.inner)
        main_layout.addWidget(self.scroll)

        signals.hr_updated.connect(self.on_hr_updated)
        signals.sensor_renamed.connect(self.on_sensor_renamed)
        signals.battery_updated.connect(self.on_battery_updated)

        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_layout)
        self.refresh_timer.start(1000)

        self.time_timer = QTimer()
        self.time_timer.timeout.connect(self.update_time)
        self.time_timer.start(1000)

    def update_time(self):
        self.header_time.setText(datetime.now().strftime("%d.%m.%Y %H:%M:%S"))

    def on_hr_updated(self, sid, hr):
        if sid in self.ecg_widgets:
            self.ecg_widgets[sid].push(hr)
        if sid in self.hr_labels:
            s = sensors.get(sid, {})
            if s.get("connected"):
                self.hr_labels[sid].setText(str(hr))
                self.hr_labels[sid].setStyleSheet("color: #00ff00; font-size: 48px; font-weight: bold; background: transparent;")
                if sid in self.status_labels:
                    self.status_labels[sid].setText("Подключен")
                    self.status_labels[sid].setStyleSheet("color: #00aa00; font-size: 10px; background: transparent;")
            elif s.get("status") == "reconnecting":
                self.hr_labels[sid].setText("---")
                self.hr_labels[sid].setStyleSheet("color: #cc6600; font-size: 36px; font-weight: bold; background: transparent;")
                if sid in self.status_labels:
                    self.status_labels[sid].setText("Переподключение...")
                    self.status_labels[sid].setStyleSheet("color: #cc6600; font-size: 9px; background: transparent;")
            else:
                self.hr_labels[sid].setText("---")
                self.hr_labels[sid].setStyleSheet("color: #003300; font-size: 36px; font-weight: bold; background: transparent;")
                if sid in self.status_labels:
                    self.status_labels[sid].setText("Отключен")
                    self.status_labels[sid].setStyleSheet("color: #003300; font-size: 10px; background: transparent;")
        self.update_header()

    def on_battery_updated(self, sid, level):
        if sid in self.battery_labels:
            self.battery_labels[sid].setText(f"Заряд: {level}%")

    def on_sensor_renamed(self, sid, new_name):
        if sid in self.name_labels:
            self.name_labels[sid].setText(new_name)
        self.refresh_layout()

    def update_header(self):
        total = len(sensors)
        connected = sum(1 for s in sensors.values() if s.get("connected"))
        if total == 0:
            self.header_status.setText("Нет датчиков")
            self.header_status.setStyleSheet("color: #cc8800; font-size: 11px; background: transparent;")
        elif connected == total:
            self.header_status.setText(f"Подключено: {connected} / {total}")
            self.header_status.setStyleSheet("color: #00cc00; font-size: 11px; background: transparent;")
        else:
            self.header_status.setText(f"Подключено: {connected} / {total}")
            self.header_status.setStyleSheet("color: #cc8800; font-size: 11px; background: transparent;")

    def refresh_layout(self):
        current_ids = set(sensors.keys())
        widget_ids = set(self.cards.keys())
        for sid in widget_ids - current_ids:
            card = self.cards.pop(sid)
            self.grid.removeWidget(card)
            card.deleteLater()
            self.ecg_widgets.pop(sid, None)
            self.hr_labels.pop(sid, None)
            self.status_labels.pop(sid, None)
            self.name_labels.pop(sid, None)
            self.battery_labels.pop(sid, None)
        for sid in current_ids - widget_ids:
            self.add_monitor_card(sid)
        self.update_header()

    def add_monitor_card(self, sid):
        s = sensors[sid]
        color = s.get("color", "#00cc00")
        card = QWidget()
        card.setStyleSheet(f"background: #000; border: 1px solid {color}; border-radius: 4px;")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(4, 4, 4, 4)
        card_layout.setSpacing(4)

        info = QWidget()
        info.setFixedWidth(90)
        info.setStyleSheet(f"background: #050505; border-right: 1px solid {color};")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(4, 4, 4, 4)
        info_layout.setSpacing(2)

        name_lbl = QLabel(s["name"])
        name_lbl.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: bold; background: transparent;")
        name_lbl.setFont(QFont("Tahoma", 8, QFont.Weight.Bold))
        name_lbl.setWordWrap(True)

        hr_lbl = QLabel("---")
        hr_lbl.setStyleSheet("color: #003300; font-size: 36px; font-weight: bold; background: transparent;")
        hr_lbl.setFont(QFont("Courier New", 20, QFont.Weight.Bold))
        hr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        unit_lbl = QLabel("уд/мин")
        unit_lbl.setStyleSheet("color: #004400; font-size: 9px; background: transparent;")
        unit_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        battery_lbl = QLabel("")
        battery_lbl.setStyleSheet("color: #666600; font-size: 9px; background: transparent;")
        battery_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if s.get("battery") is not None:
            battery_lbl.setText(f"Заряд: {s['battery']}%")

        status_lbl = QLabel("Отключен")
        status_lbl.setStyleSheet("color: #003300; font-size: 9px; background: transparent;")
        status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        info_layout.addWidget(name_lbl)
        info_layout.addWidget(hr_lbl)
        info_layout.addWidget(unit_lbl)
        info_layout.addWidget(battery_lbl)
        info_layout.addWidget(status_lbl)
        info_layout.addStretch()

        ecg = EcgWidget(sid)
        card_layout.addWidget(info)
        card_layout.addWidget(ecg)

        n = len(self.cards)
        self.grid.addWidget(card, n // 2, n % 2)

        self.cards[sid] = card
        self.ecg_widgets[sid] = ecg
        self.hr_labels[sid] = hr_lbl
        self.status_labels[sid] = status_lbl
        self.name_labels[sid] = name_lbl
        self.battery_labels[sid] = battery_lbl

class ToastNotification(QWidget):
    def __init__(self, text, parent):
        super().__init__(parent)
        self.setStyleSheet("""
            QWidget {
                background: #fff4d0;
                border: 2px solid #cc8800;
            }
            QLabel {
                color: #663300;
                font-size: 11px;
                background: transparent;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.adjustSize()
        self.setFixedWidth(280)
        self.adjustSize()

    def show_at(self, x, y, duration_ms=5000):
        self.move(x, y)
        self.show()
        self.raise_()
        QTimer.singleShot(duration_ms, self.close_and_delete)

    def close_and_delete(self):
        self.close()
        self.deleteLater()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("XOSS Heart Monitor — Studio Amateur")
        icon_path = resource_path("icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setMinimumSize(900, 600)
        self.setStyleSheet(WIN2K)

        self.log_data = []
        self.logging_active = False
        self.log_interval = 1
        self.csv_filename = ""
        self.monitor_window = None
        self.sensor_cards = {}

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(6, 6, 6, 6)

        ctrl_frame = QFrame()
        ctrl_frame.setStyleSheet("""
            QFrame {
                border-top: 2px solid #fff;
                border-left: 2px solid #fff;
                border-right: 2px solid #404040;
                border-bottom: 2px solid #404040;
            }
        """)
        ctrl_layout = QHBoxLayout(ctrl_frame)
        ctrl_layout.setContentsMargins(6, 6, 6, 6)
        ctrl_layout.setSpacing(4)

        def vsep():
            f = QFrame()
            f.setFrameShape(QFrame.Shape.VLine)
            f.setStyleSheet("border-left: 1px solid #808080; border-right: 1px solid #fff; max-width: 2px;")
            return f

        self.manage_btn = QPushButton("Управление датчиками")
        self.manage_btn.clicked.connect(self.open_device_dialog)
        self.log_btn = QPushButton("Начать запись")
        self.log_btn.clicked.connect(self.toggle_log)
        interval_lbl = QLabel("Интервал (сек):")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 60)
        self.interval_spin.setValue(1)
        self.interval_spin.valueChanged.connect(self.set_interval)
        self.monitor_btn = QPushButton("Открыть монитор")
        self.monitor_btn.clicked.connect(self.open_monitor)

        ctrl_layout.addWidget(self.manage_btn)
        ctrl_layout.addWidget(vsep())
        ctrl_layout.addWidget(self.log_btn)
        ctrl_layout.addWidget(vsep())
        ctrl_layout.addWidget(interval_lbl)
        ctrl_layout.addWidget(self.interval_spin)
        ctrl_layout.addWidget(vsep())
        ctrl_layout.addWidget(self.monitor_btn)
        ctrl_layout.addStretch()
        main_layout.addWidget(ctrl_frame)

        sensors_frame = QFrame()
        sensors_frame.setStyleSheet("""
            QFrame {
                border-top: 2px solid #fff;
                border-left: 2px solid #fff;
                border-right: 2px solid #404040;
                border-bottom: 2px solid #404040;
            }
        """)
        sensors_v = QVBoxLayout(sensors_frame)
        sensors_v.setContentsMargins(6, 6, 6, 6)
        s_lbl = QLabel("Датчики")
        s_lbl.setFont(QFont("Tahoma", 9, QFont.Weight.Bold))
        s_lbl.setStyleSheet("border-bottom: 1px solid #808080; padding-bottom: 2px;")
        sensors_v.addWidget(s_lbl)

        self.sensors_scroll = QScrollArea()
        self.sensors_scroll.setWidgetResizable(True)
        self.sensors_scroll.setStyleSheet("background: #d4d0c8; border: none;")
        self.sensors_inner = QWidget()
        self.sensors_grid = QGridLayout(self.sensors_inner)
        self.sensors_grid.setSpacing(6)
        self.sensors_grid.setContentsMargins(0, 0, 0, 0)
        self.sensors_scroll.setWidget(self.sensors_inner)
        self.empty_label = QLabel("Нет датчиков. Нажмите 'Управление датчиками'.")
        self.empty_label.setStyleSheet("color: #808080;")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sensors_grid.addWidget(self.empty_label, 0, 0)
        sensors_v.addWidget(self.sensors_scroll)
        main_layout.addWidget(sensors_frame, 2)

        log_frame = QFrame()
        log_frame.setStyleSheet("""
            QFrame {
                border-top: 2px solid #fff;
                border-left: 2px solid #fff;
                border-right: 2px solid #404040;
                border-bottom: 2px solid #404040;
            }
        """)
        log_v = QVBoxLayout(log_frame)
        log_v.setContentsMargins(6, 6, 6, 6)
        log_lbl = QLabel("Журнал")
        log_lbl.setFont(QFont("Tahoma", 9, QFont.Weight.Bold))
        log_lbl.setStyleSheet("border-bottom: 1px solid #808080; padding-bottom: 2px;")
        self.log_status_lbl = QLabel("Запись не активна")
        self.log_status_lbl.setStyleSheet("color: #808080;")
        self.log_table = QTableWidget(0, 2)
        self.log_table.setHorizontalHeaderLabels(["Дата", "Время"])
        self.log_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.log_table.setMaximumHeight(160)
        log_v.addWidget(log_lbl)
        log_v.addWidget(self.log_status_lbl)
        log_v.addWidget(self.log_table)
        main_layout.addWidget(log_frame, 1)

        signals.hr_updated.connect(self.on_hr_updated)
        signals.sensor_renamed.connect(self.on_sensor_renamed)
        signals.sensor_disconnected.connect(self.on_sensor_disconnected)
        self.active_toasts = []

        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_sensor_cards)
        self.refresh_timer.start(1000)

        self.log_timer = QTimer()
        self.log_timer.timeout.connect(self.do_log_tick)

        self.no_sensor_timer = QTimer()
        self.no_sensor_timer.setSingleShot(True)
        self.no_sensor_timer.timeout.connect(self.on_no_sensor_timeout)

        self.load_saved_sensors()

    def on_sensor_disconnected(self, sid, name):
        text = f"Связь потеряна с датчиком «{name}». Пытаюсь переподключиться..."
        toast = ToastNotification(text, self)
        self.active_toasts = [t for t in self.active_toasts if t.isVisible()]
        y = 40 + sum(t.height() + 8 for t in self.active_toasts)
        x = self.width() - toast.width() - 20
        self.active_toasts.append(toast)
        toast.show_at(x, y)

    def load_saved_sensors(self):
        saved = load_sensors_config()
        for entry in saved:
            sid = next_sensor_id()
            sensors[sid] = {
                "id": sid,
                "name": entry.get("name", f"Датчик {sid}"),
                "address": entry.get("address", ""),
                "hr": 0,
                "battery": None,
                "connected": False,
                "status": "disconnected",
                "auto_reconnect": True,
                "color": entry.get("color") or color_for_index(sid),
            }
        self.rebuild_sensor_grid()
        for sid, s in sensors.items():
            run_ble(connect_ble(sid, s["address"], signals.hr_updated, signals.battery_updated, signals.sensor_disconnected))

    def open_device_dialog(self):
        dlg = DeviceDialog(self)
        dlg.exec()
        self.rebuild_sensor_grid()

    def rebuild_sensor_grid(self):
        for card in self.sensor_cards.values():
            self.sensors_grid.removeWidget(card)
            card.deleteLater()
        self.sensor_cards.clear()

        if not sensors:
            self.empty_label.show()
            self.update_log_table_headers()
            return
        self.empty_label.hide()

        cols = 5
        for i, (sid, s) in enumerate(sensors.items()):
            card = SensorCard(sid)
            card.remove_btn.clicked.connect(lambda _, x=sid: self.remove_sensor(x))
            self.sensor_cards[sid] = card
            self.sensors_grid.addWidget(card, i // cols, i % cols)

        self.update_log_table_headers()

    def remove_sensor(self, sid):
        if sid in sensors:
            sensors[sid]["auto_reconnect"] = False
            run_ble(disconnect_ble(sid))
            del sensors[sid]
            save_sensors_config()
        self.rebuild_sensor_grid()

    def refresh_sensor_cards(self):
        for card in self.sensor_cards.values():
            card.update_display()

    def on_hr_updated(self, sid, hr):
        if sid in self.sensor_cards:
            self.sensor_cards[sid].update_display()

    def on_sensor_renamed(self, sid, new_name):
        if sid in self.sensor_cards:
            self.sensor_cards[sid].update_display()
        self.update_log_table_headers()
        self.rebuild_sensor_grid()

    def toggle_log(self):
        if not self.logging_active:
            self.logging_active = True
            self.log_btn.setText("Остановить запись")
            self.log_status_lbl.setText("Запись активна...")
            self.log_status_lbl.setStyleSheet("color: #006400; font-weight: bold;")
            self.csv_filename = os.path.join(
                os.path.dirname(os.path.abspath(sys.argv[0])),
                f"журнал_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            )
            ids = sorted(sensors.keys())
            with open(self.csv_filename, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(["Дата", "Время"] + [sensors[i]["name"] for i in ids])
            self.log_timer.start(self.log_interval * 1000)
        else:
            self.logging_active = False
            self.log_btn.setText("Начать запись")
            self.log_status_lbl.setText("Запись остановлена")
            self.log_status_lbl.setStyleSheet("color: #808080;")
            self.log_timer.stop()
            self.no_sensor_timer.stop()

    def set_interval(self, val):
        self.log_interval = val
        if self.logging_active:
            self.log_timer.setInterval(val * 1000)

    def do_log_tick(self):
        ids = sorted(sensors.keys())
        now = datetime.now()
        row = [now.strftime("%d.%m.%Y"), now.strftime("%H:%M:%S")]
        row += [sensors[i]["hr"] if sensors[i]["connected"] else "" for i in ids]
        if self.csv_filename:
            with open(self.csv_filename, "a", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(row)
        self.log_data.append(row)
        if len(self.log_data) > 50:
            self.log_data.pop(0)
        self.update_log_table()
        self.write_txt_files()

        any_connected = any(sensors[i]["connected"] for i in ids)
        if any_connected:
            if self.no_sensor_timer.isActive():
                self.no_sensor_timer.stop()
            self.log_status_lbl.setText("Запись активна...")
            self.log_status_lbl.setStyleSheet("color: #006400; font-weight: bold;")
        else:
            if not self.no_sensor_timer.isActive():
                self.no_sensor_timer.start(30000)
            self.log_status_lbl.setText("Нет подключённых датчиков — запись остановится через 30 сек...")
            self.log_status_lbl.setStyleSheet("color: #cc6600; font-weight: bold;")

    def on_no_sensor_timeout(self):
        if not self.logging_active:
            return
        self.logging_active = False
        self.log_btn.setText("Начать запись")
        self.log_status_lbl.setText("Запись остановлена: нет подключённых датчиков")
        self.log_status_lbl.setStyleSheet("color: #808080;")
        self.log_timer.stop()

    def write_txt_files(self):
        folder = os.path.dirname(os.path.abspath(sys.argv[0]))
        for sid, s in sensors.items():
            if s["connected"]:
                path = os.path.join(folder, f"{s['name']}.txt")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(str(s["hr"]))

    def update_log_table_headers(self):
        ids = sorted(sensors.keys())
        cols = 2 + len(ids)
        self.log_table.setColumnCount(cols)
        headers = ["Дата", "Время"] + [sensors[i]["name"] for i in ids]
        self.log_table.setHorizontalHeaderLabels(headers)

    def update_log_table(self):
        self.log_table.setRowCount(0)
        for row_data in reversed(self.log_data[-20:]):
            row = self.log_table.rowCount()
            self.log_table.insertRow(row)
            for col, val in enumerate(row_data):
                self.log_table.setItem(row, col, QTableWidgetItem(str(val)))

    def open_monitor(self):
        if self.monitor_window is None or not self.monitor_window.isVisible():
            self.monitor_window = MonitorWindow()
        self.monitor_window.show()
        self.monitor_window.raise_()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Windows")
    icon_path = resource_path("icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
