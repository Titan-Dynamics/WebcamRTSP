import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from collections import deque
import socket

if os.name == "nt":
    try:
        CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW  # Hide console windows for child processes
    except Exception:  # pragma: no cover
        CREATE_NO_WINDOW = 0x08000000
else:
    CREATE_NO_WINDOW = 0

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except Exception:  # pragma: no cover
    tk = None
    ttk = None
    messagebox = None
    filedialog = None


@dataclass
class RtspTarget:
    host: str = "127.0.0.1"
    port: int = 8554
    path: str = "live.stream"

    @property
    def url(self) -> str:
        return f"rtsp://{self.host}:{self.port}/{self.path}"


class StreamingApp(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master)
        self.master = master
        self.pack(fill=tk.BOTH, expand=True)

        self.ffmpeg_path = self._detect_ffmpeg()
        self.mediamtx_path = self._detect_mediamtx()
        self.devices = []  # list[str]
        self.proc: subprocess.Popen | None = None
        self.mtx_proc: subprocess.Popen | None = None
        self.mtx_launched_here: bool = False
        self.stop_event = threading.Event()
        self._out_tail = deque(maxlen=80)
        self._mtx_tail = deque(maxlen=80)

        self._build_ui()
        # load persisted settings (before enumerating devices)
        try:
            self._load_settings()
        except Exception:
            pass
        # Kill any pre-existing MediaMTX per user request
        try:
            self._kill_existing_mediamtx()
        except Exception as e:
            self._log("Startup mediamtx kill raised:", e)
        # initial enumerate
        self.refresh_devices()
        self._update_commands_preview()
        # Fit window to minimal required size and keep fixed
        self._fit_to_min_size()
        # Reflect initial status in title
        self._update_window_title()

    # ---------- UI ----------
    def _build_ui(self):
        self.master.title("Webcam RTSP Streamer")
        # Window size will be set to requested size after UI is built

        pad = {"padx": 8, "pady": 6}
        # Status for window title
        self.status_var = tk.StringVar(value="Inactive")

        # We will build 4 rows, each split into left/right subframes
        # Make both main columns expand to fill window width
        for c in range(2):
            self.columnconfigure(c, weight=1)

        # Row 1
        row = 0
        f1l = ttk.Frame(self)
        f1r = ttk.Frame(self)
        f1l.grid(row=row, column=0, sticky=tk.EW, **pad)
        f1r.grid(row=row, column=1, sticky=tk.EW, **pad)
        try:
            f1l.columnconfigure(1, weight=1)  # allow camera dropdown to stretch
        except Exception:
            pass

        # Camera selector with label
        ttk.Label(f1l, text="Camera:").grid(row=0, column=0, sticky=tk.W, **pad)
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(f1l, textvariable=self.device_var, state="readonly", width=32)
        self.device_combo.grid(row=0, column=1, sticky=tk.EW, **pad)
        ttk.Button(f1l, text="Refresh", command=self.refresh_devices).grid(row=0, column=2, sticky=tk.W, **pad)

        ttk.Label(f1r, text="Res:").grid(row=0, column=0, sticky=tk.W, **pad)
        self.res_var = tk.StringVar(value="640x480")
        self.res_combo = ttk.Combobox(f1r, textvariable=self.res_var, values=[
            "640x480", "800x600", "1280x720", "1920x1080", "2560x1440", "3840x2160",
        ], width=12)
        self.res_combo.grid(row=0, column=1, sticky=tk.W, **pad)
        ttk.Label(f1r, text="FPS:").grid(row=0, column=2, sticky=tk.W, **pad)
        self.fps_var = tk.StringVar(value="30")
        self.fps_combo = ttk.Combobox(f1r, textvariable=self.fps_var, values=["24", "25", "30", "50", "60"], width=6)
        self.fps_combo.grid(row=0, column=3, sticky=tk.W, **pad)

        # Row 2
        row += 1
        f2l = ttk.Frame(self)
        f2r = ttk.Frame(self)
        f2l.grid(row=row, column=0, sticky=tk.EW, **pad)
        f2r.grid(row=row, column=1, sticky=tk.EW, **pad)
        try:
            f2l.columnconfigure(1, weight=1)  # host entry expands
            f2r.columnconfigure(1, weight=1)  # path entry expands
        except Exception:
            pass

        ttk.Label(f2l, text="Hostname:").grid(row=0, column=0, sticky=tk.W, **pad)
        self.host_var = tk.StringVar(value="127.0.0.1")
        ttk.Entry(f2l, textvariable=self.host_var, width=18).grid(row=0, column=1, sticky=tk.EW, **pad)
        ttk.Label(f2l, text="Port:").grid(row=0, column=2, sticky=tk.W, **pad)
        self.port_var = tk.StringVar(value="8554")
        ttk.Entry(f2l, textvariable=self.port_var, width=8).grid(row=0, column=3, sticky=tk.W, **pad)

        ttk.Label(f2r, text="Path:").grid(row=0, column=0, sticky=tk.W, **pad)
        self.path_var = tk.StringVar(value="live.stream")
        ttk.Entry(f2r, textvariable=self.path_var, width=20).grid(row=0, column=1, sticky=tk.EW, **pad)
        # Start/Stop toggle on same row
        self.toggle_btn = ttk.Button(f2r, text="Start", command=self.toggle_stream)
        self.toggle_btn.grid(row=0, column=2, sticky=tk.W, **pad)

        # Row 3: RTSP URL (full row with Copy)
        row += 1
        furl = ttk.Frame(self)
        furl.grid(row=row, column=0, columnspan=2, sticky=tk.EW, **pad)
        ttk.Label(furl, text="RTSP URL:", width=12).grid(row=0, column=0, sticky=tk.W, **pad)
        # Allow URL entry to stretch within its frame
        try:
            furl.columnconfigure(1, weight=1)
        except Exception:
            pass
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(furl, textvariable=self.url_var, state="readonly", width=72)
        self.url_entry.grid(row=0, column=1, sticky=tk.EW, **pad)
        ttk.Button(furl, text="Copy", command=self._copy_url).grid(row=0, column=2, sticky=tk.E, **pad)

        # Row 4: Mission Planner GStreamer (full row with Copy)
        row += 1
        fgs = ttk.Frame(self)
        fgs.grid(row=row, column=0, columnspan=2, sticky=tk.EW, **pad)
        ttk.Label(fgs, text="GStreamer:", width=12).grid(row=0, column=0, sticky=tk.W, **pad)
        # Allow GS entry to stretch within its frame
        try:
            fgs.columnconfigure(1, weight=1)
        except Exception:
            pass
        self.gs_var = tk.StringVar()
        self.gs_entry = ttk.Entry(fgs, textvariable=self.gs_var, state="readonly", width=72)
        self.gs_entry.grid(row=0, column=1, sticky=tk.EW, **pad)
        ttk.Button(fgs, text="Copy", command=self._copy_gs).grid(row=0, column=2, sticky=tk.E, **pad)

        # React to field changes
        for v in (self.res_var, self.fps_var, self.host_var, self.port_var, self.path_var, self.device_var):
            v.trace_add("write", lambda *args: (self._update_commands_preview(), self._save_settings()))

    # ---------- Helpers ----------
    def _detect_ffmpeg(self) -> str | None:
        # When running as PyInstaller bundle, check _MEIPASS first
        if getattr(sys, 'frozen', False):
            bundle_dir = sys._MEIPASS
            bundled = os.path.join(bundle_dir, "ffmpeg.exe")
            if os.path.isfile(bundled):
                return bundled
        # Prefer executable next to script, then CWD, then PATH
        script_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
        cwd = os.getcwd()
        candidates = [
            os.path.join(script_dir, "ffmpeg.exe"),
            os.path.join(script_dir, "ffmpeg"),
            os.path.join(cwd, "ffmpeg.exe"),
            os.path.join(cwd, "ffmpeg"),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")

    def _detect_mediamtx(self) -> str | None:
        # When running as PyInstaller bundle, check _MEIPASS first
        if getattr(sys, 'frozen', False):
            bundle_dir = sys._MEIPASS
            bundled = os.path.join(bundle_dir, "mediamtx.exe")
            if os.path.isfile(bundled):
                return bundled
        # Prefer executable next to script, then CWD, then PATH
        script_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
        cwd = os.getcwd()
        candidates = [
            os.path.join(script_dir, "mediamtx.exe"),
            os.path.join(script_dir, "mediamtx"),
            os.path.join(cwd, "mediamtx.exe"),
            os.path.join(cwd, "mediamtx"),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return shutil.which("mediamtx") or shutil.which("mediamtx.exe")

    # Removed ffmpeg browse/detect UI; assume ffmpeg.exe is next to the script

    def _rtsp_target(self) -> RtspTarget:
        host = (self.host_var.get() or "127.0.0.1").strip()
        try:
            port = int(self.port_var.get())
        except Exception:
            port = 8554
        path = (self.path_var.get() or "live.stream").lstrip("/")
        return RtspTarget(host=host, port=port, path=path)

    def _build_ffmpeg_cmd(self) -> list[str]:
        if not self.ffmpeg_path:
            raise RuntimeError("FFmpeg not found. Please ensure ffmpeg is in PATH or next to this script.")

        device = self.device_var.get()
        if not device:
            raise RuntimeError("Select a camera device.")
        res = self.res_var.get().strip() or "640x480"
        fps = self.fps_var.get().strip() or "30"
        target = self._rtsp_target()

        # Compose command to match the provided example ordering
        cmd = [
            self.ffmpeg_path,
            "-f", "dshow",
            "-rtbufsize", "100M",
            "-thread_queue_size", "512",
            "-i",
            # Windows dshow: for Popen(list), do NOT embed quotes around the device name
            (f"video={device}" if os.name == "nt" else f"video={shlex.quote(device)}"),
            "-r", str(fps),
            "-video_size", res,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-x264-params", "keyint=15:min-keyint=15:scenecut=-1",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-max_delay", "0",
            "-flush_packets", "1",
            "-f", "rtsp",
        ]

        # Listen mode removed; using external MediaMTX

        cmd += [
            "-rtsp_transport",
            "tcp",
            target.url,
        ]
        return cmd

    def _update_commands_preview(self):
        url = self._rtsp_target().url
        self.url_var.set(url)
        gs = (
            f"rtspsrc location={url} udp-reconnect=1 timeout=0 do-retransmission=false ! "
            f"application/x-rtp ! decodebin3 ! queue max-size-buffers=1 leaky=2 ! "
            f"videoconvert ! video/x-raw,format=BGRA ! appsink name=outsink sync=false"
        )
        # Update read-only entries
        try:
            self.gs_var.set(gs)
        except Exception:
            pass
        # No coupling to MediaMTX; external server expected unless listen mode

    def _set_text(self, widget: tk.Text, value: str):
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, value)
        widget.configure(state=tk.DISABLED)

    def _fit_to_min_size(self):
        try:
            self.master.update_idletasks()
            w = self.master.winfo_reqwidth()
            h = self.master.winfo_reqheight()
            # Apply exact requested geometry and prevent resizing
            self.master.geometry(f"{max(1, w)}x{max(1, h)}")
            self.master.minsize(max(1, w), max(1, h))
            self.master.resizable(False, False)
        except Exception:
            pass

    def _update_window_title(self):
        try:
            base = "Webcam RTSP Streamer"
            status = self.status_var.get() if hasattr(self, "status_var") else ""
            suffix = f" - {status}" if status else ""
            self.master.title(base + suffix)
        except Exception:
            pass

    # ---------- Copy helpers ----------
    def _copy_to_clipboard(self, text: str):
        try:
            self.master.clipboard_clear()
            self.master.clipboard_append(text)
            self._log("Copied to clipboard", text[:80] + ("…" if len(text) > 80 else ""))
        except Exception as e:
            self._log("Clipboard copy failed:", e)

    def _copy_url(self):
        self._copy_to_clipboard(self.url_var.get())

    def _copy_gs(self):
        try:
            txt = self.gs_var.get()
        except Exception:
            txt = ""
        self._copy_to_clipboard(txt)

    # ---------- Debug logging ----------
    def _log(self, *args):
        try:
            print("[RTSP-GUI]", *args, flush=True)
        except Exception:
            pass

    def _format_cmd(self, cmd: list[str]) -> str:
        # Produce a human-readable command string
        def q(a: str) -> str:
            if os.name == "nt":
                return f'"{a}"' if (" " in a or "\t" in a or "\"" in a) else a
            else:
                return shlex.quote(a)

        return " ".join(q(c) for c in cmd)

    # ---------- Device enumeration ----------
    def refresh_devices(self):
        devices = self._list_dshow_cameras()
        self.devices = devices
        self.device_combo["values"] = devices
        if devices and (not self.device_var.get() or self.device_var.get() not in devices):
            self.device_var.set(devices[0])
        if not devices:
            self.device_var.set("")

    def _list_dshow_cameras(self) -> list[str]:
        ff = self.ffmpeg_path or "ffmpeg"
        try:
            # ffmpeg prints devices to stderr
            proc = subprocess.run(
                [ff, "-f", "dshow", "-list_devices", "true", "-i", "dummy"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
            output = proc.stderr
        except Exception as e:
            output = str(e)

        devices = []
        capture = False
        for line in output.splitlines():
            s = line.strip()
            if "DirectShow video devices" in s:
                capture = True
                continue
            if "DirectShow audio devices" in s:
                capture = False
            if capture:
                # Lines like: "USB2.0 PC CAMERA"
                if s.startswith('"') and s.endswith('"'):
                    devices.append(s.strip('"'))
        return devices

    # ---------- Streaming control ----------
    def start_stream(self):
        # If we still have a reference to a finished process, clear it
        if self.proc is not None:
            try:
                if self.proc.poll() is None:
                    # Still running, ignore duplicate start
                    return
            except Exception:
                pass
            # Stale reference to exited process
            self._log("Clearing stale ffmpeg process reference before start")
            self.proc = None
        if not self.ffmpeg_path:
            messagebox.showerror("FFmpeg not found", "FFmpeg not found. Place ffmpeg.exe next to this script.")
            return
        # Proactively kill any pre-existing ffmpeg instances to avoid conflicts
        try:
            self._kill_existing_ffmpeg()
        except Exception as e:
            self._log("Pre-start ffmpeg kill raised:", e)
        try:
            cmd = self._build_ffmpeg_cmd()
        except Exception as e:
            messagebox.showerror("Invalid settings", str(e))
            return

        self.stop_event.clear()

        # Ensure MediaMTX is running
        # Proactively kill any pre-existing MediaMTX to avoid port conflicts
        try:
            self._kill_existing_mediamtx()
        except Exception as e:
            self._log("Pre-start mediamtx kill raised:", e)
        if not self._ensure_mediamtx_running():
            messagebox.showerror(
                "MediaMTX not running",
                "MediaMTX is required. Place mediamtx.exe next to this script or in PATH.",
            )
            self._log("MediaMTX not running or failed to start")
            return
        # Wait for RTSP TCP port to be ready
        tgt = self._rtsp_target()
        if not self._wait_for_port(tgt.host, tgt.port, timeout=6.0):
            self._log(f"RTSP server not listening at {tgt.host}:{tgt.port}")
            messagebox.showerror(
                "RTSP not ready",
                f"Could not connect to {tgt.host}:{tgt.port}.\nCheck firewall permissions or port conflicts.",
            )
            return

        # On Windows, hide any console windows from child processes
        creationflags = CREATE_NO_WINDOW

        # Debug info
        target = self._rtsp_target()
        self._log("Starting stream with:", {
            "ffmpeg": self.ffmpeg_path,
            "device": self.device_var.get(),
            "resolution": self.res_var.get(),
            "fps": self.fps_var.get(),
            "url": target.url,
        })
        self._log("FFmpeg command:")
        self._log(self._format_cmd(cmd))

        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=creationflags,
            )
        except FileNotFoundError:
            self.proc = None
            messagebox.showerror("FFmpeg error", "Failed to launch ffmpeg. Check the path.")
            self._log("FFmpeg launch failed: FileNotFoundError")
            return
        except Exception as e:  # pragma: no cover
            self.proc = None
            messagebox.showerror("FFmpeg error", f"Failed to start stream: {e}")
            self._log("FFmpeg launch failed:", e)
            return

        self._set_running_state(True)

        # Reader thread to keep buffers from filling
        threading.Thread(target=self._pump_output, daemon=True).start()
        # Watcher thread
        threading.Thread(target=self._watch_process, daemon=True).start()
        # Start MediaMTX log pump if we launched it
        if self.mtx_proc and self.mtx_launched_here:
            threading.Thread(target=self._pump_mtx_output, daemon=True).start()

    def _pump_output(self):
        if not self.proc or not self.proc.stdout:
            return
        try:
            for line in self.proc.stdout:
                try:
                    ln = line.rstrip("\r\n")
                    if ln:
                        self._log("ffmpeg:", ln)
                        self._out_tail.append(ln)
                except Exception:
                    pass
                if self.stop_event.is_set():
                    break
        except Exception:
            pass

    def _watch_process(self):
        p = self.proc
        if not p:
            return
        rc = p.wait()
        self._log(f"ffmpeg exited with code {rc}")

        # Reset UI when process ends
        def _reset_ui():
            # Clear process reference so user can start again
            self.proc = None
            self._set_running_state(False)

        self.master.after(0, _reset_ui)
        if rc != 0 and not self.stop_event.is_set():
            # Show last lines for quick diagnosis
            tail = "\n".join(list(self._out_tail)[-20:])
            try:
                messagebox.showerror("Stream stopped", f"ffmpeg exited with code {rc}.\n\nLast output:\n{tail}")
            except Exception:
                self._log("ffmpeg error tail:", tail)

    def _pump_mtx_output(self):
        p = self.mtx_proc
        if not p or not p.stdout:
            return
        try:
            for line in p.stdout:
                try:
                    ln = line.rstrip("\r\n")
                    if ln:
                        self._log("mediamtx:", ln)
                        self._mtx_tail.append(ln)
                except Exception:
                    pass
        except Exception:
            pass

    def _kill_existing_ffmpeg(self):
        # Try to stop any existing ffmpeg processes system-wide (user requested behavior)
        # First, ensure our tracked process is not running
        try:
            if self.proc and self.proc.poll() is None:
                self._log("Terminating existing tracked ffmpeg process before start")
                self.stop_stream()
        except Exception:
            pass
        # Now, issue system-level kills for stray ffmpeg processes
        if os.name == "nt":
            cmd = ["taskkill", "/IM", "ffmpeg.exe", "/F"]
            self._log("Issuing:", " ".join(cmd))
            try:
                subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=5,
                    creationflags=CREATE_NO_WINDOW,
                )
            except Exception as e:
                self._log("taskkill error:", e)
        else:
            issued = False
            if shutil.which("pkill"):
                cmd = ["pkill", "-f", "ffmpeg"]
                self._log("Issuing:", " ".join(cmd))
                try:
                    subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        timeout=5,
                        creationflags=CREATE_NO_WINDOW,
                    )
                    issued = True
                except Exception as e:
                    self._log("pkill error:", e)
            if not issued and shutil.which("killall"):
                cmd = ["killall", "ffmpeg"]
                self._log("Issuing:", " ".join(cmd))
                try:
                    subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        timeout=5,
                        creationflags=CREATE_NO_WINDOW,
                    )
                except Exception as e:
                    self._log("killall error:", e)
        # Give the OS a moment to reap processes
        time.sleep(0.2)

    def _kill_existing_mediamtx(self):
        # Kill any running mediamtx processes system-wide (user requested behavior)
        if os.name == "nt":
            cmd = ["taskkill", "/IM", "mediamtx.exe", "/F"]
            self._log("Issuing:", " ".join(cmd))
            try:
                subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=5,
                    creationflags=CREATE_NO_WINDOW,
                )
            except Exception as e:
                self._log("taskkill mediamtx error:", e)
        else:
            issued = False
            if shutil.which("pkill"):
                cmd = ["pkill", "-f", "mediamtx"]
                self._log("Issuing:", " ".join(cmd))
                try:
                    subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        timeout=5,
                        creationflags=CREATE_NO_WINDOW,
                    )
                    issued = True
                except Exception as e:
                    self._log("pkill mediamtx error:", e)
            if not issued and shutil.which("killall"):
                cmd = ["killall", "mediamtx"]
                self._log("Issuing:", " ".join(cmd))
                try:
                    subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        timeout=5,
                        creationflags=CREATE_NO_WINDOW,
                    )
                except Exception as e:
                    self._log("killall mediamtx error:", e)
        time.sleep(0.2)

    def stop_stream(self):
        self.stop_event.set()
        p = self.proc
        self.proc = None
        if not p:
            return
        try:
            if os.name == "nt":
                p.terminate()
                # Give it a moment, then force kill if needed
                for _ in range(20):
                    if p.poll() is not None:
                        break
                    time.sleep(0.1)
                if p.poll() is None:
                    p.kill()
            else:
                p.terminate()
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        finally:
            self._set_running_state(False)
            # Nothing else to stop

    def toggle_stream(self):
        # Single Start/Stop button behavior
        if self.proc and self.proc.poll() is None:
            self.stop_stream()
        else:
            self.start_stream()

    def _set_running_state(self, running: bool):
        # Toggle button text
        try:
            self.toggle_btn.configure(text="Stop" if running else "Start")
        except Exception:
            pass
        # Disable inputs that affect command while streaming
        for w in [
            self.device_combo,
            self.res_combo,
            self.fps_combo,
        ]:
            w.configure(state="disabled" if running else "readonly")
        self.status_var.set("Active" if running else "Inactive")
        self._update_window_title()

    # ---------- Utility ----------
    def destroy(self):  # ensure cleanup on close
        try:
            self.stop_stream()
        finally:
            super().destroy()
        # Stop MediaMTX only if we launched it
        try:
            if self.mtx_launched_here:
                self._stop_mediamtx()
        except Exception:
            pass

    # ---------- MediaMTX management ----------
    # (No FFmpeg browse UI; binary is assumed next to script)

    # ---------- MediaMTX management ----------
    def _detect_mediamtx_yml(self) -> str | None:
        # When running as PyInstaller bundle, check _MEIPASS first
        if getattr(sys, 'frozen', False):
            bundle_dir = sys._MEIPASS
            bundled = os.path.join(bundle_dir, "mediamtx.yml")
            if os.path.isfile(bundled):
                return bundled
        # Prefer config next to script, then CWD
        script_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
        cwd = os.getcwd()
        candidates = [
            os.path.join(script_dir, "mediamtx.yml"),
            os.path.join(cwd, "mediamtx.yml"),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return None

    def _ensure_mediamtx_running(self) -> bool:
        # If a process exists and is alive, good
        if self.mtx_proc and self.mtx_proc.poll() is None:
            return True
        path = self.mediamtx_path or self._detect_mediamtx()
        self.mediamtx_path = path
        if not path:
            return False
        if not os.path.isfile(path) and not shutil.which(path):
            return False
        try:
            creationflags = CREATE_NO_WINDOW
            # Build command with config file if available
            cmd = [path]
            config_path = self._detect_mediamtx_yml()
            if config_path:
                cmd.append(config_path)
                self._log("Starting MediaMTX:", path, "with config:", config_path)
            else:
                self._log("Starting MediaMTX:", path)
            self.mtx_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=creationflags,
            )
            self.mtx_launched_here = True
            # Don't block; give it a short moment
            time.sleep(0.6)
            return True
        except Exception as e:
            self._log("Failed to start MediaMTX:", e)
            self.mtx_proc = None
            self.mtx_launched_here = False
            return False

    def _stop_mediamtx(self):
        p = self.mtx_proc
        self.mtx_proc = None
        if not p:
            return
        try:
            p.terminate()
            for _ in range(20):
                if p.poll() is not None:
                    break
                time.sleep(0.1)
            if p.poll() is None:
                p.kill()
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        finally:
            self.mtx_launched_here = False

    # ---------- Network helpers ----------
    def _wait_for_port(self, host: str, port: int, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_port_open(host, port):
                return True
            time.sleep(0.2)
        return False

    def _is_port_open(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except Exception:
            return False

    # ---------- Settings persistence ----------
    def _settings_path(self) -> str:
        # Use LOCALAPPDATA on Windows, else home
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            folder = os.path.join(base, "RTSPStreamer")
        else:
            folder = os.path.join(os.path.expanduser("~"), ".rtsp_streamer")
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception:
            pass
        return os.path.join(folder, "settings.json")

    def _save_settings(self):
        import json
        data = {
            "device": self.device_var.get(),
            "resolution": self.res_var.get(),
            "fps": self.fps_var.get(),
            "host": self.host_var.get(),
            "port": self.port_var.get(),
            "path": self.path_var.get(),
        }
        try:
            with open(self._settings_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load_settings(self):
        import json
        p = self._settings_path()
        if not os.path.isfile(p):
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        # Apply settings to UI
        if data.get("device"):
            self.device_var.set(data["device"])
        if data.get("resolution"):
            self.res_var.set(data["resolution"])
        if data.get("fps"):
            self.fps_var.set(str(data["fps"]))
        if data.get("host"):
            self.host_var.set(data["host"])
        if data.get("port"):
            self.port_var.set(str(data["port"]))
        if data.get("path"):
            self.path_var.set(data["path"])
        # listen removed
        # no auto_mtx persisted anymore


# --- Monkeypatch improved device enumeration and refresh (post-class) ---
def _better_refresh_devices(self: StreamingApp):
    # Ensure ffmpeg path is valid; detect next to script if missing
    if not self.ffmpeg_path or not os.path.isfile(self.ffmpeg_path):
        self.ffmpeg_path = self._detect_ffmpeg()
    if not self.ffmpeg_path:
        messagebox.showerror("FFmpeg not found", "Cannot enumerate cameras. Place ffmpeg.exe next to this script.")
        devices = []
    else:
        devices = _better_list_dshow_cameras(self)
    self.devices = devices
    self.device_combo["values"] = devices
    if devices and (not self.device_var.get() or self.device_var.get() not in devices):
        self.device_var.set(devices[0])
    if not devices:
        self.device_var.set("")


def _better_list_dshow_cameras(self: StreamingApp) -> list[str]:
    ff = self.ffmpeg_path or self._detect_ffmpeg() or "ffmpeg"
    try:
        proc = subprocess.run(
            [ff, "-hide_banner", "-f", "dshow", "-list_devices", "true", "-i", "dummy"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=10,
        )
        lines = ((proc.stderr or "") + "\n" + (proc.stdout or "")).splitlines()
    except Exception as e:
        lines = str(e).splitlines()

    devices: list[str] = []
    capture = False
    # First pass: generic format with explicit (video) markers
    for line in lines:
        s = line.strip()
        if not s or "Alternative name" in s:
            continue
        if "(video)" in s and '"' in s:
            first = s.find('"')
            last = s.rfind('"')
            if last > first >= 0:
                name = s[first + 1: last]
                if name and name not in devices:
                    devices.append(name)

    # Fallback pass: use section headers (older ffmpeg output)
    if not devices:
        for line in lines:
            s = line.strip()
            if not s:
                continue
            if "DirectShow video devices" in s:
                capture = True
                continue
            if "DirectShow audio devices" in s:
                capture = False
                continue
            if not capture:
                continue
            if "Alternative name" in s:
                continue
            if '"' in s:
                first = s.find('"')
                last = s.rfind('"')
                if last > first >= 0:
                    name = s[first + 1: last]
                    if name and name not in devices:
                        devices.append(name)

    return devices


# apply monkeypatches
StreamingApp.refresh_devices = _better_refresh_devices
StreamingApp._list_dshow_cameras = _better_list_dshow_cameras


def _set_dark_title_bar(window: tk.Tk):
    """Enable dark title bar on Windows 10/11."""
    try:
        import platform
        if platform.system() != "Windows":
            return
        from ctypes import windll, c_int, byref, sizeof

        # For tkinter, we need to get the top-level window handle
        # winfo_id() returns a child window, so we use GetAncestor to get the top-level
        child_hwnd = window.winfo_id()
        # Get the top-level window by walking up the parent chain
        hwnd = windll.user32.GetAncestor(child_hwnd, 2)  # GA_ROOT = 2
        if not hwnd:
            hwnd = windll.user32.GetParent(child_hwnd)

        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 11) or 19 (Windows 10 older builds)
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        value = c_int(1)  # 1 = dark mode, 0 = light mode
        result = windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            byref(value),
            sizeof(value)
        )
        if result != 0:
            # Try the older Windows 10 attribute if the newer one fails
            DWMWA_USE_IMMERSIVE_DARK_MODE = 19
            windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_USE_IMMERSIVE_DARK_MODE,
                byref(value),
                sizeof(value)
            )
    except Exception:
        pass  # Silently fail on unsupported systems


def _apply_dark_theme(root: tk.Tk):
    """Apply a dark theme to the application."""
    # Define dark colors
    bg_dark = "#1e1e1e"
    bg_lighter = "#2d2d2d"
    fg_light = "#e0e0e0"
    fg_dim = "#a0a0a0"
    accent = "#3a8dde"
    entry_bg = "#3c3c3c"

    # Configure ttk styles - must set theme before configuring
    style = ttk.Style()
    style.theme_use("clam")

    # Configure root window after theme is set
    root.configure(bg=bg_dark)

    # Frame
    style.configure("TFrame", background=bg_dark)

    # Label
    style.configure("TLabel", background=bg_dark, foreground=fg_light)

    # Button
    style.configure("TButton",
                    background=bg_lighter,
                    foreground=fg_light,
                    borderwidth=1,
                    focuscolor=accent)
    style.map("TButton",
              background=[("active", accent), ("pressed", "#2a6db0")],
              foreground=[("active", "#ffffff")])

    # Entry
    style.configure("TEntry",
                    fieldbackground=entry_bg,
                    foreground=fg_light,
                    insertcolor=fg_light,
                    borderwidth=1)

    # Combobox
    style.configure("TCombobox",
                    fieldbackground=entry_bg,
                    background=bg_lighter,
                    foreground=fg_light,
                    arrowcolor=fg_light,
                    borderwidth=1)
    style.map("TCombobox",
              fieldbackground=[("readonly", entry_bg)],
              selectbackground=[("readonly", accent)],
              selectforeground=[("readonly", "#ffffff")])

    # Configure the dropdown listbox colors (requires option_add)
    root.option_add("*TCombobox*Listbox.background", entry_bg)
    root.option_add("*TCombobox*Listbox.foreground", fg_light)
    root.option_add("*TCombobox*Listbox.selectBackground", accent)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")


def main():
    if tk is None:
        print("Tkinter not available in this Python environment.")
        sys.exit(1)

    # Set AppUserModelID for proper taskbar icon on Windows
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WebcamRTSP.Streamer")
        except Exception:
            pass

    root = tk.Tk()

    # Hide window during setup to prevent visual glitches
    root.withdraw()

    # Apply dark theme
    _apply_dark_theme(root)

    # Set window icon
    script_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
    icon_path = os.path.join(script_dir, "logo.ico")
    if os.path.isfile(icon_path):
        try:
            root.iconbitmap(icon_path)
        except Exception:
            pass

    app = StreamingApp(root)
    root.protocol("WM_DELETE_WINDOW", root.destroy)

    # Apply dark title bar before showing window (Windows 10/11)
    _set_dark_title_bar(root)

    # Center window on screen
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 2
    root.geometry(f"+{x}+{y}")

    # Show window after all setup is complete
    root.deiconify()
    root.lift()
    root.focus_force()

    root.mainloop()


if __name__ == "__main__":
    main()
