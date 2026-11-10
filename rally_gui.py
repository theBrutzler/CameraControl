"""Rally Camera Control – GUI zur Steuerung der Logitech Rally Camera.

* Pan / Tilt / Zoom, Fokus, Belichtung, Weißabgleich, Bildeinstellungen (UVC)
* RightSight (automatische Gesichtsverfolgung) ein-/ausschalten
  -> RightSight läuft als Host-Prozess (RightSightService.exe) von Logi Sync.
     "Aus" friert den Prozess ein (Suspend), "An" setzt ihn fort. Benötigt Admin-Rechte.
* Presets (Positionen) speichern / abrufen
"""
import ctypes
import json
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import psutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uvc import Camera  # noqa: E402

FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
PRESET_FILE = os.path.join(APP_DIR, "presets.json")
RS_NAME = "rightsightservice.exe"


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def restart_as_admin():
    if FROZEN:
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, None, None, 1)
        return
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, f'"{os.path.abspath(__file__)}"', None, 1)


# --------------------------------------------------------------------------- RightSight
def rs_processes():
    out = []
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower() == RS_NAME:
            out.append(p)
    return out


def rs_state():
    """'missing' | 'running' | 'frozen'"""
    procs = rs_processes()
    if not procs:
        return "missing"
    try:
        if all(p.status() == psutil.STATUS_STOPPED for p in procs):
            return "frozen"
    except psutil.Error:
        pass
    return "running"


def rs_set(freeze):
    for p in rs_processes():
        p.suspend() if freeze else p.resume()


