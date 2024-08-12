import copy
import queue
from enum import Enum

import bluesky.plan_stubs as bps
import pandas as pd
from bluesky.run_engine import RunEngine
from ophyd import Component as Cpt
from ophyd import Device, EpicsMotor, EpicsSignalRO
from qmicroscope import Microscope
from qtpy import QtCore, QtGui, QtWidgets

RE = RunEngine()
x = None
y = None


class MaiaStage(Device):
    x = Cpt(EpicsMotor, "{PI180:1-Ax:MaiaX}Mtr")
    y = Cpt(EpicsMotor, "{PI180:1-Ax:MaiaY}Mtr")
    z = Cpt(EpicsMotor, "{PI180:1-Ax:MaiaZ}Mtr")
    r = Cpt(EpicsMotor, "{SR50pp:1-Ax:MaiaR}Mtr")


M = MaiaStage("XF:04BMC-ES:2", name="M")


class LEDState(Enum):
    QUEUED = QtCore.Qt.blue
    COLLECTING = QtCore.Qt.red
    COMPLETE = QtCore.Qt.green
    NOT_QUEUED = QtCore.Qt.gray


class QueueItem:
    def __init__(self, label, data):
        self.label = label
        self.data = data


class QueueModel:
    def __init__(self):
        self.queue = []

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
            self.list_widget.addItem(str(item.label))


class SamplePositionQueueWidget(QueueWidget):
    def add_item(self, position_name, x, y, z):
        item = QueueItem(label=position_name, data=(x, y, z))
        self.model.add_item(item)
        self.update_list()


class CollectionQueueWidget(QueueWidget):
    def add_item(self, collection_name, *params):
        item = QueueItem(label=collection_name, data=params)
        self.model.add_item(item)
        self.update_list()


class RunEngineControls:
    def __init__(self, RE, GUI, motors):

        self.RE = RE
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

        self.RE.state_hook = self.handle_state_change
        self.handle_state_change(self.RE.state, None)

    def run(self):
        if self.RE.state == "idle":
            self.RE(self.GUI.plan())
        else:
            self.RE.resume()

    def pause(self):
        if self.RE.state == "running":
            self.RE.request_pause()
        elif self.RE.state == "paused":
            self.RE.stop()

    def handle_state_change(self, new, old):
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


class SampleControlWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        # label = QtWidgets.QLabel("Sample control widget")

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
        layout = QtWidgets.QVBoxLayout()
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

        readback_values_layout = QtWidgets.QGridLayout()
        x_label = QtWidgets.QLabel("X Pos:")
        self.x_rb_label = QtWidgets.QLabel("0")
        y_label = QtWidgets.QLabel("Y Pos:")
        self.y_rb_label = QtWidgets.QLabel("0")
        z_label = QtWidgets.QLabel("Z Pos:")
        self.z_rb_label = QtWidgets.QLabel("0")
        readback_values_layout.addWidget(x_label, 0, 0)
        readback_values_layout.addWidget(self.x_rb_label, 0, 1)
        readback_values_layout.addWidget(y_label, 1, 0)
        readback_values_layout.addWidget(self.y_rb_label, 1, 1)
        readback_values_layout.addWidget(z_label, 2, 0)
        readback_values_layout.addWidget(self.z_rb_label, 2, 1)

        M.x.subscribe(lambda value, **kwargs: self.update_label("x", value))
        M.y.subscribe(lambda value, **kwargs: self.update_label("y", value))
        M.z.subscribe(lambda value, **kwargs: self.update_label("z", value))

        self.position_save_text_box = QtWidgets.QLineEdit()
        self.position_save_button = QtWidgets.QPushButton("Save Position")

        readback_values_layout.addWidget(self.position_save_text_box, 3, 0, 1, 2)
        readback_values_layout.addWidget(self.position_save_button, 4, 0, 1, 2)

        self.saved_positions_list = SamplePositionQueueWidget()
        self.position_save_button.clicked.connect(self.save_motor_positions)
        readback_values_layout.addWidget(self.saved_positions_list, 5, 0, 1, 2)

        layout.addWidget(widget_label)
        layout.addLayout(nudge_buttons)
        layout.addLayout(readback_values_layout)

        self.setLayout(layout)

    def save_motor_positions(self):
        self.saved_positions_list.add_item(
            self.position_save_text_box.text(), M.x.read(), M.y.read(), M.z.read()
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


class ScanSetupWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.widget_layout = QtWidgets.QGridLayout()
        label = QtWidgets.QLabel("Scan Setup widget")
        self.widget_layout.addWidget(label)
        self.setLayout(self.widget_layout)


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


class MAIAGUI(QtWidgets.QMainWindow):
    led_color_change_signal = QtCore.Signal(int, LEDState)

    def __init__(self, parent=None, filter_obj=None) -> None:
        super(MAIAGUI, self).__init__(parent)
        self.setWindowTitle("MAIA data acquisition")
        self.main_widget = QtWidgets.QWidget()
        self.setCentralWidget(self.main_widget)

        self._create_layout()
        self.led_color_change_signal.connect(self.change_led_color)

    def change_led_color(self, position, color: LEDState):
        print(f"Changing LED color! {color}")

    def _create_layout(self):
        self.widget_layout = QtWidgets.QGridLayout()
        self._add_widgets()
        self.main_widget.setLayout(self.widget_layout)

    def _add_widgets(self):
        # Adding import button
        import_button = QtWidgets.QPushButton("Import Excel Plan")
        self.widget_layout.addWidget(import_button, 0, 0)
        import_button.clicked.connect(self.import_excel_plan)
        self.re_controls = RunEngineControls(RE, self, motors=[x, y])
        self.widget_layout.addWidget(self.re_controls.widget, 1, 0)

        self.sample_control_widget = SampleControlWidget()
        self.widget_layout.addWidget(self.sample_control_widget, 1, 1)

        self.microscope_view_widget = MicroscopeViewWidget()
        self.widget_layout.addWidget(self.microscope_view_widget, 1, 2)

        self.queue_widget = CollectionQueueWidget()
        self.widget_layout.addWidget(self.queue_widget, 2, 0)

        self.scan_setup_widget = ScanSetupWidget()
        self.widget_layout.addWidget(self.scan_setup_widget, 2, 1)

        self.detector_image_widget = DetectorImageWidget()
        self.widget_layout.addWidget(self.detector_image_widget, 2, 2)

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
