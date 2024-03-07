import os
import shutil
import sys
import csv
import psutil
import time
import atexit
import traceback
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from PyQt5.QtCore import QThread, pyqtSignal, QLockFile, Qt, QTimer
from PyQt5.QtWidgets import QApplication, QCheckBox, QMainWindow, QLabel, QPushButton, QComboBox, QLineEdit, QFileDialog, QMessageBox, QTextEdit, QSystemTrayIcon, QMenu, QAction, QListWidget, QListWidgetItem
from resources import *
from PyQt5.QtGui import QIcon, QTextCursor, QFont

# Define a global variable for slow_mode
global_slow_mode = True

class SingleInstanceApp(QApplication):
    def __init__(self, argv, main_window_class):
        super().__init__(argv)
        self.main_window_class = main_window_class
        
        # Set the default path for QLockFile
        #lockfile_directory = os.path.join(os.getenv('LOCALAPPDATA'), 'AutoDelete')
        local_appdata = os.path.join(os.path.expanduser("~"), "AppData", "Local")
        lockfile_directory = os.path.join(local_appdata, 'AutoDelete')

        if not os.path.exists(lockfile_directory):
            os.makedirs(lockfile_directory)
        
        lockfile_path = os.path.join(lockfile_directory, 'your_app_lockfile.lock')
        self.lockfile = QLockFile(lockfile_path)
        
        if not self.lockfile.tryLock(100):
            # Another instance is running, activate the existing window
            self.activate_existing_window()
            sys.exit(0)

    def activate_existing_window(self):
        for widget in self.topLevelWidgets():
            if isinstance(widget, self.main_window_class):
                widget.showNormal()
                widget.activateWindow()
                widget.raise_()
                break

