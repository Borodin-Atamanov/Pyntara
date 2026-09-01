#!/usr/bin/python3
"""Record the Wayland screen to a file with ffmpeg.

The bridge: a python3-dbus client asks the xdg-desktop-portal ScreenCast
portal for a PipeWire stream (fd plus node id), GStreamer pipewiresrc reads
that stream, and ffmpeg encodes the raw frames into the output file. The
screen choice is persisted through the portal restore_token, so after the
first run (one dialog) the recording starts without asking again.

Run as the desktop user in the Wayland session. Dependencies are system
packages: python3-dbus, gstreamer1.0-pipewire (gi bindings) and ffmpeg.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import Gst, GLib

DESKTOP_IFACE = "org.freedesktop.portal.Desktop"
DESKTOP_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"
PORTAL_PERSIST_MODE_PERSISTENT = 2
TOKEN_ENV = "PYNTARA_WAYRECORD_TOKEN"
TOKEN_FILE_NAME = "wayrecord_token"
VAAPI_DEVICE = "/dev/dri/renderD128"


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
            print(f"warning: cannot close portal session: {exc}")


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
        print(f"warning: cannot save restore token: {exc}")


def default_output() -> Path:
    videos = Path(os.environ.get("XDG_VIDEOS_DIR", Path.home() / "Videos"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return videos / f"wayrecord_{stamp}.mp4"


def build_ffmpeg_command(
    output: Path, width: int, height: int, fps: int, codec: str
) -> list[str]:
    """The ffmpeg command that consumes raw I420 frames from stdin."""
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "warning",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "yuv420p",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
    ]
    if "vaapi" in codec:
        cmd += [
            "-vaapi_device",
            VAAPI_DEVICE,
            "-vf",
            "format=nv12,hwupload",
            "-c:v",
            codec,
            "-qp",
            "24",
        ]
    else:
        cmd += ["-c:v", codec, "-preset", "veryfast", "-crf", "23"]
    cmd += ["-pix_fmt", "yuv420p", str(output)]
    return cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record the Wayland screen to a file with ffmpeg."
    )
    parser.add_argument("output", nargs="?", type=Path, help="output file")
    parser.add_argument("--fps", type=int, default=30, help="frame rate")
    parser.add_argument("--codec", default="libx264", help="ffmpeg video encoder")
    parser.add_argument(
        "--seconds",
        type=float,
        default=0,
        help="record this many seconds, then stop (0 = until Ctrl+C)",
    )
    args = parser.parse_args(argv)
    output = args.output if args.output is not None else default_output()

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    client = PortalClient(bus)
    loop = GLib.MainLoop()
    state: dict[str, object] = {}

    def on_create(response, results):
        if response != 0:
            print(f"error: cannot create portal session ({response})")
            loop.quit()
            return
        state["session"] = str(results["session_handle"])
        client._session = str(state["session"])
        print("portal session created")
        client.select_sources(on_select, str(state["session"]), load_token())

    def on_select(response, results):
        if response != 0:
            print(f"error: cannot select sources ({response})")
            loop.quit()
            return
        client.start(on_start, str(state["session"]))

    def on_start(response, results):
        if response != 0:
            print("error: screen sharing was not granted")
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
    GLib.timeout_add(60000, lambda: (print("error: portal timeout"), loop.quit())[1])
    loop.run()

    if not state.get("fd"):
        client.close_session()
        print("error: no screen stream")
        return 1

    width = int(state["width"])
    height = int(state["height"])
    fps = args.fps
    if not width or not height:
        print("error: portal did not report the screen size")
        client.close_session()
        return 1
    print(f"recording {width}x{height}@{fps} to {output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_cmd = build_ffmpeg_command(output, width, height, fps, args.codec)
    ffmpeg = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)

    Gst.init(None)
    pipeline = Gst.parse_launch(
        f"pipewiresrc fd={state['fd']} path={state['node_id']} "
        f"! videoconvert ! videorate "
        f"! video/x-raw,format=I420,framerate={fps}/1 ! fdsink"
    )
    fdsink = pipeline.get_by_name("fdsink0")
    fdsink.set_property("fd", ffmpeg.stdin.fileno())
    fdsink.set_property("sync", False)

    stop = {"flag": False}

    def on_signal(signum, frame):
        stop["flag"] = True
        GLib.idle_add(loop.quit)

    def on_message(bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print(f"error: capture failed: {err.message}")
            stop["flag"] = True
            loop.quit()
        return True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    gbus = pipeline.get_bus()
    gbus.add_signal_watch()
    gbus.connect("message", on_message)
    if args.seconds > 0:
        GLib.timeout_add_seconds(
            max(1, int(args.seconds)), lambda: (stop.__setitem__("flag", True), loop.quit())[1]
        )

    pipeline.set_state(Gst.State.PLAYING)
    print("recording started; press Ctrl+C to stop")
    loop.run()

    pipeline.set_state(Gst.State.NULL)
    ffmpeg.stdin.close()
    try:
        ffmpeg.wait(timeout=30)
    except subprocess.TimeoutExpired:
        ffmpeg.terminate()
        ffmpeg.wait(timeout=10)
    client.close_session()
    if ffmpeg.returncode != 0:
        print(f"error: ffmpeg exited with {ffmpeg.returncode}")
        return 1
    print(f"recorded {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
