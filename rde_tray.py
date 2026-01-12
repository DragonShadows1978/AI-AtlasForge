#!/usr/bin/env python3
"""
RDE Dashboard System Tray Indicator
Shows dashboard status in the system tray with quick actions.
"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AppIndicator3', '0.1')

from gi.repository import Gtk, AppIndicator3, GLib
import subprocess
import urllib.request
import json
import os

DASHBOARD_URL = "http://localhost:5000"
CHECK_INTERVAL = 10000  # 10 seconds

class RDETrayIndicator:
    def __init__(self):
        self.indicator = AppIndicator3.Indicator.new(
            "rde-dashboard",
            "network-server",
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title("RDE Dashboard")

        self.build_menu()
        self.update_status()

        # Check status periodically
        GLib.timeout_add(CHECK_INTERVAL, self.update_status)

    def build_menu(self):
        menu = Gtk.Menu()

        # Status item (updated dynamically)
        self.status_item = Gtk.MenuItem(label="Status: Checking...")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)

        menu.append(Gtk.SeparatorMenuItem())

        # Open Dashboard
        open_item = Gtk.MenuItem(label="Open Dashboard")
        open_item.connect("activate", self.open_dashboard)
        menu.append(open_item)

        # Open Terminal Dashboard
        terminal_item = Gtk.MenuItem(label="Open Terminal")
        terminal_item.connect("activate", self.open_terminal)
        menu.append(terminal_item)

        menu.append(Gtk.SeparatorMenuItem())

        # Restart Dashboard
        restart_item = Gtk.MenuItem(label="Restart Dashboard")
        restart_item.connect("activate", self.restart_dashboard)
        menu.append(restart_item)

        # Stop Dashboard
        stop_item = Gtk.MenuItem(label="Stop Dashboard")
        stop_item.connect("activate", self.stop_dashboard)
        menu.append(stop_item)

        menu.append(Gtk.SeparatorMenuItem())

        # Quit tray
        quit_item = Gtk.MenuItem(label="Quit Tray")
        quit_item.connect("activate", self.quit)
        menu.append(quit_item)

        menu.show_all()
        self.indicator.set_menu(menu)

    def update_status(self):
        try:
            req = urllib.request.Request(f"{DASHBOARD_URL}/api/status", timeout=3)
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read())
                stage = data.get("rd_stage", data.get("stage", "Unknown"))
                mode = data.get("mode", "Unknown")
                running = data.get("running", False)

                status_text = f"{mode} - {stage}"
                if running:
                    status_text += " (Running)"
                self.status_item.set_label(f"Status: {status_text}")
                self.indicator.set_icon("network-server")

        except Exception as e:
            self.status_item.set_label("Status: Offline")
            self.indicator.set_icon("network-offline")

        return True  # Continue timer

    def open_dashboard(self, _):
        subprocess.Popen(["xdg-open", DASHBOARD_URL])

    def open_terminal(self, _):
        subprocess.Popen(["xdg-open", "http://localhost:5002"])

    def restart_dashboard(self, _):
        subprocess.run(["systemctl", "--user", "restart", "rde-dashboard"])
        self.status_item.set_label("Status: Restarting...")

    def stop_dashboard(self, _):
        subprocess.run(["systemctl", "--user", "stop", "rde-dashboard"])
        self.status_item.set_label("Status: Stopped")

    def quit(self, _):
        Gtk.main_quit()


def main():
    indicator = RDETrayIndicator()
    Gtk.main()


if __name__ == "__main__":
    main()
