import copy
import queue
import traceback
from dataclasses import asdict, dataclass, fields
from enum import Enum
from multiprocessing import Pipe, Process, Value
from typing import Optional
from unittest.mock import Mock

import bluesky.plan_stubs as bps
import pandas as pd
from bluesky.run_engine import RunEngine
from ophyd import Component as Cpt
from ophyd import Device, EpicsMotor, EpicsSignalRO
from qmicroscope import Microscope
from qtpy import QtCore, QtGui, QtWidgets

# Create a mock object for shutter
# shutter = Mock()

# Mock the status.get() method to return "Open"
# shutter.status.get.return_value = "Open"


RE = RunEngine()
x = None
y = None

"""
class MaiaStage(Device):
    x = Cpt(EpicsMotor, "{PI180:1-Ax:MaiaX}Mtr")
    y = Cpt(EpicsMotor, "{PI180:1-Ax:MaiaY}Mtr")
    z = Cpt(EpicsMotor, "{PI180:1-Ax:MaiaZ}Mtr")
    r = Cpt(EpicsMotor, "{SR50pp:1-Ax:MaiaR}Mtr")


M = MaiaStage("XF:04BMC-ES:2", name="M")
"""


@dataclass
class SampleMetadata:
    info: str = ""
    # name: str = ""
    owner: str = ""
    serial: str = ""
    type: str = ""


@dataclass
class ScanMetadata:
    region: str = ""
    info: str = ""
    seq_num: str = ""
    seq_total: str = ""


@dataclass
class MaiaFlyDefinition:
    ystart: float
    ystop: float
    ypitch: int
    xstart: float
    xstop: float
    xpitch: int
    dwell: float
    name: str = ""
    md: Optional[SampleMetadata | ScanMetadata] = None


class LEDState(Enum):
    QUEUED = QtCore.Qt.GlobalColor.blue
    COLLECTING = QtCore.Qt.GlobalColor.red
    COMPLETE = QtCore.Qt.GlobalColor.green
    NOT_QUEUED = QtCore.Qt.GlobalColor.gray


class QueueItem:
    def __init__(self, label, data):
        self.label = label
        self.data = data


class QueueModel:
    def __init__(self):
        self.queue: list[QueueItem] = []

    def add_item(self, item):
        self.queue.append(item)

    def remove_item(self, index):
        if 0 <= index < len(self.queue):
            del self.queue[index]

    def move_item_up(self, index):
        if 1 <= index < len(self.queue):
            self.queue[index - 1], self.queue[index] = (
                self.queue[index],
                self.queue[index - 1],
            )

    def move_item_down(self, index):
        if 0 <= index < len(self.queue) - 1:
            self.queue[index + 1], self.queue[index] = (
                self.queue[index],
                self.queue[index + 1],
            )

    def get_items(self):
        return self.queue


class QueueWidget(QtWidgets.QWidget):
    queue_updated = QtCore.Signal(object)

    def __init__(self):
        super().__init__()
        self.model = QueueModel()
        self.init_ui()

    def init_ui(self):
        self.setLayout(QtWidgets.QVBoxLayout())

        self.list_widget = QtWidgets.QListWidget()
        self.layout().addWidget(self.list_widget)

        # self.add_button = QtWidgets.QPushButton("Add Item")
        # self.add_button.clicked.connect(self.add_item)
        # self.layout().addWidget(self.add_button)

        self.remove_button = QtWidgets.QPushButton("Remove Item")
        self.remove_button.clicked.connect(self.remove_item)
        self.layout().addWidget(self.remove_button)

        self.up_button = QtWidgets.QPushButton("Move Up")
        self.up_button.clicked.connect(self.move_item_up)
        self.layout().addWidget(self.up_button)

        self.down_button = QtWidgets.QPushButton("Move Down")
        self.down_button.clicked.connect(self.move_item_down)
        self.layout().addWidget(self.down_button)
        self.list_widget.itemEntered.connect(self.show_tooltip)

    def show_tooltip(self, item):
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), item.toolTip())

    def add_item(self):
        """
        item, ok = QtWidgets.QInputDialog.getText(
            self,
            "Add Item",
            "Enter item as a comma-separated tuple (e.g., 'value1,value2,value3,value4'):",
        )
        if ok:
            item_tuple = tuple(item.split(","))
            if len(item_tuple) == 4:
                self.model.add_item(item_tuple)
                self.update_list()
        """
        pass

    def remove_item(self):
        selected_items = self.list_widget.selectedItems()
        if selected_items:
            index = self.list_widget.row(selected_items[0])
            self.model.remove_item(index)
            self.update_list()

    def move_item_up(self):
        selected_items = self.list_widget.selectedItems()
        if selected_items:
            index = self.list_widget.row(selected_items[0])
            self.model.move_item_up(index)
            self.update_list()

    def move_item_down(self):
        selected_items = self.list_widget.selectedItems()
        if selected_items:
            index = self.list_widget.row(selected_items[0])
            self.model.move_item_down(index)
            self.update_list()

    def update_list(self):
        self.list_widget.clear()
        for item in self.model.get_items():
            list_item = QtWidgets.QListWidgetItem(str(item.label))
            self.list_widget.addItem(list_item)

        self.queue_updated.emit(self.model.get_items())