# --------------------------------------------------------------------------- GUI
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Rally Camera Control")
        self.resizable(False, False)
        self.cam = None
        self.rows = {}          # (kind, prop) -> dict(var, auto, scale, lbl, rng)
        self.dragging = False
        self.guard = False
        self.presets = self.load_presets()
        self.keep_off = tk.BooleanVar(value=False)

        self.build_top()
        self.build_rightsight()
        self.build_motion()
        self.build_image()
        self.build_presets()
        self.connect()
        self.after(1000, self.tick)

    # ---------- Aufbau
    def build_top(self):
        f = ttk.Frame(self, padding=8)
        f.pack(fill="x")
        ttk.Label(f, text="Kamera:").pack(side="left")
        from pygrabber.dshow_graph import FilterGraph
        names = FilterGraph().get_input_devices()
        self.cam_var = tk.StringVar(value=next((n for n in names if "rally" in n.lower()), names[0] if names else ""))
        cb = ttk.Combobox(f, textvariable=self.cam_var, values=names, state="readonly", width=34)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.connect())
        self.status = ttk.Label(f, text="", foreground="gray")
        self.status.pack(side="right")

    def build_rightsight(self):
        f = ttk.LabelFrame(self, text="Automatische Gesichtsverfolgung (RightSight)", padding=8)
        f.pack(fill="x", padx=8, pady=4)
        self.rs_lbl = tk.Label(f, text="…", font=("Segoe UI", 11, "bold"), width=26, anchor="w")
        self.rs_lbl.grid(row=0, column=0, sticky="w")
        self.rs_btn = ttk.Button(f, text="…", command=self.rs_toggle, width=22)
        self.rs_btn.grid(row=0, column=1, padx=6)
        ttk.Checkbutton(f, text="Immer aus halten (neue Instanzen automatisch einfrieren)",
                        variable=self.keep_off).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        if not is_admin():
            ttk.Button(f, text="Als Administrator neu starten (nötig zum Abschalten)",
                       command=self.admin_restart).grid(row=2, column=0, columnspan=2, sticky="we", pady=(6, 0))

    def build_motion(self):
        f = ttk.LabelFrame(self, text="Bewegung", padding=8)
        f.pack(fill="x", padx=8, pady=4)
        pad = ttk.Frame(f)
        pad.grid(row=0, column=0, rowspan=3, padx=(0, 12))
        self.step = tk.IntVar(value=5)
        for txt, r, c, dp, dt in (("▲", 0, 1, 0, 1), ("◀", 1, 0, -1, 0), ("⌂", 1, 1, None, None),
                                  ("▶", 1, 2, 1, 0), ("▼", 2, 1, 0, -1)):
            b = ttk.Button(pad, text=txt, width=4)
            b.grid(row=r, column=c, padx=1, pady=1)
            if dp is None:
                b.configure(command=self.home)
            else:
                self.hold(b, lambda dp=dp, dt=dt: self.nudge(dp, dt))
        sf = ttk.Frame(pad)
        sf.grid(row=3, column=0, columnspan=3, pady=(6, 0))
        ttk.Label(sf, text="Schritt (°):").pack(side="left")
        ttk.Spinbox(sf, from_=1, to=30, width=4, textvariable=self.step).pack(side="left", padx=4)
        sl = ttk.Frame(f)
        sl.grid(row=0, column=1, sticky="we")
        self.slider(sl, "cam", "Pan", "Schwenken", 0, False)
        self.slider(sl, "cam", "Tilt", "Neigen", 1, False)
        self.slider(sl, "cam", "Zoom", "Zoom", 2, False)
        self.slider(sl, "cam", "Focus", "Fokus", 3, True)

    def build_image(self):
        f = ttk.LabelFrame(self, text="Bild", padding=8)
        f.pack(fill="x", padx=8, pady=4)
        self.slider(f, "cam", "Exposure", "Belichtung", 0, True)
        self.slider(f, "proc", "WhiteBalance", "Weißabgleich (K)", 1, True)
        self.slider(f, "proc", "Brightness", "Helligkeit", 2, False)
        self.slider(f, "proc", "Contrast", "Kontrast", 3, False)
        self.slider(f, "proc", "Saturation", "Sättigung", 4, False)
        self.slider(f, "proc", "Sharpness", "Schärfe", 5, False)
        self.slider(f, "proc", "Gain", "Gain", 6, False)
        self.slider(f, "proc", "BacklightCompensation", "Gegenlicht-Komp.", 7, False)
        ttk.Button(f, text="Alles auf Standard zurücksetzen", command=self.reset_all).grid(
            row=8, column=0, columnspan=4, sticky="we", pady=(8, 0))

    def build_presets(self):
        f = ttk.LabelFrame(self, text="Presets (Pan/Tilt/Zoom)", padding=8)
        f.pack(fill="x", padx=8, pady=(4, 8))
        self.preset_box = ttk.Combobox(f, state="readonly", width=24)
        self.preset_box.pack(side="left")
        ttk.Button(f, text="Abrufen", command=self.preset_recall).pack(side="left", padx=4)
        ttk.Button(f, text="Speichern…", command=self.preset_save).pack(side="left")
        ttk.Button(f, text="Löschen", command=self.preset_delete).pack(side="left", padx=4)
        self.refresh_presets()

    # ---------- generische Slider-Zeile
    def slider(self, parent, kind, prop, label, row, has_auto):
        ttk.Label(parent, text=label, width=17).grid(row=row, column=0, sticky="w")
        var = tk.DoubleVar()
        sc = ttk.Scale(parent, from_=0, to=1, variable=var, length=260,
                       command=lambda v, k=kind, p=prop: self.on_slide(k, p))
        sc.grid(row=row, column=1, padx=6, pady=2)
        sc.bind("<ButtonPress-1>", lambda e: setattr(self, "dragging", True))
        sc.bind("<ButtonRelease-1>", lambda e: setattr(self, "dragging", False))
        lbl = ttk.Label(parent, text="–", width=6)
        lbl.grid(row=row, column=2)
        avar = tk.BooleanVar()
        if has_auto:
            ttk.Checkbutton(parent, text="Auto", variable=avar,
                            command=lambda k=kind, p=prop: self.on_auto(k, p)).grid(row=row, column=3)
        self.rows[(kind, prop)] = dict(var=var, auto=avar, scale=sc, lbl=lbl, rng=None, has_auto=has_auto)

    # ---------- Kamera
    def connect(self):
        try:
            self.cam = Camera(self.cam_var.get())
        except Exception as e:
            self.cam = None
            self.status.config(text=f"Keine Verbindung: {e}", foreground="red")
            return
        self.status.config(text="verbunden", foreground="green")
        for (kind, prop), r in self.rows.items():
            rng = self.cam.range(kind, prop)
            r["rng"] = rng
            state = "normal" if rng else "disabled"
            r["scale"].configure(state=state)
            if rng:
                r["scale"].configure(from_=rng["min"], to=rng["max"])
        self.refresh()

    def refresh(self):
        if not self.cam:
            return
        self.guard = True
        try:
            for (kind, prop), r in self.rows.items():
                if not r["rng"]:
                    continue
                try:
                    val, flags = self.cam.get(kind, prop)
                except Exception:
                    continue
                r["var"].set(val)
                r["lbl"].config(text=str(val))
                auto = bool(flags & 1) and r["has_auto"]
                r["auto"].set(auto)
                r["scale"].configure(state="disabled" if auto else "normal")
        finally:
            self.guard = False

    def on_slide(self, kind, prop):
        if self.guard or not self.cam:
            return
        r = self.rows[(kind, prop)]
        step = r["rng"]["step"] or 1
        val = int(round(r["var"].get() / step) * step)
        r["lbl"].config(text=str(val))
        try:
            self.cam.set(kind, prop, val)
        except Exception as e:
            self.status.config(text=f"Fehler {prop}: {e}", foreground="red")

    def on_auto(self, kind, prop):
        r = self.rows[(kind, prop)]
        auto = r["auto"].get()
        try:
            self.cam.set(kind, prop, int(r["var"].get()), auto=auto)
        except Exception as e:
            self.status.config(text=f"Fehler {prop}: {e}", foreground="red")
        r["scale"].configure(state="disabled" if auto else "normal")

    def nudge(self, dp, dt):
        if not self.cam:
            return
        s = self.step.get()
        for prop, d in (("Pan", dp), ("Tilt", dt)):
            r = self.rows[("cam", prop)]
            if d and r["rng"]:
                v = r["var"].get() + d * s
                v = max(r["rng"]["min"], min(r["rng"]["max"], int(v)))
                r["var"].set(v)
                self.on_slide("cam", prop)

    def home(self):
        for prop in ("Pan", "Tilt"):
            r = self.rows[("cam", prop)]
            r["var"].set(0)
            self.on_slide("cam", prop)
        r = self.rows[("cam", "Zoom")]
        r["var"].set(r["rng"]["min"])
        self.on_slide("cam", "Zoom")

    def hold(self, btn, fn):
        job = {}

        def loop():
            fn()
            job["id"] = self.after(120, loop)

        def stop(_e=None):
            if "id" in job:
                self.after_cancel(job.pop("id"))

        btn.bind("<ButtonPress-1>", lambda e: loop())
        btn.bind("<ButtonRelease-1>", stop)
        btn.bind("<Leave>", stop)

    def reset_all(self):
        if not self.cam:
            return
        for (kind, prop), r in self.rows.items():
            if not r["rng"]:
                continue
            try:
                self.cam.set(kind, prop, r["rng"]["default"], auto=bool(r["rng"]["caps"] & 1) and r["has_auto"])
            except Exception:
                pass
        self.refresh()

    # ---------- RightSight
    def rs_toggle(self):
        st = rs_state()
        try:
            if st == "running":
                rs_set(True)
            elif st == "frozen":
                rs_set(False)
        except psutil.AccessDenied:
            if messagebox.askyesno("Administrator nötig",
                                   "Zum Abschalten/Einschalten von RightSight werden Admin-Rechte benötigt.\n"
                                   "Jetzt als Administrator neu starten?"):
                self.admin_restart()
        self.rs_update()

    def admin_restart(self):
        restart_as_admin()
        self.destroy()

    def rs_update(self):
        st = rs_state()
        if st == "running":
            self.rs_lbl.config(text="● RightSight AKTIV", fg="#c62828")
            self.rs_btn.config(text="Gesichtsverfolgung AUS")
        elif st == "frozen":
            self.rs_lbl.config(text="● RightSight AUS (eingefroren)", fg="#2e7d32")
            self.rs_btn.config(text="Gesichtsverfolgung AN")
        else:
            self.rs_lbl.config(text="○ RightSight läuft nicht", fg="#2e7d32")
            self.rs_btn.config(text="–", state="disabled")
            return
        self.rs_btn.config(state="normal")

    # ---------- Presets
    def load_presets(self):
        try:
            with open(PRESET_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def refresh_presets(self):
        self.preset_box["values"] = list(self.presets)
        if self.presets and not self.preset_box.get():
            self.preset_box.current(0)

    def preset_save(self):
        name = simpledialog.askstring("Preset", "Name:", parent=self)
        if not name:
            return
        self.presets[name] = {p: int(self.rows[("cam", p)]["var"].get()) for p in ("Pan", "Tilt", "Zoom")}
        with open(PRESET_FILE, "w", encoding="utf-8") as f:
            json.dump(self.presets, f, indent=2, ensure_ascii=False)
        self.refresh_presets()
        self.preset_box.set(name)

    def preset_recall(self):
        p = self.presets.get(self.preset_box.get())
        if not p:
            return
        for prop, v in p.items():
            self.rows[("cam", prop)]["var"].set(v)
            self.on_slide("cam", prop)

    def preset_delete(self):
        n = self.preset_box.get()
        if n in self.presets:
            del self.presets[n]
            with open(PRESET_FILE, "w", encoding="utf-8") as f:
                json.dump(self.presets, f, indent=2, ensure_ascii=False)
            self.preset_box.set("")
            self.refresh_presets()

    # ---------- Zyklus
    def tick(self):
        try:
            if self.keep_off.get() and rs_state() == "running":
                try:
                    rs_set(True)
                except psutil.AccessDenied:
                    self.keep_off.set(False)
                    self.status.config(text="Admin-Rechte nötig für 'Immer aus'", foreground="red")
            self.rs_update()
            if not self.dragging:
                self.refresh()
        finally:
            self.after(1000, self.tick)


if __name__ == "__main__":
    App().mainloop()
