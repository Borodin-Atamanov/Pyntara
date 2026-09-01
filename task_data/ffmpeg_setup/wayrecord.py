#!/usr/bin/python3
"""Capture the Wayland screen as raw video on stdout.

The ffmpeg CLI cannot capture Wayland natively, so this script is a capture
source: it asks the xdg-desktop-portal ScreenCast portal for a PipeWire
stream (a file descriptor plus the stream node id), reads that stream with
the GStreamer pipewiresrc element and writes raw frames to stdout. The
caller pipes the stream into ffmpeg and controls every encoding parameter:

pyntara-wayrecord | ffmpeg -f rawvideo -pix_fmt yuv420p -s 1920x1080 -r 30 -i pipe:0 -c:v libx264 out.mp4

The stream format is given as one GStreamer caps string with --caps; the
script passes it to the pipeline unchanged, so any combination of pixel
format, size and frame rate is possible, from 120 fps to one frame per ten
seconds (framerate=1/10). The first run shows the KDE screen dialog once;
the script saves the single-use restore token the portal returns and passes
it back on later runs, so the recording starts without asking again. The
token lives in the per-user file wayrecord_token under the pyntara config
directory, or in the path of the PYNTARA_WAYRECORD_TOKEN environment
variable. All messages go to stderr; stdout carries only raw frames. The
capture stops when the pipe closes (ffmpeg exits) or on Ctrl+C.
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
DEFAULT_CAPS = "video/x-raw,format=I420,framerate=30/1"

PROG = "pyntara-wayrecord"

DESCRIPTION = (
    "Capture the Wayland screen as raw video on stdout.\n"
    "Streams the screen through the ScreenCast portal and writes raw frames\n"
    "to stdout, so the caller pipes the stream into ffmpeg and controls every\n"
    "encoding parameter. The first run shows the screen dialog once; the\n"
    "portal restore token is then saved and reused, so later runs start\n"
    "without asking."
)

EXAMPLES = """
examples:
  record the whole screen at 30 fps (default caps):
    pyntara-wayrecord | ffmpeg -f rawvideo -pix_fmt yuv420p -s 1920x1080 -r 30 -i pipe:0 -c:v libx264 out.mp4

  high frame rate, 120 fps:
    pyntara-wayrecord --caps video/x-raw,format=I420,framerate=120/1 | ffmpeg -f rawvideo -pix_fmt yuv420p -s 1920x1080 -r 120 -i pipe:0 -c:v libx264 out.mp4

  one frame per ten seconds (timelapse):
    pyntara-wayrecord --caps video/x-raw,format=I420,framerate=1/10 | ffmpeg -f rawvideo -pix_fmt yuv420p -s 1920x1080 -r 1/10 -i pipe:0 -c:v libx264 out.mp4

  smaller NV12 stream for hardware encoding:
    pyntara-wayrecord --caps video/x-raw,format=NV12,width=1280,height=720,framerate=30/1 | ffmpeg -f rawvideo -pix_fmt nv12 -s 1280x720 -r 30 -i pipe:0 -c:v h264_vaapi out.mp4

  stop by closing the pipe (for example ffmpeg -t) or with Ctrl+C
"""


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=DESCRIPTION,
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--caps",
        default=DEFAULT_CAPS,
        metavar="CAPS",
        help=(
            "GStreamer video caps for the captured stream, passed to the "
            "pipeline unchanged. Any pixel format, size and frame rate "
            f"combination is possible. Default: {DEFAULT_CAPS} (native size, "
            "30 fps)."
        ),
    )
    return parser


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


def caps_geometry(
    caps_string: str, native_width: int, native_height: int
) -> tuple[str, int, int, int, int]:
    """Resolve format, size and frame rate from the caps for the report.

    Fields missing from the caps fall back to the native screen size and to
    30 fps; the values only shape the stderr hint, the caps itself is passed
    to the pipeline unchanged.
    """

    try:
        caps = Gst.Caps.from_string(caps_string)
        structure = caps.get_structure(0)
    except Exception:
        return "I420", native_width, native_height, 30, 1
    fmt = structure.get_string("format") or "I420"
    ok, width = structure.get_int("width")
    width = width if ok else native_width
    ok, height = structure.get_int("height")
    height = height if ok else native_height
    ok, fps_num, fps_den = structure.get_fraction("framerate")
    if not ok:
        fps_num, fps_den = 30, 1
    return fmt, width, height, fps_num, fps_den


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    caps_string = args.caps
    Gst.init(None)

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

    native_width = int(state["width"])
    native_height = int(state["height"])
    if not native_width or not native_height:
        client.close_session()
        print("error: portal did not report the screen size", file=sys.stderr)
        return 1

    fmt, width, height, fps_num, fps_den = caps_geometry(
        caps_string, native_width, native_height
    )
    print(f"video caps: {caps_string}", file=sys.stderr)
    fps_text = f"{fps_num}" if fps_den == 1 else f"{fps_num}/{fps_den}"
    print(
        "pipe into ffmpeg, e.g.: pyntara-wayrecord | ffmpeg -f rawvideo "
        f"-pix_fmt {fmt.lower()} -s {width}x{height} -r {fps_text} -i pipe:0 ...",
        file=sys.stderr,
    )
    print(
        "recording started; Ctrl+C or closing the pipe stops it", file=sys.stderr
    )

    pipeline = Gst.parse_launch(
        f"pipewiresrc fd={state['fd']} path={state['node_id']} "
        f"! videoconvert ! videoscale ! videorate ! {caps_string} ! fdsink"
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
