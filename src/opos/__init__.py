# SPDX-License-Identifier: GPL-3.0-or-later
import sys

if sys.version_info < (3, 11):
    sys.exit('Old Phone OS Interface needs Python 3.11 or newer; this is Python %d.%d. Install Python 3.11 or newer and run the command again with it.' % sys.version_info[:2])
