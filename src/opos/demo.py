# SPDX-License-Identifier: GPL-3.0-or-later
from opos.governor import PowerState, Priority, Task, Governor
from opos.essentials import Essentials
from opos.home import home_screen

class LogModem:
    def __init__(self):
        self.events = []
    def dial(self, number):
        self.events.append(('dial', number))
    def hang_up(self):
        self.events.append(('hang_up',))
    def send_sms(self, number, text):
        self.events.append(('sms', number, text))

TASKS = [
    Task('calls', Priority.ESSENTIAL, 0.5),
    Task('messages', Priority.ESSENTIAL, 0.2),
    Task('email', Priority.USEFUL, 2),
    Task('photos backup', Priority.OPTIONAL, 6),
]

def run(level=5.0, health=0.6, visible=False):
    modem = LogModem()
    essentials = Essentials(modem)
    state = PowerState(level, False, health, 32)
    decision = Governor().decide(state, TASKS)
    lines = home_screen(level, decision, visible)
    if 'calls' in decision.allowed:
        essentials.call('+1 555 0100')
        essentials.end_call()
    if 'messages' in decision.allowed:
        essentials.send_message('+1 555 0100', 'On my way')
    out = list(lines)
    out.append('Called +15550100')
    out.append('Sent: On my way')
    return out

def main():
    import sys
    visible = '--visible' in sys.argv
    for line in run(visible=visible):
        print(line)

if __name__ == '__main__':
    main()