class MonitoringThread(QThread):
    status_signal = pyqtSignal(str)
    log_signal = pyqtSignal(str)
    log_batch_signal = pyqtSignal(list)  # Define the log_batch_signal
    countdown_signal = pyqtSignal(str)

    def __init__(self, target_list, monitoring_interval, max_workers=8):
        super().__init__()
        self.deleted_file_count = 0
        self.target_list = target_list
        self.monitoring_interval = monitoring_interval
        self.max_workers = max_workers if max_workers > 0 else 4  # Set a default value of 4 if max_workers is 0 or negative
        self.monitoring = True
        self.slow_mode = True
        self.deleted_dirs = set()

    def run(self):
        if global_slow_mode:
            self.log_signal.emit("Slow Mode: On")
        else:
            self.log_signal.emit("Slow Mode: Off")
            
        while self.monitoring:
                
            for target in self.target_list:
                hdd_path, directory_to_clean, target_space_gb, target_period_days = target

                print(f"Checking target: {directory_to_clean}")
                self.status_signal.emit(f"Checking target: {directory_to_clean}")

                if target_period_days is not None:
                    print("period")
                    self.delete_files_by_period(directory_to_clean, target_period_days)

                if target_space_gb is not None and hdd_path is not None:
                    print("size")
                    self.delete_files_by_size(hdd_path, target_space_gb, directory_to_clean)

            self.enter_interval_and_update_status()
            print(f"End of run: {time.time()}")

    
    def delete_files_by_size(self, hdd_path, target_space_gb, directory_to_clean):
        if self.monitoring == True:
            hdd_space_remaining = self.get_hdd_space_remaining(hdd_path)
            hdd_space_remaining_gb = hdd_space_remaining / (1024 ** 3)
            calculated_size = target_space_gb - hdd_space_remaining_gb
            directory = directory_to_clean
            self.log_signal.emit(f"Target path: {directory_to_clean}")
            self.log_signal.emit(f"{hdd_path} Drive's target size: {target_space_gb:.2f} GB")
            self.log_signal.emit(f"{hdd_path} Drive's remaining size: {hdd_space_remaining_gb:.2f} GB")
            if hdd_space_remaining < target_space_gb * (1024 ** 3):  # Convert target_space_gb to bytes
                self.log_signal.emit(f"Total Deleting files size: {calculated_size:.2f} GB.")
                self.delete_files_until_target_size(hdd_path, target_space_gb * (1024 ** 3), directory_to_clean)
                self.delete_empty_folders(directory)
        else:
            self.status_signal.emit(f"Stopped")

    def get_hdd_space_remaining(self, hdd_path):
        print("get hdd remaining size")
        hdd_stats = psutil.disk_usage(hdd_path)
        hdd_space_remaining = hdd_stats.free
        print(f"{hdd_space_remaining}")
        return hdd_space_remaining

    def delete_files_until_target_size(self, hdd_path, target_size_bytes, directory_to_clean):
        # Get files and their sizes and modification times
        file_data = []
        for file_path in self.get_files_to_delete_by_size(directory_to_clean):
            try:
                file_size = os.path.getsize(file_path)
                file_mtime = os.path.getmtime(file_path)
                file_data.append((file_path, file_size, file_mtime))
            except Exception as e:
                self.status_signal.emit(f"Error reading file data: {e}")
                self.log_signal.emit(f"Error: {e}")

        # Sort by modification time (oldest first)
        file_data.sort(key=lambda x: x[2])

        space_freed = 0
        hdd_space_remaining = self.get_hdd_space_remaining(hdd_path)
        for file_path, file_size, _ in file_data:
            if hdd_space_remaining >= target_size_bytes:
                break

            deleted_size = self.delete_file(file_path)
            hdd_space_remaining += deleted_size
            
    def delete_files_batch(self, files_to_delete):
        for file_path in files_to_delete:
            try:
                self.delete_file(file_path)
            except Exception as e:
                self.status_signal.emit(f"Error deleting file: {e}")
                self.log_signal.emit(f"Error: {e}")

    
    def delete_file(self, file_path):
        global global_slow_mode
        if self.monitoring == True:
            try:
                file_name = os.path.basename(file_path)
                directory = os.path.dirname(file_path)
                self.status_signal.emit(f"Deleting {file_name}")
                self.log_signal.emit(f"Deleting {file_name}")

                file_size = 0
                if os.path.isfile(file_path):
                    file_size = os.path.getsize(file_path)
                    os.remove(file_path)
                    self.deleted_dirs.add(directory)  # Add the directory to the set
                    if global_slow_mode:
                        time.sleep(0.02)

                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
                    if global_slow_mode:
                        time.sleep(0.02)

                # Update the space after deletion
                return file_size

            except Exception as e:
                self.status_signal.emit(f"Error deleting file or directory: {e}")
                self.log_signal.emit(f"Error: {e}")

            # Check only directories where files have been deleted
            for directory in self.deleted_dirs:
                self.delete_empty_folders(directory)
        else:
            self.status_signal.emit(f"Stopped")

        self.deleted_dirs.clear()

        return 0

    def get_files_to_delete_by_size(self, directory):
        current_time = time.time()
        files_to_delete = []
        
        if self.monitoring == True:
            for root, _, files in os.walk(directory):
                for file in files:
                    file_path = os.path.join(root, file)
                    files_to_delete.append(file_path)
        else:
            self.status_signal.emit(f"Stopped")

        return files_to_delete

    def delete_files_by_period(self, directory, target_period_days):
        current_time = time.time()
        deleted_files = []
        self.log_signal.emit(f"Target Path: {directory}.")
        self.log_signal.emit(f"Delete all the files older than {target_period_days} day(s).")
        if self.monitoring == True:
            if os.path.isfile(directory):
                self.delete_files_in_file_condition(directory, current_time, target_period_days)
            elif os.path.isdir(directory):
                print("period 5")
                self.delete_files_in_directory_condition(directory, current_time, target_period_days)
            self.delete_empty_folders(directory)
        else:
            self.status_signal.emit(f"Stopped")

        return deleted_files
            
    def delete_files_in_file_condition(self, directory, file_path, current_time, target_period_days):
        global global_slow_mode

        if not self.monitoring:
            self.status_signal.emit(f"Stopped")
            return

        try:
            file_last_modified_time = os.path.getmtime(file_path)
            time_difference = current_time - file_last_modified_time
            if time_difference >= target_period_days * 24 * 60 * 60:
                if os.path.isfile(file_path):
                    file_name = os.path.basename(file_path)
                    self.status_signal.emit(f"Deleting {file_name}")
                    self.log_signal.emit(f"Deleting {file_name}")
                    os.remove(file_path)
                    self.delete_empty_folders(directory)
                    if global_slow_mode:
                        time.sleep(0.02)
        except Exception as e:
            self.status_signal.emit(f"Error deleting file: {e}")
            self.log_signal.emit(f"Error: {e}")


            
    def delete_files_in_directory_condition(self, directory, current_time, target_period_days):
        for root, dirs, files in os.walk(directory):
            for file in files:
                if not self.monitoring:
                    return

                file_path = os.path.join(root, file)
                try:
                    file_last_modified_time = os.path.getmtime(file_path)
                    time_difference = current_time - file_last_modified_time
                    if time_difference >= target_period_days * 24 * 60 * 60:
                        file_name = os.path.basename(file_path)
                        self.status_signal.emit(f"Deleting {file_name}")
                        self.log_signal.emit(f"Deleting {file_name}")
                        os.remove(file_path)
                        
                        if global_slow_mode:
                            time.sleep(0.02)
                except Exception as e:
                    self.status_signal.emit(f"Error deleting files: {e}")
                    self.log_signal.emit(f"Error: {e}")

            # Immediately delete empty folders in the current root after all files have been deleted
            for dir in dirs:
                dir_path = os.path.join(root, dir)
                if not os.listdir(dir_path):
                    self.status_signal.emit(f"Deleting Empty Directory: {dir_path}")
                    os.rmdir(dir_path)


                
    def enter_interval_and_update_status(self):
        remaining_seconds = self.monitoring_interval * 60  # Convert minutes to seconds
        print(f"{remaining_seconds}")
        self.log_signal.emit(f"Waiting for next cycle: {self.monitoring_interval} min(s)")
        while remaining_seconds > 0:
            minutes = remaining_seconds // 60
            seconds = remaining_seconds % 60
            countdown_text = f"Next monitoring will start in...{minutes}min {seconds}sec"
            self.countdown_signal.emit(countdown_text)
            QThread.sleep(1)  # Sleep for 1 second
            remaining_seconds -= 1
            self.status_signal.emit(f"Next Monitoring will start in {minutes}min {seconds}sec")
        
        self.status_signal.emit("Monitoring")  # Emit "Monitoring" status signal
        self.log_signal.emit("Next monitoring cycle starting.")
        
    def delete_empty_folders(self, directory):
        if not self.monitoring:
            return

        def try_delete_empty_folder(folder_path):
            try:
                if not os.listdir(folder_path):  # Check if folder is empty
                    self.status_signal.emit(f"Deleting Empty Directory: {folder_path}")
                    os.rmdir(folder_path)
            except Exception as e:
                self.status_signal.emit(f"Error deleting folder: {e}")
                self.log_signal.emit(f"Error: {e}")

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            for root, dirs, _ in os.walk(directory, topdown=False):
                folder_paths = [os.path.join(root, d) for d in dirs]
                executor.map(try_delete_empty_folder, folder_paths)
                        
    def set_max_workers(self, max_workers):
        self.max_workers = max_workers