class SamplePositionQueueWidget(QueueWidget):
    go_to_position_signal = QtCore.Signal(float, float, float)

    def contextMenuEvent(self, event):
        # Find the item at the click position
        item = self.list_widget.itemAt(
            self.list_widget.mapFromGlobal(event.globalPos())
        )

        if item:
            # Create a context menu
            menu = QtWidgets.QMenu(self)

            # Add actions to the menu
            go_to_position_action = menu.addAction("Go to position")

            go_to_position_action.triggered.connect(
                lambda: self.emit_go_to_position(item)
            )

            # Execute the menu and get the selected action
            action = menu.exec_(event.globalPos())

    def emit_go_to_position(self, item):
        index = self.list_widget.indexFromItem(item).row()
        position_item = self.model.get_items()[index]
        self.emit_go_to_position.emit(*position_item.data)

    def add_item(self, position_name, x, y, z):
        item = QueueItem(label=position_name, data=(x, y, z))
        self.model.add_item(item)
        self.update_list()


class CollectionQueueWidget(QueueWidget):
    selected_item_data_signal = QtCore.Signal(object, int)

    def contextMenuEvent(self, event):
        # Find the item at the click position
        item = self.list_widget.itemAt(
            self.list_widget.mapFromGlobal(event.globalPos())
        )

        if item:
            # Create a context menu
            menu = QtWidgets.QMenu(self)

            # Add actions to the menu
            edit_action = menu.addAction("Edit Request")
            # remove_action = menu.addAction("Remove")
            # move_up_action = menu.addAction("Move Up")
            # move_down_action = menu.addAction("Move Down")

            edit_action.triggered.connect(lambda: self.edit_item(item))

            # Execute the menu and get the selected action
            action = menu.exec_(event.globalPos())

    def edit_item(self, item):
        index = self.list_widget.indexFromItem(item).row()
        queue_item = self.model.get_items()[index]
        self.selected_item_data_signal.emit(queue_item.data, index)

    def add_item(self, label, data: MaiaFlyDefinition):
        item = QueueItem(label=label, data=data)
        self.model.add_item(item)
        self.update_list()

    def update_list(self):
        self.list_widget.clear()
        for item in self.model.get_items():
            list_item = QtWidgets.QListWidgetItem(str(item.label))
            text = """<table border='1' style='border-collapse: collapse;'>
            <tr>
            <th style='border: 1px solid black;'>Parameter</th>
            <th style='border: 1px solid black;'>Value</th>
            </tr>"""
            # for key, value in item.data[0].items():
            for field in fields(item.data):
                key = field.name
                value = getattr(item.data, key)

                text += f"""<tr><td style='border: 1px solid black;'>{key}</td>
                <td style='border: 1px solid black;'>{value}</td></tr>"""
            text = text + "</table>"
            list_item.setToolTip(text)
            self.list_widget.addItem(list_item)

        self.queue_updated.emit(self.model.get_items())


class RunEngineState(Enum):
    idle = 0
    running = 1
    paused = 2


class RunEngineWorker(Process):
    def __init__(self, conn, run_engine_state: Value):
        super().__init__()
        self.conn = conn
        self.run_engine_state = run_engine_state

    def update_run_engine_state(self, new, old):
        try:
            current_state = RunEngineState[new]
        except KeyError as e:
            current_state = None
        if current_state is not None:
            self.run_engine_state.value = current_state.value

    def initialize_run_engine(self):
        self.RE = RunEngine()
        self.RE.state_hook = self.update_run_engine_state
        M = MaiaStage("XF:04BMC-ES:2", name="M")

    def run(self):
        self.initialize_run_engine()

        while True:
            message = self.conn.recv()
            if message == "STOP":
                break
            else:
                plan = self.plan_selector(payload=message)
                if plan:
                    try:
                        self.conn.send({"status": f"running plan for {message}"})
                        self.RE(plan)
                        self.conn.send({"status": "completed"})
                    except Exception as e:
                        self.conn.send(
                            {
                                "status": "failed",
                                "error": str(e),
                                "traceback": traceback.format_exc(),
                            }
                        )

    def plan_selector(self, payload: MaiaFlyDefinition):
        pass


