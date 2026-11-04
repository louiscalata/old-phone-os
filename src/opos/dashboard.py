# SPDX-License-Identifier: GPL-3.0-or-later
from opos.power import feature_drain, plan_reason, time_left_words, total_drain
import re
from opos.eol import is_worn
# A daily goal counts as reached for 8 hours after its time, then targets the same time the next day.
GOAL_GRACE_H = 8.0
_REDACT_RE = re.compile(r'\b(AI|model|Governor|assistant)\b', re.IGNORECASE)
def _redact(s):
    return _REDACT_RE.sub('...', s)
def _format_goal_12h(goal_str):
    """'16:00' reads '4 PM'; a trailing word stays, so '06:00 tomorrow' reads '6 AM tomorrow'."""
    hhmm, _, day = goal_str.partition(" ")
    h_str, m_str = hhmm.split(":")
    h = int(h_str)
    m = int(m_str)
    ampm = "PM" if h >= 12 else "AM"
    h12 = h % 12
    if h12 == 0:
        h12 = 12
    words = f"{h12} {ampm}" if m == 0 else f"{h12}:{m:02d} {ampm}"
    return f"{words} {day}" if day else words
def _format_time_12h(sec):
    sec = int(sec)
    total_minutes = sec // 60
    hour_24 = (total_minutes // 60) % 24
    minute = total_minutes % 60
    ampm = "PM" if hour_24 >= 12 else "AM"
    h12 = hour_24 % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{minute:02d} {ampm}"
class Dashboard:
    def __init__(self, tasks, history_len=60):
        self.tasks = tasks
        self._history_len = history_len
        self.goal = None
        self.in_use = None
        self.undone = set()
        self.history = []
        self._times = []
        self.events = []
        self._last_paused = ()
        self.paused_names = ()
        self._last_record_minute = None
        self._goal_armed = False
    def set_goal(self, hhmm, clock_s=None):
        if hhmm is None or hhmm == "":
            self.goal = None
            self._goal_armed = False
            return
        try:
            h_str, m_str = hhmm.split(":")
            h = int(h_str)
            m = int(m_str)
        except Exception:
            raise ValueError
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        self.goal = f"{h:02d}:{m:02d}"
        # A goal set inside its own grace window means the next one, so it is not reached yet.
        # Without a clock, the goal counts as set before its time.
        self._goal_armed = clock_s is None or self._since_goal_s(clock_s) >= GOAL_GRACE_H * 3600
    def task_name(self, name):
        """The task a paused-row id ("#2") or name refers to; ValueError when there is none."""
        names = {t.name for t in self.tasks}
        if isinstance(name, str) and re.match(r'^#\d+$', name):
            try:
                idx = int(name[1:])
                if 0 <= idx < len(self.tasks):
                    return self.tasks[idx].name
                raise ValueError
            except Exception:
                raise ValueError
        if name in names:
            return name
        raise ValueError
    def undo(self, name):
        """Ask for a paused task back. The final decision honours it until the mode changes,
        and never against a hard floor."""
        real_name = self.task_name(name)
        self.undone.add(real_name)
        self.events.append(f"{real_name} turned back on")
        if len(self.events) > 20:
            self.events = self.events[-20:]
    def record(self, level, clock_s=None, *, changed=False):
        if clock_s is None:
            self.history.append(level)
            self._times.append(None)
            n = self._history_len
            if n > 0:
                if len(self.history) > n:
                    self.history = self.history[-n:]
                    self._times = self._times[-n:]
            else:
                self.history = []
                self._times = []
            return
        if self._times and self._times[-1] is not None and clock_s < self._times[-1]:
            self.reset_history(level)
        minute = int(clock_s // 60)
        if minute != self._last_record_minute or changed:
            self.history.append(level)
            self._times.append(clock_s)
            self._last_record_minute = minute
            n = self._history_len
            if n > 0:
                if len(self.history) > n:
                    self.history = self.history[-n:]
                    self._times = self._times[-n:]
            else:
                self.history = []
                self._times = []
            cutoff = clock_s - 3600
            while self._times and self._times[0] is not None and self._times[0] < cutoff:
                self.history.pop(0)
                self._times.pop(0)
    def reset_history(self, level):
        n = self._history_len
        if n == 0:
            self.history = []
            self._times = []
        else:
            self.history = [level]
            self._times = [None]
        self._last_record_minute = None
    def _since_goal_s(self, clock_s):
        h_str, m_str = self.goal.split(":")
        return (clock_s - int(h_str) * 3600 - int(m_str) * 60) % 86400
    def goal_reached(self, clock_s):
        """True in the grace window, once the clock was seen before the goal time (armed)."""
        if not self.goal or clock_s is None:
            return False
        if self._since_goal_s(clock_s) >= GOAL_GRACE_H * 3600:
            self._goal_armed = True
            return False
        return self._goal_armed
    def hours_to_goal(self, clock_s):
        """Hours until the daily goal; None means no goal or goal reached."""
        if not self.goal or clock_s is None or self.goal_reached(clock_s):
            return None
        h_str, m_str = self.goal.split(":")
        goal_sec = int(h_str) * 3600 + int(m_str) * 60
        delta = goal_sec - clock_s
        if delta <= 0:  # 0 here means a goal set at its own time: the next one is a day away.
            delta += 86400
        return delta / 3600.0
    def set_in_use(self, name):
        if name is None or name == "":
            self.in_use = None
            return
        if not isinstance(name, str):
            raise ValueError
        names = {t.name for t in self.tasks}
        if name not in names:
            raise ValueError
        self.in_use = name
        self.undone.discard(name)

    def view(self, level, mode, charging, health, clock_s, visible, reason, deferred=(), steps=(), drain_per_h=None):
        """Render the final decision; the panel never adds or removes pauses on its own.

        deferred and steps are the final decision's paused tasks and saving steps;
        drain_per_h is its drain (computed from them when not given).
        """
        hours = self.hours_to_goal(clock_s)
        reached = self.goal_reached(clock_s)
        paused_set = set(deferred)
        paused_names = [t.name for t in self.tasks if t.name in paused_set]
        steps = tuple(steps)
        self.paused_names = tuple(paused_names)
        prev = set(self._last_paused or ())
        curr = set(paused_names)
        newly = curr - prev
        removed = prev - curr
        if newly or removed:
            for n in newly:
                self.events.append(f"{n} paused")
            for n in removed:
                self.events.append(f"{n} back on")
            self._last_paused = tuple(paused_names)
            if len(self.events) > 20:
                self.events = self.events[-20:]
        clamped = max(0.0, min(100.0, level))
        width = round(clamped, 1)
        if level >= 100:
            tone = "full"
        elif charging:
            tone = "charging"
        elif level < 20:
            tone = "low"
        else:
            tone = "normal"
        percent = round(level)
        drain_total = total_drain(self.tasks, paused=paused_names, steps=steps, health=health) if drain_per_h is None else drain_per_h
        hours_left_corrected = float('inf') if drain_total <= 0 else level / drain_total
        if charging:
            meets_goal = True
        elif hours is None:
            meets_goal = False
        else:
            meets_goal = hours_left_corrected >= hours
        # Calls and messages treat a battery shown as 0% as empty, charging or not, so the goal does too.
        if reached:
            meets_goal = percent > 0
        goal_note = ""
        calls_until = ""
        if clock_s is not None and hours_left_corrected < float('inf'):
            end_seconds = int(round((clock_s + hours_left_corrected * 3600) / 60)) * 60
            calls_until = _format_time_12h(end_seconds % 86400)
        if reached and not charging and percent > 0:
            goal_note = f"Made it to {_format_goal_12h(self.goal)}. Calls and messages stay on"
        elif not charging and self.goal and hours is not None and not meets_goal:
            if clock_s is not None and hours_left_corrected != float('inf') and hours_left_corrected == hours_left_corrected:
                goal_words = _format_goal_12h(self.goal)
                goal_note = f"Won't last until {goal_words}, calls and messages until ~{calls_until}"
        time_left = "Charging" if charging else time_left_words(hours_left_corrected)
        in_use = self.in_use if self.in_use and self.in_use not in paused_set else None
        saving_for = ["calls", "messages"]
        if in_use:
            saving_for.append(in_use)
        if any(t.name == "alarms" for t in self.tasks):
            saving_for.append("alarms")
        name_to_idx = {t.name: i for i, t in enumerate(self.tasks)}
        paused_list = [{"name": n, "id": f"#{name_to_idx[n]}"} for n in paused_names]
        if clock_s is None:
            relevant = self.history
        else:
            cutoff = clock_s - 3600
            relevant = [lvl for lvl, t in zip(self.history, self._times) if t is None or t >= cutoff]
        history_out = [round(level, 1) for level in relevant]
        drain = feature_drain(steps, health)
        total = sum(drain.values())
        features_out = []
        for f, v in drain.items():
            name = "helper" if f == "model" else f
            share = round(v / max(total, 1e-9) * 100)
            features_out.append({"name": name, "per_hour": round(v, 2), "share": share})
        if charging and not paused_names:
            headline = "Charging: everything back on"
        elif paused_names or steps:
            headline = "Saving power for " + ", ".join(saving_for)
        else:
            headline = "Everything on"
        out = {
            "meter": {"width": width, "tone": tone, "label": f"Battery {round(level)} percent"},
            "percent": percent,
            "time_left": time_left,
            "hours_left": hours_left_corrected if hours_left_corrected < float('inf') else None,
            "calls_until": calls_until,
            "goal": self.goal or "",
            "goal_met": meets_goal,
            "saving_for": saving_for,
            "paused": paused_list,
            "events": self.events[-8:],
            "history": history_out,
            "features": features_out,
            "steps": list(steps),
            "headline": headline,
            "goal_note": goal_note,
            "worn_note": "Battery is worn, keeping a reserve for calls" if is_worn(health) and not charging else "",
        }
        if visible:
            if reached:
                plan_words = "Goal time reached"
            elif charging:
                plan_words = "Charging" if paused_names else "Charging: everything back on"
            elif hours is None:
                plan_words = "No goal set"
            else:
                plan_words = plan_reason(steps, paused_names, meets_goal, in_use)
            out["why"] = reason + "; " + plan_words
        else:
            for item in out["paused"]:
                item["name"] = _redact(item["name"])
            out["events"] = [_redact(e) for e in out["events"]]
            out["saving_for"] = [_redact(s) for s in out["saving_for"]]
            out["headline"] = _redact(out["headline"])
            out["goal_note"] = _redact(out["goal_note"])
            out["worn_note"] = _redact(out["worn_note"])
            for f in out["features"]:
                f["name"] = _redact(f["name"])
        return out
