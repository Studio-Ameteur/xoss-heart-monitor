import sys
import random
import threading
import asyncio
import time
import csv
import os
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QDialog,
    QListWidget, QListWidgetItem, QSpinBox, QSplitter, QFrame,
    QScrollArea, QGridLayout, QMessageBox, QHeaderView
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
from PyQt6.QtGui import QPainter, QPen, QColor, QFont

try:
    from bleak import BleakScanner, BleakClient
    BLE_AVAILABLE = True
except ImportError:
    BLE_AVAILABLE = False

HEART_RATE_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

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
    QGroupBox {
        border-top: 2px solid #fff;
        border-left: 2px solid #fff;
        border-right: 2px solid #404040;
        border-bottom: 2px solid #404040;
        margin-top: 6px;
        padding: 6px;
        font-weight: bold;
    }
    QGroupBox::title { subcontrol-origin: margin; left: 6px; }
    QSpinBox {
        background: #fff;
        border-top: 2px solid #808080;
        border-left: 2px solid #808080;
        border-right: 2px solid #fff;
        border-bottom: 2px solid #fff;
        padding: 1px 4px;
        min-width: 50px;
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
    QDialog {
        background: #d4d0c8;
    }
    QLabel { background: transparent; }
"""

sensors = {}
ble_loop = None
clients = {}

def get_ble_loop():
    global ble_loop
    if ble_loop is None or not ble_loop.is_running():
        ble_loop = asyncio.new_event_loop()
        t = threading.Thread(target=ble_loop.run_forever, daemon=True)
        t.start()
    return ble_loop

def run_ble(coro):
    return asyncio.run_coroutine_threadsafe(coro, get_ble_loop())

def hr_callback(sid, signal):
    def callback(sender, data):
        flags = data[0]
        hr = int.from_bytes(data[1:3], "little") if flags & 0x01 else data[1]
        if sid in sensors:
            sensors[sid]["hr"] = hr
            signal.emit(sid, hr)
    return callback

async def connect_ble(sid, address, signal):
    sensors[sid]["status"] = "connecting"
    signal.emit(sid, 0)
    try:
        client = BleakClient(address, timeout=10.0)
        await client.connect()
        clients[sid] = client
        sensors[sid]["connected"] = True
        sensors[sid]["status"] = "connected"
        signal.emit(sid, sensors[sid]["hr"])
        await client.start_notify(HEART_RATE_UUID, hr_callback(sid, signal))
        while sensors[sid]["connected"]:
            if not client.is_connected:
                break
            await asyncio.sleep(1)
        try:
            await client.stop_notify(HEART_RATE_UUID)
            await client.disconnect()
        except:
            pass
    except Exception:
        pass
    if sid in sensors:
        sensors[sid]["connected"] = False
        sensors[sid]["status"] = "disconnected"
        sensors[sid]["hr"] = 0
    if sid in clients:
        del clients[sid]
    signal.emit(sid, 0)

async def disconnect_ble(sid):
    if sid in sensors:
        sensors[sid]["connected"] = False
    client = clients.get(sid)
    if client:
        try:
            await client.stop_notify(HEART_RATE_UUID)
            await client.disconnect()
        except:
            pass
        if sid in clients:
            del clients[sid]
    if sid in sensors:
        sensors[sid]["status"] = "disconnected"
        sensors[sid]["hr"] = 0

class Signals(QObject):
    hr_updated = pyqtSignal(int, int)
    scan_done = pyqtSignal(list)

signals = Signals()

class EcgWidget(QWidget):
    HISTORY = 150

    def __init__(self, sensor_id, parent=None):
        super().__init__(parent)
        self.sid = sensor_id
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
            x = int(W * i / 10)
            painter.drawLine(x, 0, x, H)
        for i in range(1, 5):
            y = int(H * i / 5)
            painter.drawLine(0, y, W, y)

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

        for glow in [(QColor(0,255,0,20), 6), (QColor(0,255,0,60), 3), (QColor(0,255,0,255), 1)]:
            pen = QPen(glow[0], glow[1])
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            step = W / (self.HISTORY - 1)
            path_pts = []
            for i, v in enumerate(self.history):
                hr = v if v > 0 else (mn + rng / 2)
                x = int(i * step)
                y = int(H - ((hr - mn) / rng) * H * 0.8 - H * 0.1)
                path_pts.append((x, y))
            for i in range(1, len(path_pts)):
                painter.drawLine(path_pts[i-1][0], path_pts[i-1][1],
                                 path_pts[i][0], path_pts[i][1])
        painter.end()

class SensorCard(QFrame):
    def __init__(self, sid, parent=None):
        super().__init__(parent)
        self.sid = sid
        self.setFrameStyle(QFrame.Shape.Box)
        self.setStyleSheet("""
            QFrame {
                background: #d4d0c8;
                border-top: 2px solid #fff;
                border-left: 2px solid #fff;
                border-right: 2px solid #404040;
                border-bottom: 2px solid #404040;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setSpacing(2)
        layout.setContentsMargins(6, 6, 6, 6)

        s = sensors[sid]
        self.name_label = QLabel(s["name"])
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setFont(QFont("Tahoma", 9, QFont.Weight.Bold))

        self.hr_label = QLabel("---")
        self.hr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hr_label.setFont(QFont("Courier New", 28, QFont.Weight.Bold))
        self.hr_label.setStyleSheet("color: #808080;")

        self.unit_label = QLabel("")
        self.unit_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.unit_label.setStyleSheet("color: #404040; font-size: 10px;")

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

        layout.addWidget(self.name_label)
        layout.addWidget(self.hr_label)
        layout.addWidget(self.unit_label)
        layout.addWidget(self.status_label)
        layout.addLayout(btn_row)

    def toggle_connection(self):
        s = sensors.get(self.sid, {})
        if s.get("connected"):
            run_ble(disconnect_ble(self.sid))
        else:
            run_ble(connect_ble(self.sid, s["address"], signals.hr_updated))

    def update_display(self):
        s = sensors.get(self.sid, {})
        if not s:
            return
        if s.get("connected"):
            self.hr_label.setText(str(s["hr"]))
            self.hr_label.setStyleSheet("color: #800000;")
            self.unit_label.setText("уд/мин")
            self.status_label.setText("Подключен")
            self.status_label.setStyleSheet("color: #006400; font-size: 10px;")
            self.conn_btn.setText("Откл")
            self.setStyleSheet("""
                QFrame {
                    background: #f0f4f0;
                    border-top: 2px solid #fff;
                    border-left: 2px solid #fff;
                    border-right: 2px solid #404040;
                    border-bottom: 2px solid #404040;
                }
            """)
        elif s.get("status") == "connecting":
            self.hr_label.setText("---")
            self.hr_label.setStyleSheet("color: #808000;")
            self.status_label.setText("Подключение...")
            self.status_label.setStyleSheet("color: #808000; font-size: 10px;")
            self.conn_btn.setText("Откл")
        else:
            self.hr_label.setText("---")
            self.hr_label.setStyleSheet("color: #808080;")
            self.unit_label.setText("")
            self.status_label.setText("Отключен")
            self.status_label.setStyleSheet("color: #808080; font-size: 10px;")
            self.conn_btn.setText("Подкл")
            self.setStyleSheet("""
                QFrame {
                    background: #d4d0c8;
                    border-top: 2px solid #fff;
                    border-left: 2px solid #fff;
                    border-right: 2px solid #404040;
                    border-bottom: 2px solid #404040;
                }
            """)

class DeviceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Управление датчиками")
        self.setMinimumSize(420, 380)
        self.setStyleSheet(WIN2K)
        self.scan_results = []

        layout = QVBoxLayout(self)

        scan_label = QLabel("Поиск устройств Bluetooth:")
        scan_label.setFont(QFont("Tahoma", 9, QFont.Weight.Bold))
        layout.addWidget(scan_label)

        self.scan_btn = QPushButton("Начать поиск")
        self.scan_btn.clicked.connect(self.start_scan)
        layout.addWidget(self.scan_btn)

        self.scan_status = QLabel("Нажмите 'Начать поиск'")
        self.scan_status.setStyleSheet("color: #808080;")
        layout.addWidget(self.scan_status)

        self.scan_list = QListWidget()
        self.scan_list.setMinimumHeight(130)
        layout.addWidget(self.scan_list)

        add_btn = QPushButton("Добавить выбранное")
        add_btn.clicked.connect(self.add_selected)
        layout.addWidget(add_btn)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("border-top: 1px solid #808080; border-bottom: 1px solid #fff;")
        layout.addWidget(sep)

        added_label = QLabel("Добавленные датчики:")
        added_label.setFont(QFont("Tahoma", 9, QFont.Weight.Bold))
        layout.addWidget(added_label)

        self.added_list = QListWidget()
        self.added_list.setMaximumHeight(100)
        layout.addWidget(self.added_list)

        btn_row = QHBoxLayout()
        remove_btn = QPushButton("Удалить выбранный")
        remove_btn.clicked.connect(self.remove_selected)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
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
            fake = [
                {"name": "XOSS Pro 1", "address": "AA:BB:CC:DD:EE:01", "rssi": -55},
                {"name": "XOSS Pro 2", "address": "AA:BB:CC:DD:EE:02", "rssi": -63},
                {"name": "XOSS Pro 3", "address": "AA:BB:CC:DD:EE:03", "rssi": -71},
            ]
            signals.scan_done.emit(fake)

    async def do_scan(self):
        results = []
        try:
            devices = await BleakScanner.discover(timeout=6.0)
            xoss, other = [], []
            for d in devices:
                name = d.name or "Без имени"
                rssi = getattr(d, "rssi", None)
                entry = {"name": name, "address": d.address, "rssi": rssi}
                if any(k in name.upper() for k in ["XOSS","HR","HEART","POLAR","WAHOO","GARMIN"]):
                    xoss.append(entry)
                else:
                    other.append(entry)
            results = xoss + other
        except Exception:
            pass
        signals.scan_done.emit(results)

    def on_scan_done(self, results):
        self.scan_results = results
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
            return
        d = item.data(Qt.ItemDataRole.UserRole)
        sid = len(sensors)
        sensors[sid] = {
            "id": sid,
            "name": d["name"],
            "address": d["address"],
            "hr": 0,
            "connected": False,
            "status": "disconnected"
        }
        run_ble(connect_ble(sid, d["address"], signals.hr_updated))
        self.refresh_added()

    def remove_selected(self):
        item = self.added_list.currentItem()
        if not item:
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        if sid in sensors:
            run_ble(disconnect_ble(sid))
            del sensors[sid]
        self.refresh_added()

    def refresh_added(self):
        self.added_list.clear()
        for sid, s in sensors.items():
            label = f"{s['name']}  |  {s['address']}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, sid)
            self.added_list.addItem(item)

class MonitorWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Монитор пульса — XOSS Heart Monitor")
        self.setStyleSheet("background: #000;")
        self.setMinimumSize(800, 500)
        self.ecg_widgets = {}
        self.hr_labels = {}
        self.status_labels = {}
        self.cards = {}

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: #000; border: none;")
        self.inner = QWidget()
        self.inner.setStyleSheet("background: #000;")
        self.grid = QGridLayout(self.inner)
        self.grid.setSpacing(6)
        self.grid.setContentsMargins(6, 6, 6, 6)
        self.scroll.setWidget(self.inner)

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
        main_layout.addWidget(self.scroll)

        signals.hr_updated.connect(self.on_hr_updated)

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_layout)
        self.timer.start(1000)

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
            else:
                self.hr_labels[sid].setText("---")
                self.hr_labels[sid].setStyleSheet("color: #003300; font-size: 36px; font-weight: bold; background: transparent;")
                if sid in self.status_labels:
                    self.status_labels[sid].setText("Отключен")
                    self.status_labels[sid].setStyleSheet("color: #003300; font-size: 10px; background: transparent;")
        self.update_header()

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

        for sid in current_ids - widget_ids:
            self.add_monitor_card(sid)

        self.update_header()

    def add_monitor_card(self, sid):
        s = sensors[sid]
        card = QWidget()
        card.setStyleSheet("background: #000; border: 1px solid #003300; border-radius: 4px;")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(4, 4, 4, 4)
        card_layout.setSpacing(4)

        info = QWidget()
        info.setFixedWidth(90)
        info.setStyleSheet("background: #050505; border-right: 1px solid #003300;")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(4, 4, 4, 4)
        info_layout.setSpacing(2)

        name_lbl = QLabel(s["name"])
        name_lbl.setStyleSheet("color: #00aa00; font-size: 10px; font-weight: bold; background: transparent;")
        name_lbl.setFont(QFont("Tahoma", 8, QFont.Weight.Bold))
        name_lbl.setWordWrap(True)

        hr_lbl = QLabel("---")
        hr_lbl.setStyleSheet("color: #003300; font-size: 36px; font-weight: bold; background: transparent;")
        hr_lbl.setFont(QFont("Courier New", 20, QFont.Weight.Bold))
        hr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        unit_lbl = QLabel("уд/мин")
        unit_lbl.setStyleSheet("color: #004400; font-size: 9px; background: transparent;")
        unit_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        status_lbl = QLabel("Отключен")
        status_lbl.setStyleSheet("color: #003300; font-size: 9px; background: transparent;")
        status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        info_layout.addWidget(name_lbl)
        info_layout.addWidget(hr_lbl)
        info_layout.addWidget(unit_lbl)
        info_layout.addWidget(status_lbl)
        info_layout.addStretch()

        ecg = EcgWidget(sid)
        ecg.setMinimumHeight(80)

        card_layout.addWidget(info)
        card_layout.addWidget(ecg)

        n = len(self.cards)
        row, col = n // 2, n % 2
        self.grid.addWidget(card, row, col)

        self.cards[sid] = card
        self.ecg_widgets[sid] = ecg
        self.hr_labels[sid] = hr_lbl
        self.status_labels[sid] = status_lbl

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("XOSS Heart Monitor — Studio Amateur")
        self.setMinimumSize(900, 600)
        self.setStyleSheet(WIN2K)

        self.log_data = []
        self.logging_active = False
        self.log_interval = 1
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
                background: #d4d0c8;
            }
        """)
        ctrl_layout = QHBoxLayout(ctrl_frame)
        ctrl_layout.setContentsMargins(6, 6, 6, 6)
        ctrl_layout.setSpacing(4)

        self.manage_btn = QPushButton("Управление датчиками")
        self.manage_btn.clicked.connect(self.open_device_dialog)
        self.log_btn = QPushButton("Начать запись")
        self.log_btn.clicked.connect(self.toggle_log)
        interval_label = QLabel("Интервал (сек):")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 60)
        self.interval_spin.setValue(1)
        self.interval_spin.valueChanged.connect(self.set_interval)
        self.monitor_btn = QPushButton("Открыть монитор")
        self.monitor_btn.clicked.connect(self.open_monitor)

        def sep():
            f = QFrame()
            f.setFrameShape(QFrame.Shape.VLine)
            f.setStyleSheet("border-left: 1px solid #808080; border-right: 1px solid #fff; max-width: 2px;")
            return f

        ctrl_layout.addWidget(self.manage_btn)
        ctrl_layout.addWidget(sep())
        ctrl_layout.addWidget(self.log_btn)
        ctrl_layout.addWidget(sep())
        ctrl_layout.addWidget(interval_label)
        ctrl_layout.addWidget(self.interval_spin)
        ctrl_layout.addWidget(sep())
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
                background: #d4d0c8;
            }
        """)
        sensors_v = QVBoxLayout(sensors_frame)
        sensors_v.setContentsMargins(6, 6, 6, 6)
        lbl = QLabel("Датчики")
        lbl.setFont(QFont("Tahoma", 9, QFont.Weight.Bold))
        lbl.setStyleSheet("border-bottom: 1px solid #808080; padding-bottom: 2px;")
        sensors_v.addWidget(lbl)

        self.sensors_scroll = QScrollArea()
        self.sensors_scroll.setWidgetResizable(True)
        self.sensors_scroll.setStyleSheet("background: #d4d0c8; border: none;")
        self.sensors_inner = QWidget()
        self.sensors_inner.setStyleSheet("background: #d4d0c8;")
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
                background: #d4d0c8;
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

        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_sensor_cards)
        self.refresh_timer.start(1000)

        self.log_timer = QTimer()
        self.log_timer.timeout.connect(self.do_log_tick)

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
            return
        self.empty_label.hide()

        cols = 5
        for i, (sid, s) in enumerate(sensors.items()):
            card = SensorCard(sid)
            card.remove_btn.clicked.connect(lambda _, x=sid: self.remove_sensor(x))
            self.sensor_cards[sid] = card
            row, col = i // cols, i % cols
            self.sensors_grid.addWidget(card, row, col)

        self.update_log_table_headers()

    def remove_sensor(self, sid):
        if sid in sensors:
            run_ble(disconnect_ble(sid))
            del sensors[sid]
        self.rebuild_sensor_grid()

    def refresh_sensor_cards(self):
        for sid, card in self.sensor_cards.items():
            card.update_display()

    def on_hr_updated(self, sid, hr):
        if sid in self.sensor_cards:
            self.sensor_cards[sid].update_display()

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

    def set_interval(self, val):
        self.log_interval = val
        if self.logging_active:
            self.log_timer.setInterval(val * 1000)

    def do_log_tick(self):
        ids = sorted(sensors.keys())
        now = datetime.now()
        row = [now.strftime("%d.%m.%Y"), now.strftime("%H:%M:%S")]
        row += [sensors[i]["hr"] if sensors[i]["connected"] else "" for i in ids]
        with open(self.csv_filename, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(row)
        self.log_data.append(row)
        if len(self.log_data) > 50:
            self.log_data.pop(0)
        self.update_log_table()
        self.write_txt_files()

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
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
