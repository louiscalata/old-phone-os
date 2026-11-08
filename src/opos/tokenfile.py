# SPDX-License-Identifier: GPL-3.0-or-later
"""The page token on disk: made once, private, and kept across restarts (opos.web --token-file).

Without a file every start of the server draws a new token, so every page that is still open holds a stale one. A phone whose
server is restarted by systemd (Restart=always), by an update, or by "Restart helper" would show a dead page until someone
reloaded it. With a file the token is the same after a restart and an open page just goes on.
"""
import os

from opos import secure


def ensure(path):
    """Create the token file, mode 0600, if it does not exist. An existing file is never touched or replaced.

    The token is written to a private file beside the target first and put in place with a hard link, which fails when the
    target already exists: a reader never sees a half-written token, and a crash cannot leave an empty file that would make
    the server refuse to start on every restart. Where hard links are not available the file is created in place instead.
    Returns True when it created the file. Raises OSError when the directory is missing or cannot be written.
    """
    path = os.fspath(path)
    if os.path.lexists(path):
        return False
    token = (secure.new_token() + "\n").encode("ascii")
    tmp = "%s.%d.new" % (path, os.getpid())
    try:
        os.unlink(tmp)  # a start that had this pid and died between making the file and removing it left one behind
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(token)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        except OSError:  # no hard links on this file system
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                return False
            with os.fdopen(fd, "wb") as f:
                f.write(token)
        return True
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def usable(path, err):
    """Make the file if it is missing; True when there is a file at `path` afterwards.

    A phone whose state directory cannot be written (a full disk, a read-only mount) must still show its screen: the server
    then keeps a token for this run only, as it did before there was a token file, and says so on `err`. A file that exists
    but cannot be read, or holds a bad token, is not this case: the caller refuses to start on it.
    """
    try:
        ensure(path)
    except OSError as e:
        print("opos: cannot make the token file (%s); this run keeps its token in memory only" % type(e).__name__, file=err)
    return os.path.lexists(path)
