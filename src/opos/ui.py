# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import time


def render(tick):
    return "\n".join(tick.screen)


class ScreenModem:
    def __init__(self):
        self.events = []
    def dial(self, number):
        self.events.append(("dial", number))
    def hang_up(self):
        self.events.append(("hang_up",))
    def send_sms(self, number, text):
        self.events.append(("sms", number, text))


def _grey(brightness):
    v = max(0, min(100, round(brightness))) * 255 // 100
    return f"#{v:02x}{v:02x}{v:02x}"


def _fg(brightness):
    v = max(0, min(100, round(brightness)))
    if v >= 46:
        return "#000000"
    return "#ffffff"


class PhoneApp:
    def __init__(self, root, visible=False):
        from opos.essentials import Essentials
        from opos.runtime import Runtime
        self.runtime = Runtime(visible=visible)
        self.modem = ScreenModem()
        self.ess = Essentials(self.modem)
        root.geometry("360x640")
        root.title("Old Phone OS Interface")
        self.screen = root.Label if hasattr(root, "Label") else None
        import tkinter as tk
        self.battery = tk.Scale(root, from_=0, to=100, orient="horizontal", label="Battery", command=lambda _v: self.refresh())
        self.battery.set(50)
        self.temp = tk.Scale(root, from_=15, to=60, orient="horizontal", label="Temp C", command=lambda _v: self.refresh())
        self.temp.set(25)
        self.display = tk.Label(root, font=("TkDefaultFont", 18), justify="left", anchor="nw")
        self.display.pack(fill="both", expand=True)
        self.battery.pack(fill="x")
        self.temp.pack(fill="x")
        self.number = tk.Entry(root)
        self.number.pack(fill="x")
        row = tk.Frame(root)
        row.pack(fill="x")
        tk.Button(row, text="Call", command=self.do_call).pack(side="left", expand=True, fill="x")
        tk.Button(row, text="Hang up", command=self.do_hangup).pack(side="left", expand=True, fill="x")
        self.message = tk.Entry(root)
        self.message.pack(fill="x")
        tk.Button(root, text="Send", command=self.do_send).pack(fill="x")
        self.status = tk.Label(root, anchor="w", justify="left")
        self.status.pack(fill="x")
        self.refresh()

    def refresh(self):
        tick = self.runtime.step(time.monotonic(), float(self.battery.get()), temp_c=float(self.temp.get()))
        text = render(tick)
        self.display.config(text=text, bg=_grey(tick.budget.screen_brightness), fg=_fg(tick.budget.screen_brightness))
        return tick

    def do_call(self):
        try:
            out = self.ess.call(self.number.get())
        except (ValueError, RuntimeError) as e:
            self.status.config(text=f"error: {e}" if str(e) else f"error: {type(e).__name__}")
        else:
            self.status.config(text=out)

    def do_hangup(self):
        try:
            self.ess.end_call()
        except (ValueError, RuntimeError) as e:
            self.status.config(text=f"error: {e}" if str(e) else f"error: {type(e).__name__}")
        else:
            self.status.config(text="ended")

    def do_send(self):
        try:
            msg = self.ess.send_message(self.number.get(), self.message.get())
        except (ValueError, RuntimeError) as e:
            self.status.config(text=f"error: {e}" if str(e) else f"error: {type(e).__name__}")
        else:
            self.status.config(text=f"sent to {msg.number}" if msg.sent else f"queued for {msg.number}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--visible", action="store_true")
    args = ap.parse_args(argv)
    try:
        import tkinter as tk
        root = tk.Tk()
    except (ImportError, tk.TclError) if "tk" in dir() else (ImportError, Exception):
        from opos.runtime import Runtime
        tick = Runtime(visible=False).step(time.monotonic(), 0.05, temp_c=25.0)
        print(render(tick))
        return 0
    except Exception:
        from opos.runtime import Runtime
        tick = Runtime(visible=False).step(time.monotonic(), 0.05, temp_c=25.0)
        print(render(tick))
        return 0
    PhoneApp(root, visible=args.visible)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