class AutoScrollTextEdit(QTextEdit):
    def scrollContentsBy(self, dx, dy):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.setTextCursor(cursor)


class DiskMonitorApp(QMainWindow):
    
    def create_directories_if_not_exist(self):
        directories = [
            "D:/Program/RVS/Autodelete",
            "D:/Program/RVS/Autodelete/Log"
            # Add more directories as needed
        ]
        for directory in directories:
            os.makedirs(directory, exist_ok=True)
            
    def update_countdown(self, countdown_text):
        self.countdown_label.setText(countdown_text)  # Update the label with the countdown text

    def create_csv_file_if_not_exist(self):
        csv_path = "D:/Program/RVS/Autodelete/targetlist.csv"
        if not os.path.exists(csv_path):
            fieldnames = ["HDD", "Directory", "Space (GB)", "Period (Days)"]
            # Setting list for 404L 2nd.
            '''initial_content = [
                ["E:", "E:/POCB/HEX", "300", "25"],
                ["E:", "E:/Radiant Vision Systems Data/TrueTest/UserData/AutoGenerated/DB1", "300", "7"],
                ["E:", "E:/Radiant Vision Systems Data/TrueTest/UserData/AutoGenerated/DB2", "300", "7"],
                ["D:", "D:/Program/RVS/InputLog", "", "7"],
                ["D:", "D:/Program/RVS/TrueTestWatcherLog", "", "30"],
                ["D:", "D:/Program/RVS/UploadQueue", "", "30"],
                ["D:", "D:/Program/RVS/Autodelete/Log", "", "7"]
            ]'''
            # Setting list for 501L 1st.
            initial_content = [
                ["E:", "E:/POCB/HEX", "500", "50"],
                ["E:", "E:/Radiant Vision Systems Data/TrueTest/UserData/AutoGenerated/DB1", "500", "7"],
                ["E:", "E:/Radiant Vision Systems Data/TrueTest/UserData/AutoGenerated/DB2", "500", "7"],
                ["D:", "D:/Radiant Vision Systems Data/TrueTest/UserData/AutoGenerated/DB1", "30", "3"],
                ["D:", "D:/Radiant Vision Systems Data/TrueTest/UserData/AutoGenerated/DB2", "30", "3"],
                ["D:", "D:/Program/RVS/InputLog", "", "3"],
                ["D:", "D:/Program/RVS/TrueTestWatcherLog", "", "14"],
                ["D:", "D:/Program/RVS/UploadQueue", "", "14"],
                ["D:", "D:/Program/RVS/Autodelete/Log", "", "7"]
            ]
            with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
                csv_writer = csv.writer(csvfile)
                csv_writer.writerow(fieldnames)
                csv_writer.writerows(initial_content)
            
    def load_conditions_from_csv(self):
        csv_path = "D:/Program/RVS/Autodelete/targetlist.csv"
        
        self.target_list.clear()
        self.target_list_widget.clear()
        
        rows = []
        if os.path.exists(csv_path):
            with open(csv_path, "r") as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    hdd_path = row["HDD"]
                    directory_to_clean = row["Directory"]
                    
                    target_space_gb = row["Space (GB)"]
                    if target_space_gb:
                        target_space_gb = float(target_space_gb)
                    else:
                        target_space_gb = None
                    
                    target_period_days = row["Period (Days)"]
                    if target_period_days:
                        target_period_days = int(target_period_days)
                    else:
                        target_period_days = None
                    
                    self.target_list.append((hdd_path, directory_to_clean, target_space_gb, target_period_days))
                    condition_text = f"HDD: {hdd_path} | Directory: {directory_to_clean}"
                    if target_space_gb is not None:
                        condition_text += f" | Space: {target_space_gb} GB"
                    if target_period_days is not None:
                        condition_text += f" | Period: {target_period_days} Days"
                    list_item = QListWidgetItem(condition_text)
                    self.target_list_widget.addItem(list_item)

    def __init__(self):
        super().__init__()
        
        self.inactivity_timer = QTimer()  # Move this line up here
        self.inactivity_timer.timeout.connect(self.hide_to_tray)
        self.inactivity_timer.start(30000)

        self.setWindowTitle("Autodelete v1.0.0.5_Modified Version")
        self.setGeometry(100, 100, 820, 560)
        
        atexit.register(self.save_log)
        
        self.monitoring_thread = MonitoringThread([], 0, 0)
        
        self.create_directories_if_not_exist()  # Check and create directories
        self.create_csv_file_if_not_exist()  # Check and create CSV file

        self.target_list = []

        self.hdd_label = QLabel("Select HDD:", self)
        self.hdd_label.setGeometry(20, 20, 100, 20)

        self.hdd_choice = QComboBox(self)
        self.hdd_choice.setGeometry(120, 20, 200, 20)
        self.update_hdd_list()

        self.directory_label = QLabel("Directory to Clean:", self)
        self.directory_label.setGeometry(20, 60, 120, 20)

        self.directory_entry = QLineEdit(self)
        self.directory_entry.setGeometry(140, 60, 150, 20)

        self.choose_directory_button = QPushButton("Choose Directory", self)
        self.choose_directory_button.setGeometry(300, 60, 100, 20)
        self.choose_directory_button.clicked.connect(self.choose_directory)

        self.target_space_label = QLabel("Target Space (GB):", self)
        self.target_space_label.setGeometry(20, 100, 120, 20)

        self.target_space_entry = QLineEdit(self)
        self.target_space_entry.setGeometry(140, 100, 150, 20)

        self.target_period_label = QLabel("Target Period (Days):", self)
        self.target_period_label.setGeometry(20, 140, 120, 20)

        self.target_period_entry = QLineEdit(self)
        self.target_period_entry.setGeometry(140, 140, 150, 20)

        self.add_condition_button = QPushButton("Add Condition", self)
        self.add_condition_button.setGeometry(20, 180, 80, 30)
        self.add_condition_button.clicked.connect(self.add_condition)

        self.delete_selected_button = QPushButton("Delete Condition", self)
        self.delete_selected_button.setGeometry(105, 180, 90, 30)
        self.delete_selected_button.clicked.connect(self.delete_selected_conditions)
        
        self.refresh_condition_button = QPushButton("Refresh Condition", self)
        self.refresh_condition_button.setGeometry(200, 180, 100, 30)
        self.refresh_condition_button.clicked.connect(self.refresh_condition)

        self.start_button = QPushButton("Start", self)
        self.start_button.setGeometry(305, 180, 55, 30)
        self.start_button.clicked.connect(self.start_monitoring)

        self.stop_button = QPushButton("Stop", self)
        self.stop_button.setGeometry(365, 180, 55, 30)
        self.stop_button.clicked.connect(self.stop_monitoring)

        self.target_list_widget = QListWidget(self)
        self.target_list_widget.setGeometry(20, 220, 400, 300)

        self.status_label = QLabel("Status: Not Monitoring", self)
        self.status_label.setGeometry(450, 20, 340, 20)
        
        self.countdown_label = QLabel("Next monitoring will start in...0min 0sec", self)
        #self.countdown_label.setGeometry(450, 20, 200, 20)
        self.countdown_label.setGeometry(450, 170, 340, 20)

        self.log_label = QLabel("Log:", self)
        self.log_label.setGeometry(450, 130, 50, 20)

        self.log_text_edit = QTextEdit(self)
        self.log_text_edit.setGeometry(450, 150, 340, 370)
        self.log_text_edit.setReadOnly(True)
        
        self.monitoring_interval_label = QLabel("Monitoring Interval (Mins):", self)
        self.monitoring_interval_label.setGeometry(450, 50, 170, 20)
        
        self.monitoring_interval_entry = QLineEdit(self)
        self.monitoring_interval_entry.setGeometry(630, 50, 100, 20)
        self.monitoring_interval_entry.setText("30")

        self.slowmode_checkbox = QCheckBox("Slow Mode", self)
        self.slowmode_checkbox.setGeometry(450, 80, 170, 20)
        self.slowmode_checkbox.setChecked(True)
        self.slowmode_checkbox.stateChanged.connect(self.update_slow_mode)
        
        self.autohide_checkbox = QCheckBox("Auto Hide Mode", self)
        self.autohide_checkbox.setGeometry(450, 100, 170, 20)
        self.autohide_checkbox.setChecked(True)
        self.autohide_checkbox.stateChanged.connect(self.update_autohide)
        
        # Clear Log Button
        self.clear_log_button = QPushButton("Clear Log", self)
        self.clear_log_button.setGeometry(660, 530, 60, 25)
        self.clear_log_button.clicked.connect(self.clear_log)
        
        # Save Log Button
        self.save_log_button = QPushButton("Save Log", self)
        self.save_log_button.setGeometry(730, 530, 60, 25)
        self.save_log_button.clicked.connect(self.save_log)

        self.system_tray_icon = QSystemTrayIcon(self)
        self.system_tray_icon.setIcon(QIcon(':/trash.ico'))  # Set the path to your tray icon
        self.system_tray_icon.setToolTip("Autodelete")
        self.system_tray_icon.activated.connect(self.toggle_visibility)

        self.tray_menu = QMenu()
        show_action = QAction("Show", self)
        quit_action = QAction("Quit", self)
        show_action.triggered.connect(self.show_window)
        quit_action.triggered.connect(self.quit_app)
        self.tray_menu.addAction(show_action)
        self.tray_menu.addAction(quit_action)
        self.system_tray_icon.setContextMenu(self.tray_menu)

        self.load_conditions_from_csv()

        self.hide()
        
        self.current_status = "Stopped"
        
        self.monitoring_threads = []
        
        self.monitoring_thread = MonitoringThread([], 0, 0)
        self.monitoring_thread.status_signal.connect(self.update_status)  # Connect status signal
        self.monitoring_thread.log_signal.connect(self.update_log)  # Connect log signal
        #self.monitoring_thread.dot_signal.connect(self.update_status)  # Connect dot signal for status updates
        self.monitoring_thread.countdown_signal.connect(self.update_countdown)  # Connect countdown signal
        
        font = QFont()
        font.setBold(True)

        # Set the font for the QLabel
        self.status_label.setFont(font)
        
        monitoring_interval = int(self.monitoring_interval_entry.text())
        self.update_log("START MONITORING")
        self.monitoring_thread = MonitoringThread(self.target_list, monitoring_interval, 0)  # Use max_workers = 0 initially

        self.monitoring_thread.status_signal.connect(self.update_status)
        self.monitoring_thread.log_signal.connect(self.update_log)
        self.monitoring_thread.countdown_signal.connect(self.update_countdown)

        # Start the monitoring thread
        self.monitoring_thread.start()
        self.update_status()
        
    def update_max_workers(self):
        try:
            self.max_workers = 8
        except ValueError:
            pass
        
    def update_autohide(self, state):
        if state == Qt.Checked:
            self.inactivity_timer.start(30000)
        else:
            self.inactivity_timer.stop()
        
    def keyPressEvent(self, event):
        if self.autohide_checkbox.isChecked():
            self.inactivity_timer.start(30000)
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if self.autohide_checkbox.isChecked():
            self.inactivity_timer.start(30000)
        super().mousePressEvent(event)
        
    def hide_to_tray(self):
        self.hide()
        self.system_tray_icon.show()
    
    def refresh_condition(self):
        # Code to reload the CSV file and update your conditions
        self.load_conditions_from_csv()
        self.update_log("Conditions refreshed")
    
    def update_slow_mode(self, state):
        global global_slow_mode  # Declare that you're using the global variable
        print("Slow mode state changed")
        if state == Qt.Checked:
            global_slow_mode = True  # Update the global variable
            print("Changed to True")
        else:
            global_slow_mode = False  # Update the global variable
            print("Changed to False")
            
    def update_status(self, status=""):
        print(f"Received status signal: {status}")
        if "Deleting" in status:
            formatted_status = f"<span style='color: blue; font-weight: bold;'>{status}</span>"
        if "Next Monitoring" in status:
            formatted_status = f"<span style='color: red; font-weight: bold;'>{status}</span>"
        else:
            formatted_status = status

        self.current_status = formatted_status  # Update the current status variable
        self.status_label.setText(f"Status: {self.current_status}")
        
    def save_conditions_to_csv(self):
        csv_path = "D:/Program/RVS/Autodelete/targetlist.csv"
        fieldnames = ["HDD", "Directory", "Space (GB)", "Period (Days)"]

        with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
            csv_writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            csv_writer.writeheader()

            for condition in self.target_list:
                hdd_path, directory_to_clean, target_space_gb, target_period_days = condition
                csv_writer.writerow({
                    "HDD": hdd_path,
                    "Directory": directory_to_clean,
                    "Space (GB)": target_space_gb,
                    "Period (Days)": target_period_days
                })

    def add_condition(self):
        hdd_path = self.hdd_choice.currentText()
        directory_to_clean = self.directory_entry.text()
        target_space_gb_text = self.target_space_entry.text()
        target_period_days_text = self.target_period_entry.text()

        if target_space_gb_text:
            target_space_gb = float(target_space_gb_text)
        else:
            target_space_gb = None

        if target_period_days_text:
            target_period_days = int(target_period_days_text)
        else:
            target_period_days = None

        condition = (hdd_path, directory_to_clean, target_space_gb, target_period_days)
        self.target_list.append(condition)

        self.add_condition_item_to_list_widget(condition)

        self.hdd_choice.setCurrentIndex(0)
        self.directory_entry.clear()
        self.target_space_entry.clear()
        self.target_period_entry.clear()

        self.save_conditions_to_csv()  # Save the updated conditions to the CSV file

    def add_condition_item_to_list_widget(self, condition):
        hdd_path, directory_to_clean, target_space_gb, target_period_days = condition

        item_text = f"HDD: {hdd_path} | Directory: {directory_to_clean}"
        if target_space_gb is not None:
            item_text += f" | Space: {target_space_gb} GB"
        if target_period_days is not None:
            item_text += f" | Period: {target_period_days} Days"

        list_item = QListWidgetItem(item_text)
        self.target_list_widget.addItem(list_item)
        
    def delete_condition(self, list_item):
        index = self.target_list_widget.indexFromItem(list_item).row()
        if index >= 0 and index < len(self.target_list):
            del self.target_list[index]
            self.target_list_widget.takeItem(index)
            self.save_conditions_to_csv()
            
    def delete_selected_conditions(self):
        selected_items = self.target_list_widget.selectedItems()
        if selected_items:
            for selected_item in selected_items:
                index = self.target_list_widget.indexFromItem(selected_item).row()
                if index >= 0 and index < len(self.target_list):
                    del self.target_list[index]
                    self.target_list_widget.takeItem(index)
                    self.save_conditions_to_csv()  # Save updated conditions to the CSV file
        else:
            QMessageBox.warning(self, "No Selection", "No conditions selected for deletion.")

    def load_target_list(self):
        try:
            with open("D:/Program/RVS/Autodelete/targetlist.csv", "r", newline="") as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    hdd_path = row["HDD"]
                    directory_to_clean = row["Directory"]
                    target_space_gb = float(row["Space (GB)"])
                    target_period_days = int(row["Period (Days)"])
                    self.target_list.append((hdd_path, directory_to_clean, target_space_gb, target_period_days))
                    item_text = f"HDD: {hdd_path} | Directory: {directory_to_clean} | Space: {target_space_gb} GB | Period: {target_period_days} Days"
                    list_item = QListWidgetItem(item_text)
                    self.target_list_widget.addItem(list_item)
        except FileNotFoundError:
            pass

    def save_target_list(self):
        with open("D:/Program/RVS/Autodelete/targetlist.csv", "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            for target in self.target_list:
                writer.writerow(target)

    def closeEvent(self, event):
        self.hide()
        self.system_tray_icon.show()
        event.ignore()
    
    def show_window(self):
        self.showNormal()
        self.system_tray_icon.hide()

    def hide_window(self):
        self.hide()
        self.system_tray_icon.show()

    def toggle_visibility(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_window()

    def update_hdd_list(self):
        partitions = [partition.device for partition in psutil.disk_partitions()]
        self.hdd_choice.clear()
        self.hdd_choice.addItems(partitions)

    def choose_directory(self):
        selected_directory = QFileDialog.getExistingDirectory(self, "Choose Directory")
        self.directory_entry.setText(selected_directory)

    def start_monitoring(self):
        self.update_max_workers()  # Update max_workers based on user input
        if self.monitoring_thread is not None and self.monitoring_thread.isRunning():
            self.monitoring_thread.terminate()
            self.monitoring_thread.wait(5000)
            self.update_status("Stopped")  # Set the status to "Stopped"
            self.update_log("Stopped monitoring for all conditions.")
        else:
            # Get the monitoring interval from the user's input
            monitoring_interval = int(self.monitoring_interval_entry.text())

            # Create a new instance of MonitoringThread with max_workers
            self.update_log("START MONITORING")
            self.monitoring_thread = MonitoringThread(self.target_list, monitoring_interval, 0)  # Use max_workers = 0 initially

            # Connect signals from the monitoring thread
            self.monitoring_thread.status_signal.connect(self.update_status)  # Connect status signal
            self.monitoring_thread.log_signal.connect(self.update_log)  # Connect log signal
            self.monitoring_thread.countdown_signal.connect(self.update_countdown)  # Connect countdown signal

            # Start the monitoring thread
            self.monitoring_thread.start()
            self.update_status()

    def stop_monitoring(self):
        if self.monitoring_thread is not None and self.monitoring_thread.isRunning():
            self.monitoring_thread.monitoring = False
            self.monitoring_thread.terminate()
            self.monitoring_thread.wait(5000)
            self.update_status("Stopped")  # Set the status to "Stopped"
            self.update_log("Stopped monitoring for all conditions.")
        else:
            self.update_log("No monitoring thread to stop.")
        
    def update_log(self, log_message, save_log=True):
        timestamped_log_message = f"[{datetime.now()}] {log_message}"
        current_log = self.log_text_edit.toPlainText()
        updated_log = current_log + timestamped_log_message + '\n'
        
        # Count the number of lines
        line_count = updated_log.count('\n')
        
        if line_count > 200 and save_log:
            self.save_log()
            self.clear_log()
            updated_log = timestamped_log_message + '\n'  # Start over with the latest log message

        self.log_text_edit.setPlainText(updated_log)

        # Scroll to the bottom
        cursor = self.log_text_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_text_edit.setTextCursor(cursor)
        self.log_text_edit.ensureCursorVisible()
        
    log_signal = pyqtSignal(str)
        
    def quit_app(self):
        self.update_log(f"User Closed App with Tray Icon", save_log=False)
        self.system_tray_icon.hide()
        self.save_log()
        QApplication.quit()
        
    def clear_log(self):
        self.log_text_edit.clear()

    def save_log(self):
        log_content = self.log_text_edit.toPlainText()
        if log_content:
            timestamp = datetime.now().strftime("%Y%m%d")
            log_filename = f"AutodeleteLog_{timestamp}.txt"
            log_folder = os.path.join("D:/Program/RVS/Autodelete/Log")
            os.makedirs(log_folder, exist_ok=True)  # Create directory if it doesn't exist
            log_path = os.path.join(log_folder, log_filename)
            
            # Append to the log file or create it if it doesn't exist
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write(log_content)
            
            self.update_log(f"Log saved as: {log_path}", save_log=False)  # Avoid triggering save_log recursively
            
    def log_exception(self, e):
        error_message = str(e)
        traceback_message = traceback.format_exc()  # Get the full traceback
        
        # Create the full error message
        full_error_message = f"ERROR: {error_message}\n{traceback_message}"
        
        # Add the error to your log display
        self.update_log(full_error_message)

if __name__ == "__main__":
    app = SingleInstanceApp(sys.argv, DiskMonitorApp)
    app_icon = QIcon(":/trash.ico")
    app.setWindowIcon(app_icon)
    window = DiskMonitorApp()
    window.show()
    sys.exit(app.exec_())