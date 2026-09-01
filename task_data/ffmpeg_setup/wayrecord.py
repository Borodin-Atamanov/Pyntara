#!/usr/bin/python3
"""Capture the Wayland screen as raw video on stdout.

The ffmpeg CLI cannot capture Wayland natively, so this script is a capture
source: it asks the xdg-desktop-portal ScreenCast portal for a PipeWire
stream (a file descriptor plus the stream node id), reads that stream with
the GStreamer pipewiresrc element and writes raw I420 frames to stdout. The
caller pipes the stream into ffmpeg and controls every encoding parameter:

pyntara-wayrecord | ffmpeg -f rawvideo -pix_fmt yuv420p -s 1920x1080 -r 30 -i pipe:0 -c:v libx264 out.mp4

The first run shows the KDE screen dialog once; the script saves the
single-use restore token the portal returns and passes it back on later
runs, so the recording starts without asking again. The token lives in the
per-user file wayrecord_token under the pyntara config directory, or in the
path of the PYNTARA_WAYRECORD_TOKEN environment variable. The stream
geometry is printed to stderr so the caller can match -s and -r; stdout
carries only raw frames. The capture stops when the pipe closes (ffmpeg
exits) or on Ctrl+C.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import sys
from pathlib import Path

import dbus
import gi

gi.require_version("Gst", "1.0")

from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import Gst, GLib

DESKTOP_IFACE = "org.freedesktop.portal.Desktop"
DESKTOP_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"
PORTAL_PERSIST_MODE_PERSISTENT = 2
TOKEN_ENV = "PYNTARA_WAYRECORD_TOKEN"
TOKEN_FILE_NAME = "wayrecord_token"
STREAM_FD = 1
DEFAULT_FPS = 30


class PortalClient:
    """Minimal xdg-desktop-portal ScreenCast client (proven against 1.21)."""

    def __init__(self, bus: dbus.Bus):
        self._bus = bus
        self._request_token_counter = 0
        self._session_token_counter = 0
        self._sender_name = re.sub(r"\.", r"_", bus.get_unique_name()[1:])
        self._session: str | None = None
        self._portal = bus.get_object(DESKTOP_IFACE, DESKTOP_PATH)

    def _new_request_path(self) -> tuple[str, str]:
        self._request_token_counter += 1
        token = f"u{self._request_token_counter}"
        path = f"{DESKTOP_PATH}/request/{self._sender_name}/{token}"
        return (path, token)

    def _dbus_screencast(self, method, callback, *args, options=None):
        (request_path, request_token) = self._new_request_path()
        self._bus.add_signal_receiver(
            callback, "Response", REQUEST_IFACE, DESKTOP_IFACE, request_path
        )
        options = dict(options or {})
        options["handle_token"] = request_token
        method(*(args + (options,)), dbus_interface=SCREENCAST_IFACE)

    def create_session(self, callback):
        self._session_token_counter += 1
        token = f"u{self._session_token_counter}"
        self._dbus_screencast(
            self._portal.CreateSession,
            callback,
            options={"session_handle_token": token},
        )

    def select_sources(self, callback, session: str, restore_token: str | None):
        options = {
            "multiple": False,
            "types": dbus.UInt32(1),
            "cursor_mode": dbus.UInt32(2),
            "persist_mode": dbus.UInt32(PORTAL_PERSIST_MODE_PERSISTENT),
        }
        if restore_token:
            options["restore_token"] = restore_token
        self._dbus_screencast(
            self._portal.SelectSources, callback, session, options=options
        )

    def start(self, callback, session: str):
        self._dbus_screencast(self._portal.Start, callback, session, "")

    def open_pipewire_fd(self) -> int:
        empty_dict = dbus.Dictionary(signature="sv")
        fd_obj = self._portal.OpenPipeWireRemote(
            self._session, empty_dict, dbus_interface=SCREENCAST_IFACE
        )
        return int(fd_obj.take())

    def close_session(self):
        if not self._session:
            return
        try:
            sess = self._bus.get_object(
                DESKTOP_IFACE, self._session, follow_name_owner_changes=True
            )
            dbus.Interface(sess, "org.freedesktop.portal.Session").Close()
        except dbus.DBusException as exc:
            print(f"warning: cannot close portal session: {exc}", file=sys.stderr)


def token_path() -> Path:
    """Per-user restore token file, so the portal remembers the screen."""
    override = os.environ.get(TOKEN_ENV)
    if override:
        return Path(override)
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_dir / "pyntara" / TOKEN_FILE_NAME


def load_token() -> str | None:
    try:
        token = token_path().read_text(encoding="utf-8").strip()
        return token or None
    except OSError:
        return None


def save_token(token: str):
    path = token_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
    except OSError as exc:
        print(f"warning: cannot save restore token: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture the Wayland screen as raw video on stdout."
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=DEFAULT_FPS,
        help="output frame rate to match in ffmpeg with -r",
    )
    args = parser.parse_args(argv)
    fps = args.fps
    if fps <= 0:
        print("error: --fps must be a positive integer", file=sys.stderr)
        return 1

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    client = PortalClient(bus)
    loop = GLib.MainLoop()
    state: dict[str, object] = {}

    def on_create(response, results):
        if response != 0:
            print(
                f"error: cannot create portal session ({response})", file=sys.stderr
            )
            loop.quit()
            return
        state["session"] = str(results["session_handle"])
        client._session = str(state["session"])
        print("portal session created", file=sys.stderr)
        client.select_sources(on_select, str(state["session"]), load_token())

    def on_select(response, results):
        if response != 0:
            print(f"error: cannot select sources ({response})", file=sys.stderr)
            loop.quit()
            return
        client.start(on_start, str(state["session"]))

    def on_start(response, results):
        if response != 0:
            print("error: screen sharing was not granted", file=sys.stderr)
            loop.quit()
            return
        streams = results["streams"]
        node_id = int(streams[0][0])
        props = dict(streams[0][1])
        size = props.get("size")
        width = int(size[0]) if size else 0
        height = int(size[1]) if size else 0
        new_token = results.get("restore_token")
        if new_token:
            save_token(str(new_token))
        state["node_id"] = node_id
        state["width"] = width
        state["height"] = height
        state["fd"] = client.open_pipewire_fd()
        loop.quit()

    client.create_session(on_create)
    GLib.timeout_add(
        60000,
        lambda: (print("error: portal timeout", file=sys.stderr), loop.quit())[1],
    )
    loop.run()

    if not state.get("fd"):
        client.close_session()
        print("error: no screen stream", file=sys.stderr)
        return 1

    width = int(state["width"])
    height = int(state["height"])
    if not width or not height:
        client.close_session()
        print("error: portal did not report the screen size", file=sys.stderr)
        return 1

    print(
        f"video stream: {width}x{height} {fps}fps format=yuv420p", file=sys.stderr
    )
    print(
        "pipe into ffmpeg, e.g.: pyntara-wayrecord | ffmpeg -f rawvideo "
        f"-pix_fmt yuv420p -s {width}x{height} -r {fps} -i pipe:0 ...",
        file=sys.stderr,
    )
    print(
        "recording started; Ctrl+C or closing the pipe stops it", file=sys.stderr
    )

    Gst.init(None)
    pipeline = Gst.parse_launch(
        f"pipewiresrc fd={state['fd']} path={state['node_id']} "
        f"! videoconvert ! videorate "
        f"! video/x-raw,format=I420,framerate={fps}/1 ! fdsink"
    )
    fdsink = pipeline.get_by_name("fdsink0")
    fdsink.set_property("fd", STREAM_FD)
    fdsink.set_property("sync", False)

    stop = {"flag": False, "code": 0}

    def on_signal(signum, frame):
        stop["flag"] = True
        GLib.idle_add(loop.quit)

    def on_message(bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            message = err.message
            debug = dbg or ""
            # The reader (ffmpeg) closing the pipe ends the capture normally;
            # a write error to stdout is that expected stop, not a failure.
            if (
                "Broken pipe" in message
                or "EPIPE" in message
                or "Broken pipe" in debug
                or "errno 32" in debug
            ):
                stop["code"] = 0
            else:
                stop["code"] = 1
                print(f"error: capture failed: {message}", file=sys.stderr)
            stop["flag"] = True
            loop.quit()
        return True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    gbus = pipeline.get_bus()
    gbus.add_signal_watch()
    gbus.connect("message", on_message)

    pipeline.set_state(Gst.State.PLAYING)
    loop.run()

    pipeline.set_state(Gst.State.NULL)
    client.close_session()
    return stop["code"]


if __name__ == "__main__":
    sys.exit(main())