class PipeMonitorThread(QtCore.QThread):
    status_update = QtCore.Signal(object)

    def __init__(self, parent_conn):
        super().__init__()
        self.parent_conn = parent_conn

    def run(self):
        while True:
            status = self.parent_conn.recv()  # Blocking call
            self.status_update.emit(status)


class RunQueueThread(QtCore.QThread):
    status_update = QtCore.Signal(object)
    update_pause = QtCore.Slot(bool)
    update_stop = QtCore.Slot(bool)

    def __init__(self, queue, parent_conn):
        super().__init__()
        self.queue = queue
        self.parent_conn = parent_conn
        self.paused = False
        self.stop = False

    def run(self):
        for scan_item in self.queue:
            self.parent_conn.send(scan_item.data)
            while self.paused:
                pass
            if self.stop:
                break

    @update_pause
    def set_pause(self, value):
        self.paused = value

    @update_stop
    def set_stop(self, value):
        self.stop = value


class RunEngineControls(QtCore.QObject):
    re_state_update_signal = QtCore.Signal(str, str, bool, str, bool, str)

    def __init__(self, GUI: "ScanControlWidget", motors):
        super().__init__()
        self.GUI = GUI
        self.motors = motors

        self.widget = button_widget = QtWidgets.QWidget()
        button_layout = QtWidgets.QHBoxLayout()
        button_widget.setLayout(button_layout)

        self.label = label = QtWidgets.QLabel("Idle")
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setStyleSheet("QLabel {background-color: green; color: white}")
        button_layout.addWidget(label)

        # Run button to execute RE
        self.button_run = button_run = QtWidgets.QPushButton("Run")
        button_run.clicked.connect(self.run)
        button_layout.addWidget(button_run)

        # Run button to execute RE
        self.button_pause = button_pause = QtWidgets.QPushButton("Pause")
        button_pause.clicked.connect(self.pause)
        button_layout.addWidget(button_pause)

        self.info_label = info_label = QtWidgets.QLabel("Motors info")
        info_label.setAlignment(QtCore.Qt.AlignLeft)
        # label.setStyleSheet('QLabel {background-color: green; color: white}')
        button_layout.addWidget(info_label)
        self.re_state_update_signal.connect(self.update_state_ui)
        self.parent_conn, self.child_conn = Pipe()
        self.run_engine_state = Value("i", RunEngineState.idle.value)

        self.worker_process = RunEngineWorker(
            conn=self.child_conn, run_engine_state=self.run_engine_state
        )
        self.worker_process.start()

        self.monitor_thread = PipeMonitorThread(self.parent_conn)
        self.monitor_thread.status_update.connect(self.update_status)
        self.monitor_thread.start()
        self.run_queue_thread = None

    def update_status(self, status):
        print(status)

    def run(self):
        if RunEngineState(self.run_engine_state.value) == RunEngineState.idle and (
            self.run_queue_thread is None or not self.run_queue_thread.isRunning()
        ):

            self.run_queue_thread = RunQueueThread(
                self.GUI.queue_widget.model.get_items(), self.parent_conn
            )
            self.run_queue_thread.start()

    def pause(self):
        if RunEngineState(self.run_engine_state.value) == RunEngineState.running:
            if self.run_queue_thread is not None and self.run_queue_thread.isRunning():
                self.run_queue_thread.set_pause(True)
        elif RunEngineState(self.run_engine_state.value) == RunEngineState.paused:
            if self.run_queue_thread is not None and self.run_queue_thread.isRunning():
                self.run_queue_thread.set_stop(True)

    def handle_state_change(self, new, old):
        print(new)
        new = "idle"
        if new == "idle":
            color = "green"
            button_run_enabled = True
            button_pause_enabled = False
            button_run_text = "Run"
            button_pause_text = "Pause"
        elif new == "paused":
            color = "blue"
            button_run_enabled = True
            button_pause_enabled = True
            button_run_text = "Resume"
            button_pause_text = "Stop"
        elif new == "running":
            color = "red"
            button_run_enabled = False
            button_pause_enabled = True
            button_run_text = "Run"
            button_pause_text = "Pause"
        else:
            color = "darkGray"
            button_run_enabled = False
            button_pause_enabled = False
            button_run_text = "Run"
            button_pause_text = "Stop"

        state = str(new).capitalize()
        self.re_state_update_signal.emit(
            color,
            state,
            button_run_enabled,
            button_run_text,
            button_pause_enabled,
            button_pause_text,
        )

    def update_state_ui(
        self,
        color,
        state,
        button_run_enabled,
        button_run_text,
        button_pause_enabled,
        button_pause_text,
    ):
        print("Updating UI")
        width = 60
        height = 60
        self.label.setFixedHeight(width)
        self.label.setFixedWidth(height)
        self.label.setStyleSheet(f"QLabel {{background-color: {color}; color: white}}")
        self.label.setText(state)

        self.info_label.setText("")
        self.button_run.setEnabled(button_run_enabled)
        self.button_run.setText(button_run_text)
        self.button_pause.setEnabled(button_pause_enabled)
        self.button_pause.setText(button_pause_text)


