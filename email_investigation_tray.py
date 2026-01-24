#!/usr/bin/env python3
"""
Email Investigation Service - System Tray Indicator
Shows email daemon status and provides start/stop/restart controls.

Unique icon: mail-unread-symbolic for differentiation from AtlasForge tray
"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AppIndicator3', '0.1')

from gi.repository import Gtk, AppIndicator3, GLib
import subprocess
import urllib.request
import json
import os
import sys

# Configuration
# Using port 5001 (standalone email investigation service)
# Port 5000 is legacy and has been deactivated
EMAIL_SERVICE_URL = "http://localhost:5001"
CHECK_INTERVAL = 5000  # 5 seconds - faster for daemon status

# Icon states using standard email/mail icons for easy differentiation
ICON_CONNECTED = "mail-read"
ICON_DISCONNECTED = "mail-unread"
ICON_DAEMON_RUNNING = "mail-send"
ICON_ERROR = "mail-mark-important"
ICON_OFFLINE = "network-offline"


class EmailInvestigationTrayIndicator:
    """System tray indicator for Email Investigation Service."""

    def __init__(self):
        # Use unique app ID to coexist with atlasforge-dashboard indicator
        self.indicator = AppIndicator3.Indicator.new(
            "email-investigation-service",
            ICON_DISCONNECTED,
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Email Investigation Service")

        # State tracking
        self.daemon_running = False
        self.connected = False
        self.service_online = False
        self.last_error = None

        self.build_menu()
        self.update_status()

        # Check status periodically
        GLib.timeout_add(CHECK_INTERVAL, self.update_status)

    def build_menu(self):
        """Build the tray menu."""
        menu = Gtk.Menu()

        # Service status header
        self.service_status_item = Gtk.MenuItem(label="Service: Checking...")
        self.service_status_item.set_sensitive(False)
        menu.append(self.service_status_item)

        # Daemon status
        self.daemon_status_item = Gtk.MenuItem(label="Daemon: Checking...")
        self.daemon_status_item.set_sensitive(False)
        menu.append(self.daemon_status_item)

        # Connection status
        self.connection_status_item = Gtk.MenuItem(label="Email: Checking...")
        self.connection_status_item.set_sensitive(False)
        menu.append(self.connection_status_item)

        menu.append(Gtk.SeparatorMenuItem())

        # ===== DAEMON CONTROLS =====
        daemon_header = Gtk.MenuItem(label="--- Daemon Controls ---")
        daemon_header.set_sensitive(False)
        menu.append(daemon_header)

        # Start Daemon
        self.start_item = Gtk.MenuItem(label="Start Daemon")
        self.start_item.connect("activate", self.start_daemon)
        menu.append(self.start_item)

        # Stop Daemon
        self.stop_item = Gtk.MenuItem(label="Stop Daemon")
        self.stop_item.connect("activate", self.stop_daemon)
        menu.append(self.stop_item)

        # Restart Daemon
        restart_item = Gtk.MenuItem(label="Restart Daemon")
        restart_item.connect("activate", self.restart_daemon)
        menu.append(restart_item)

        menu.append(Gtk.SeparatorMenuItem())

        # ===== OPEN DASHBOARD =====
        # Opens the Email Monitor tab in the mini-mind dashboard
        open_item = Gtk.MenuItem(label="Open Email Monitor")
        open_item.connect("activate", self.open_dashboard)
        menu.append(open_item)

        # Check Inbox Now
        check_item = Gtk.MenuItem(label="Check Inbox Now")
        check_item.connect("activate", self.check_inbox)
        menu.append(check_item)

        menu.append(Gtk.SeparatorMenuItem())

        # Quit tray
        quit_item = Gtk.MenuItem(label="Quit Tray")
        quit_item.connect("activate", self.quit)
        menu.append(quit_item)

        menu.show_all()
        self.indicator.set_menu(menu)

    def update_status(self):
        """Fetch and update status from the email service API."""
        try:
            req = urllib.request.Request(f"{EMAIL_SERVICE_URL}/api/email/status", timeout=3)
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read())

                self.service_online = True
                self.daemon_running = data.get("daemon_running", False)
                self.connected = data.get("connected", False)
                supervisor_running = data.get("supervisor_running", False)
                email_address = data.get("email_address", "Unknown")
                restart_count = data.get("restart_count", 0)

                # Update status labels
                self.service_status_item.set_label("Service: Online")

                daemon_status = "Running" if self.daemon_running else "Stopped"
                if supervisor_running:
                    daemon_status += " (Supervised)"
                if restart_count > 0:
                    daemon_status += f" [{restart_count} restarts]"
                self.daemon_status_item.set_label(f"Daemon: {daemon_status}")

                conn_status = "Connected" if self.connected else "Disconnected"
                self.connection_status_item.set_label(f"Email: {conn_status} - {email_address}")

                # Update icon based on state
                if self.daemon_running and self.connected:
                    self.indicator.set_icon(ICON_DAEMON_RUNNING)
                elif self.connected:
                    self.indicator.set_icon(ICON_CONNECTED)
                else:
                    self.indicator.set_icon(ICON_DISCONNECTED)

                # Update menu sensitivity
                self.start_item.set_sensitive(not self.daemon_running)
                self.stop_item.set_sensitive(self.daemon_running)

                self.last_error = None

        except urllib.error.URLError as e:
            self._handle_service_offline(f"Connection error: {e.reason}")
        except Exception as e:
            self._handle_service_offline(f"Error: {str(e)}")

        return True  # Continue timer

    def _handle_service_offline(self, error_msg):
        """Handle when the service is offline."""
        self.service_online = False
        self.daemon_running = False
        self.connected = False
        self.last_error = error_msg

        self.service_status_item.set_label("Service: Offline")
        self.daemon_status_item.set_label("Daemon: N/A")
        self.connection_status_item.set_label("Email: N/A")
        self.indicator.set_icon(ICON_OFFLINE)

        # Disable daemon controls when service is down
        self.start_item.set_sensitive(False)
        self.stop_item.set_sensitive(False)

    def start_daemon(self, _):
        """Start the email monitoring daemon via API."""
        try:
            req = urllib.request.Request(
                f"{EMAIL_SERVICE_URL}/api/email/start",
                method='POST',
                data=b'',
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())
                if data.get("success"):
                    self.daemon_status_item.set_label("Daemon: Starting...")
                    self._show_notification("Email Daemon", "Daemon started successfully")
                else:
                    error = data.get("error", "Unknown error")
                    self._show_notification("Email Daemon", f"Failed to start: {error}")
        except Exception as e:
            self._show_notification("Email Daemon", f"Error: {str(e)}")

        # Force status update
        GLib.timeout_add(500, self.update_status)

    def stop_daemon(self, _):
        """Stop the email monitoring daemon via API."""
        try:
            req = urllib.request.Request(
                f"{EMAIL_SERVICE_URL}/api/email/stop",
                method='POST',
                data=b'',
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())
                if data.get("success"):
                    self.daemon_status_item.set_label("Daemon: Stopping...")
                    self._show_notification("Email Daemon", "Daemon stopped successfully")
                else:
                    error = data.get("error", "Unknown error")
                    self._show_notification("Email Daemon", f"Failed to stop: {error}")
        except Exception as e:
            self._show_notification("Email Daemon", f"Error: {str(e)}")

        # Force status update
        GLib.timeout_add(500, self.update_status)

    def restart_daemon(self, _):
        """Restart the email monitoring daemon (stop then start)."""
        self.daemon_status_item.set_label("Daemon: Restarting...")

        # Stop first
        try:
            req = urllib.request.Request(
                f"{EMAIL_SERVICE_URL}/api/email/stop",
                method='POST',
                data=b'',
                headers={'Content-Type': 'application/json'}
            )
            urllib.request.urlopen(req, timeout=10)
        except:
            pass  # Ignore stop errors

        # Wait a moment then start
        def delayed_start():
            try:
                req = urllib.request.Request(
                    f"{EMAIL_SERVICE_URL}/api/email/start",
                    method='POST',
                    data=b'',
                    headers={'Content-Type': 'application/json'}
                )
                urllib.request.urlopen(req, timeout=10)
                self._show_notification("Email Daemon", "Daemon restarted successfully")
            except Exception as e:
                self._show_notification("Email Daemon", f"Restart error: {str(e)}")
            self.update_status()
            return False

        GLib.timeout_add(1000, delayed_start)

    def open_dashboard(self, _):
        """Open the email monitor tab in mini-mind dashboard."""
        # Open to the email monitor widget directly
        subprocess.Popen(["xdg-open", f"{EMAIL_SERVICE_URL}/#email-monitor"])

    def check_inbox(self, _):
        """Trigger manual inbox check."""
        try:
            req = urllib.request.Request(
                f"{EMAIL_SERVICE_URL}/api/email/check",
                method='POST',
                data=b'',
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read())
                if data.get("success"):
                    result = data.get("result", {})
                    new_count = result.get("new_missions", 0) + result.get("new_investigations", 0)
                    self._show_notification("Inbox Check", f"Found {new_count} new items")
                else:
                    error = data.get("error", "Unknown error")
                    self._show_notification("Inbox Check", f"Error: {error}")
        except Exception as e:
            self._show_notification("Inbox Check", f"Error: {str(e)}")

    def _show_notification(self, title, message):
        """Show a desktop notification."""
        try:
            subprocess.run([
                "notify-send",
                "-i", "mail-send",
                f"Email Service: {title}",
                message
            ], capture_output=True)
        except:
            pass  # Notifications are optional

    def quit(self, _):
        """Quit the tray application."""
        Gtk.main_quit()


def main():
    """Main entry point."""
    # Check if GTK is available
    try:
        indicator = EmailInvestigationTrayIndicator()
        Gtk.main()
    except Exception as e:
        print(f"Error starting tray indicator: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