class SampleControlWidget(QtWidgets.QGroupBox):
    def __init__(self):
        super().__init__()

        # label = QtWidgets.QLabel("Sample control widget")
        self.setTitle("Sample Control")
        nudge_buttons = QtWidgets.QGridLayout()
        # nudge_buttons.addWidget(label)
        self.nudge_amount_spin_box = QtWidgets.QSpinBox()
        self.nudge_amount_spin_box.setValue(10)
        self.nudge_amount_spin_box.setMaximum(5000)
        self.nudge_amount_spin_box.setMinimum(0)

        self.focus_amount_spin_box = QtWidgets.QSpinBox()
        self.focus_amount_spin_box.setValue(10)
        self.focus_amount_spin_box.setMaximum(100)
        self.focus_amount_spin_box.setMinimum(0)

        up_button = QtWidgets.QToolButton()

        up_button.setArrowType(QtCore.Qt.ArrowType.UpArrow)
        up_button.clicked.connect(lambda: self.nudge("up"))

        down_button = QtWidgets.QToolButton()
        down_button.setArrowType(QtCore.Qt.ArrowType.DownArrow)
        down_button.clicked.connect(lambda: self.nudge("down"))

        left_button = QtWidgets.QToolButton()
        left_button.setArrowType(QtCore.Qt.ArrowType.LeftArrow)
        left_button.clicked.connect(lambda: self.nudge("left"))

        right_button = QtWidgets.QToolButton()
        right_button.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        right_button.clicked.connect(lambda: self.nudge("right"))

        focus_in_button = QtWidgets.QToolButton()
        focus_in_button.setText("+")
        focus_in_button.clicked.connect(lambda: self.nudge("in"))

        focus_out_button = QtWidgets.QToolButton()
        focus_out_button.setText("-")
        focus_out_button.clicked.connect(lambda: self.nudge("out"))

        widget_label = QtWidgets.QLabel("Sample controls")
        layout = QtWidgets.QGridLayout()

        nudge_buttons.addWidget(
            self.nudge_amount_spin_box, 1, 1, QtCore.Qt.AlignmentFlag.AlignCenter
        )
        nudge_buttons.addWidget(up_button, 0, 1, QtCore.Qt.AlignmentFlag.AlignCenter)
        nudge_buttons.addWidget(down_button, 2, 1, QtCore.Qt.AlignmentFlag.AlignCenter)
        nudge_buttons.addWidget(left_button, 1, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        nudge_buttons.addWidget(right_button, 1, 2, QtCore.Qt.AlignmentFlag.AlignCenter)

        nudge_buttons.addWidget(
            focus_in_button, 0, 3, QtCore.Qt.AlignmentFlag.AlignCenter
        )
        # nudge_buttons.addWidget(
        #    self.focus_amount_spin_box, 1, 3, QtCore.Qt.AlignmentFlag.AlignCenter
        # )
        nudge_buttons.addWidget(
            focus_out_button, 2, 3, QtCore.Qt.AlignmentFlag.AlignCenter
        )

        x_label = QtWidgets.QLabel("X Pos:")
        self.x_rb_label = QtWidgets.QLabel("0")
        self.x_val_input = QtWidgets.QLineEdit()
        y_label = QtWidgets.QLabel("Y Pos:")
        self.y_rb_label = QtWidgets.QLabel("0")
        self.y_val_input = QtWidgets.QLineEdit()
        z_label = QtWidgets.QLabel("Z Pos:")
        self.z_rb_label = QtWidgets.QLabel("0")
        self.z_val_input = QtWidgets.QLineEdit()
        nudge_buttons.addWidget(x_label, 0, 4)
        nudge_buttons.addWidget(self.x_rb_label, 0, 5)
        nudge_buttons.addWidget(self.x_val_input, 0, 6)
        nudge_buttons.addWidget(y_label, 1, 4)
        nudge_buttons.addWidget(self.y_rb_label, 1, 5)
        nudge_buttons.addWidget(self.y_val_input, 1, 6)
        nudge_buttons.addWidget(z_label, 2, 4)
        nudge_buttons.addWidget(self.z_rb_label, 2, 5)
        nudge_buttons.addWidget(self.z_val_input, 2, 6)

        self.x_val_input.returnPressed.connect(lambda: self.set_motor_position("x"))
        self.y_val_input.returnPressed.connect(lambda: self.set_motor_position("y"))
        self.z_val_input.returnPressed.connect(lambda: self.set_motor_position("z"))

        readback_values_layout = QtWidgets.QGridLayout()

        M.x.subscribe(lambda value, **kwargs: self.update_label("x", value))
        M.y.subscribe(lambda value, **kwargs: self.update_label("y", value))
        M.z.subscribe(lambda value, **kwargs: self.update_label("z", value))

        self.position_save_text_box = QtWidgets.QLineEdit()
        self.position_save_button = QtWidgets.QPushButton("Save Position")

        readback_values_layout.addWidget(self.position_save_text_box, 3, 0)
        readback_values_layout.addWidget(self.position_save_button, 3, 1)

        self.saved_positions_list = SamplePositionQueueWidget()
        self.position_save_button.clicked.connect(self.save_motor_positions)
        self.saved_positions_list.go_to_position_signal.connect(
            self.set_motor_positions
        )
        readback_values_layout.addWidget(self.saved_positions_list, 5, 0, 1, 2)

        layout.addWidget(widget_label, 0, 0)
        layout.addLayout(nudge_buttons, 1, 0)
        layout.addLayout(readback_values_layout, 2, 0, 1, 2)

        self.setLayout(layout)

    def set_motor_position(self, pos):
        try:
            if pos == "x":
                M.x.user_setpoint.set(float(self.x_val_input.text()))
            elif pos == "y":
                M.y.user_setpoint.set(float(self.y_val_input.text()))
            elif pos == "z":
                M.z.user_setpoint.set(float(self.z_val_input.text()))
        except Exception as e:
            pass

    def set_motor_positions(self, x, y, z):
        self.x_val_input.setText(str(x))
        self.y_val_input.setText(str(y))
        self.z_val_input.setText(str(z))
        self.set_motor_position("x")
        self.set_motor_position("y")
        self.set_motor_position("z")

    def save_motor_positions(self):
        self.saved_positions_list.add_item(
            self.position_save_text_box.text(),
            M.x.user_readback.get(),
            M.y.user_readback.get(),
            M.z.user_readback.get(),
        )

    def update_label(self, label_name, value):
        label_mapping = {
            "x": self.x_rb_label,
            "y": self.y_rb_label,
            "z": self.z_rb_label,
        }
        label_mapping[label_name].setText(str(value))

    def nudge(self, direction: str):
        direction_motors = {
            "up": (M.y, 1),
            "down": (M.y, -1),
            "left": (M.x, -1),
            "right": (M.x, 1),
            "in": (M.z, 1),
            "out": (M.z, -1),
        }
        motor, factor = direction_motors[direction]
        RE(bps.mvr(motor, float(self.nudge_amount_spin_box.text()) * factor))


class ScanSetupWidget(QtWidgets.QGroupBox):
    add_to_queue_signal = QtCore.Signal(str, object)

    def __init__(self):
        super().__init__()
        self.widget_layout = QtWidgets.QGridLayout()
        self.setTitle("Scan Setup")
        # label = QtWidgets.QLabel("Scan Setup widget")
        # self.widget_layout.addWidget(label)
        self.setLayout(self.widget_layout)
        self.positions: list[QueueItem] = []
        self.setup_position_inputs()
        self.setup_other_inputs()
        self.setup_metadata_inputs()

    def setup_position_inputs(self):
        validator = QtGui.QDoubleValidator()
        self.start_x_input = QtWidgets.QLineEdit()
        self.start_x_input.setValidator(validator)
        self.start_y_input = QtWidgets.QLineEdit()
        self.start_y_input.setValidator(validator)
        self.stop_x_input = QtWidgets.QLineEdit()
        self.stop_x_input.setValidator(validator)
        self.stop_y_input = QtWidgets.QLineEdit()
        self.stop_y_input.setValidator(validator)

        self.start_presets_combobox = QtWidgets.QComboBox()
        self.end_presets_combobox = QtWidgets.QComboBox()
        self.start_presets_combobox.currentIndexChanged.connect(self.populate_start)
        self.end_presets_combobox.currentIndexChanged.connect(self.populate_end)

        start_label = QtWidgets.QLabel("Start")
        stop_label = QtWidgets.QLabel("Stop")
        x_label = QtWidgets.QLabel("X")
        y_label = QtWidgets.QLabel("Y")

        self.widget_layout.addWidget(start_label, 1, 0)
        self.widget_layout.addWidget(stop_label, 2, 0)
        self.widget_layout.addWidget(x_label, 0, 1)
        self.widget_layout.addWidget(y_label, 0, 2)

        self.widget_layout.addWidget(self.start_x_input, 1, 1)
        self.widget_layout.addWidget(self.start_y_input, 1, 2)
        self.widget_layout.addWidget(self.stop_x_input, 2, 1)
        self.widget_layout.addWidget(self.stop_y_input, 2, 2)
        self.widget_layout.addWidget(self.start_presets_combobox, 1, 3)
        self.widget_layout.addWidget(self.end_presets_combobox, 2, 3)

    def populate_start(self, idx):
        if self.positions:
            x, y, z = self.positions[idx].data
            self.start_x_input.setText(str(x))
            self.start_y_input.setText(str(y))

    def populate_end(self, idx):
        if self.positions:
            x, y, z = self.positions[idx].data
            self.stop_x_input.setText(str(x))
            self.stop_y_input.setText(str(y))

    def update_combo_boxes(self, positions: list[QueueItem]):
        self.positions = [QueueItem("", (0, 0, 0))] + positions
        self.start_presets_combobox.clear()
        self.start_presets_combobox.addItems([pos.label for pos in self.positions])
        self.end_presets_combobox.clear()
        self.end_presets_combobox.addItems([pos.label for pos in self.positions])

    def setup_other_inputs(self):
        float_validator = QtGui.QDoubleValidator()
        int_validator = QtGui.QIntValidator()
        self.estimated_time = QtWidgets.QLabel("0")
        self.step_size_input = QtWidgets.QLineEdit()

        self.step_size_input.setValidator(int_validator)
        self.dwell_input = QtWidgets.QLineEdit()

        self.dwell_input.setValidator(float_validator)

        self.widget_layout.addWidget(QtWidgets.QLabel("Step Size: "), 3, 0)
        self.widget_layout.addWidget(self.step_size_input, 3, 1)
        self.widget_layout.addWidget(QtWidgets.QLabel("Dwell : "), 4, 0)
        self.widget_layout.addWidget(self.dwell_input, 4, 1)

        self.scan_name_input = QtWidgets.QLineEdit()
        self.widget_layout.addWidget(QtWidgets.QLabel("Scan Name: "), 5, 0)
        self.widget_layout.addWidget(self.scan_name_input, 5, 1)

        self.widget_layout.addWidget(QtWidgets.QLabel("Estimated Time: "), 6, 0)
        self.widget_layout.addWidget(self.estimated_time, 6, 1)

        self.add_to_queue_button = QtWidgets.QPushButton("Add to Queue")
        self.widget_layout.addWidget(self.add_to_queue_button, 9, 0)

        # Connect slots
        self.start_x_input.textChanged.connect(self.calculate_estimated_time)
        self.stop_x_input.textChanged.connect(self.calculate_estimated_time)
        self.start_y_input.textChanged.connect(self.calculate_estimated_time)
        self.stop_y_input.textChanged.connect(self.calculate_estimated_time)
        self.step_size_input.textChanged.connect(self.calculate_estimated_time)
        self.dwell_input.textChanged.connect(self.calculate_estimated_time)
        self.add_to_queue_button.clicked.connect(self.add_to_queue)

    def calculate_estimated_time(self, _):
        try:
            num_pixels_x = abs(
                float(self.stop_x_input.text()) - float(self.start_x_input.text())
            ) / float(self.step_size_input.text())
            num_pixels_y = abs(
                float(self.stop_y_input.text()) - float(self.start_y_input.text())
            ) / float(self.step_size_input.text())
            # Time in ms
            est_time = num_pixels_x * num_pixels_y * float(self.dwell_input.text())
            self.estimated_time.setText(str(est_time / 1000))
        except Exception as e:
            pass

    def setup_metadata_inputs(self):
        self.dynamic_widget_container = QtWidgets.QWidget()
        self.dynamic_layout = QtWidgets.QGridLayout(self.dynamic_widget_container)
        self.widget_layout.addWidget(self.dynamic_widget_container, 8, 0)
        self.metadata_type_combobox = QtWidgets.QComboBox()
        self.metadata_type_combobox.currentIndexChanged.connect(self.update_line_edits)
        self.metadata_type_combobox.addItems(["sample", "scan"])
        self.widget_layout.addWidget(QtWidgets.QLabel("Metadata type: "), 7, 0)
        self.widget_layout.addWidget(self.metadata_type_combobox, 7, 1)

    def update_line_edits(self):
        for i in reversed(range(self.dynamic_layout.count())):
            widget = self.dynamic_layout.itemAt(i).widget()
            if widget is not None:
                widget.deleteLater()

        # Create new labels and QLineEdits based on the combo box selection
        option = self.metadata_type_combobox.currentText()
        if option == "sample":
            labels = [field.name for field in fields(SampleMetadata)]
        elif option == "scan":
            labels = [field.name for field in fields(ScanMetadata)]

        # Add new QLineEdits and labels
        for i, label_text in enumerate(labels):
            label = QtWidgets.QLabel(f"{label_text}")
            line_edit = QtWidgets.QLineEdit()
            self.dynamic_layout.addWidget(label, i, 0)
            self.dynamic_layout.addWidget(line_edit, i, 1)

    def fill_inputs_from_definition(self, data: MaiaFlyDefinition, index=None):
        self.start_x_input.setText(str(data.xstart))
        self.start_y_input.setText(str(data.ystart))
        self.stop_x_input.setText(str(data.xstop))
        self.stop_y_input.setText(str(data.ystop))
        self.step_size_input.setText(str(data.xpitch))
        self.dwell_input.setText(str(data.dwell))
        self.scan_name_input.setText(str(data.name))

        if isinstance(data.md, SampleMetadata):
            self.metadata_type_combobox.setCurrentText("sample")
            for i, field in enumerate(fields(data.md)):
                val = getattr(data.md, field.name)
                widget = self.dynamic_layout.itemAtPosition(i, 1).widget()
                widget.setText(str(val))

    def add_to_queue(self):
        self.add_to_queue_signal.emit(
            self.scan_name_input.text(),
            MaiaFlyDefinition(
                **{
                    "ystart": float(self.start_y_input.text()),
                    "ystop": float(self.stop_y_input.text()),
                    "ypitch": int(self.step_size_input.text()),
                    "xstart": float(self.stop_x_input.text()),
                    "xstop": float(self.stop_y_input.text()),
                    "xpitch": int(self.step_size_input.text()),
                    "dwell": float(self.dwell_input.text()),
                    "md": None,
                }
            ),
        )


class MicroscopeViewWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.widget_layout = QtWidgets.QGridLayout()
        label = QtWidgets.QLabel("Microscope View widget")
        self.widget_layout.addWidget(label)
        self.setLayout(self.widget_layout)


class DetectorImageWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.widget_layout = QtWidgets.QGridLayout()
        label = QtWidgets.QLabel("Detector Image widget")
        self.widget_layout.addWidget(label)
        self.setLayout(self.widget_layout)


class ScanControlWidget(QtWidgets.QGroupBox):
    def __init__(self):
        super().__init__()
        self.setLayout(QtWidgets.QGridLayout())
        self.queue_widget = CollectionQueueWidget()
        self.layout().addWidget(self.queue_widget, 0, 0)
        self.re_controls = RunEngineControls(self, motors=None)
        self.layout().addWidget(self.re_controls.widget, 1, 0)
        self._setup_shutter_button()
        self.setTitle("Scan Queue")

    def plan(self):
        yield from bps.sleep(1)
        yield from bps.sleep(1)
        yield from bps.sleep(1)
        yield from bps.sleep(1)

    def _setup_shutter_button(self):
        if shutter.status.get() == "Open":
            shutter_button_label = "Close Shutter"
        else:
            shutter_button_label = "Open Shutter"

        self.shutter_button = QtWidgets.QPushButton(shutter_button_label)
        self.shutter_button.clicked.connect(self.toggle_shutter)
        self.layout().addWidget(self.shutter_button, 2, 0)

    def toggle_shutter(self):
        if shutter.status.get() == "Open":
            yield from bps.mv(shutter, "Close")
            self.shutter_button.setText("Open Shutter")
        else:
            yield from bps.mv(shutter, "Open")
            self.shutter_button.setText("Close Shutter")


class MAIAGUI(QtWidgets.QMainWindow):
    led_color_change_signal = QtCore.Signal(int, LEDState)

    def __init__(self, parent=None, filter_obj=None) -> None:
        super(MAIAGUI, self).__init__(parent)
        self.setWindowTitle("MAIA data acquisition")
        self.main_widget = QtWidgets.QWidget()
        self.setCentralWidget(self.main_widget)
        self._create_menu_bar()
        self._create_layout()
        self.led_color_change_signal.connect(self.change_led_color)

    def _create_menu_bar(self):
        menuBar = self.menuBar()
        # self.setMenuBar(menuBar)
        # Creating menus using a QMenu object
        fileMenu = QtWidgets.QMenu("&File", self)
        menuBar.addMenu(fileMenu)
        # Adding actions to the File menu
        openAction = QtWidgets.QAction("&Open Excel Plan", self)
        fileMenu.addAction(openAction)
        openAction.triggered.connect(self.import_excel_plan)

        exitAction = QtWidgets.QAction("&Exit", self)
        exitAction.triggered.connect(self.close)  # Connect to close the application
        fileMenu.addAction(exitAction)

    def change_led_color(self, position, color: LEDState):
        print(f"Changing LED color! {color}")

    def _create_layout(self):
        self.widget_layout = QtWidgets.QGridLayout()
        self._add_widgets()
        self.main_widget.setLayout(self.widget_layout)

    def _add_widgets(self):
        # Adding import button
        self.scan_control_widget = ScanControlWidget()
        self.widget_layout.addWidget(self.scan_control_widget, 1, 0, 2, 1)

        self.sample_control_widget = SampleControlWidget()
        self.widget_layout.addWidget(self.sample_control_widget, 1, 1)

        self.microscope_view_widget = MicroscopeViewWidget()
        self.widget_layout.addWidget(self.microscope_view_widget, 1, 2)

        self.scan_setup_widget = ScanSetupWidget()
        self.widget_layout.addWidget(self.scan_setup_widget, 2, 1)

        self.detector_image_widget = DetectorImageWidget()
        self.widget_layout.addWidget(self.detector_image_widget, 2, 2)

        # Wiring up signals
        self.sample_control_widget.saved_positions_list.queue_updated.connect(
            self.scan_setup_widget.update_combo_boxes
        )
        self.scan_setup_widget.add_to_queue_signal.connect(
            self.scan_control_widget.queue_widget.add_item
        )
        self.scan_control_widget.queue_widget.selected_item_data_signal.connect(
            self.scan_setup_widget.fill_inputs_from_definition
        )

    def check_toggled(self, checkbox, check_state):
        if check_state == QtCore.Qt.CheckState.Checked:
            led_state = LEDState.QUEUED
        elif check_state == QtCore.Qt.CheckState.Unchecked:
            led_state = LEDState.NOT_QUEUED

    def import_excel_plan(self):
        dialog = QtWidgets.QFileDialog()
        filename, _ = dialog.getOpenFileName(
            self, "Import Plan", filter="Excel (*.xlsx)"
        )
        if filename:
            df = pd.read_excel(filename)
            expected_columns = [
                "name",
                "serial",
                "info",
                "xstart",
                "xstop",
                "ystart",
                "ystop",
                "pitch",
                "dwell",
                "type",
                "owner",
            ]
            missing_columns = set(expected_columns) - set(df.columns)
            if missing_columns:
                self.show_error_dialog(
                    f"Columns missing from imported excel: {','.join(list(missing_columns))}"
                )
                return
            for i, row in df.iterrows():
                md = SampleMetadata(
                    info=str(row["info"]),
                    owner=str("owner"),
                    serial=str(row["serial"]),
                    type=str(row["type"]),
                )
                collection_data = MaiaFlyDefinition(
                    ystart=row["ystart"],
                    ystop=row["ystop"],
                    ypitch=row["pitch"],
                    xstart=row["xstart"],
                    xstop=row["xstop"],
                    xpitch=row["pitch"],
                    dwell=row["dwell"],
                    name=str(row["name"]),
                    md=md,
                )
                self.scan_control_widget.queue_widget.add_item(
                    row["name"], collection_data
                )

    def show_error_dialog(self, message):
        dlg = QtWidgets.QMessageBox(self)
        dlg.setWindowTitle("Error")
        dlg.setText(message)
        dlg.exec()


if __name__ == "__main__":
    app = QtWidgets.QApplication([])

    main = MAIAGUI()
    main.show()

    app.exec_()
