# SPDX-License-Identifier: GPL-3.0-or-later
import errno
import multiprocessing
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from opos import calllog, contacts_page, store
from opos.call_events import CallEvent

if os.name == "nt":
    import msvcrt
else:
    import fcntl

TIMEOUT = 10
CONTACT = {"action": "add", "name": "Synthetic Contact", "number": "2025550100"}
CALL = CallEvent("1".zfill(32), "started", "+12025550100", "outgoing", 1794391200.0, True)
ROWS = [{"value": "new"}]


def receive(connection):
    if not connection.poll(TIMEOUT):
        raise TimeoutError("the other process did not respond")
    return connection.recv()


def snapshot(name):
    if name == "contacts.json":
        data = contacts_page.snapshot()
        return data["people"], data["recovered"]
    if name == "calls.json":
        data = calllog.snapshot()
        return data["calls"], data["recovered"]
    return store.Store(os.environ["OPOS_STATE_DIR"]).read_jsonl(name)


def read(connection, name):
    lock = store._lock

    @contextmanager
    def wait_for_write(path):
        connection.send("read")
        if receive(connection) != "write":
            raise AssertionError("expected the writer to finish")
        with lock(path):
            yield

    with connection, patch.object(store, "_lock", wait_for_write):
        connection.send(snapshot(name))


def write(connection, root):
    replace = os.replace

    def replace_after_read(source, destination):
        connection.send("replace")
        if receive(connection) != "read":
            raise AssertionError("expected the reader to finish")
        replace(source, destination)

    with connection, patch.object(os, "replace", replace_after_read):
        store.Store(root).write_json("state.json", {"value": "new"})


def post_after_release(connection, data):
    clean_person = contacts_page._clean_person

    def clean_after_release(data, people, existing=None):
        connection.send("validate")
        if receive(connection) != "save":
            raise AssertionError("expected permission to save")
        return clean_person(data, people, existing)

    with connection, patch.object(contacts_page, "_clean_person", clean_after_release):
        connection.send(contacts_page.post(data))


def post_while_locked(connection, data):
    lock = store._lock

    @contextmanager
    def acquire(path):
        connection.send("acquire")
        with lock(path):
            yield

    with connection, patch.object(store, "_lock", acquire):
        connection.send(contacts_page.post(data))


class StoreRecoveryCoordinationTests(unittest.TestCase):
    @contextmanager
    def process(self, target, *args):
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        with parent, child:
            process = context.Process(target=target, args=(child, *args))
            process.start()
            child.close()
            try:
                yield parent
                process.join(TIMEOUT)
                self.assertEqual(process.exitcode, 0)
            finally:
                if process.is_alive():
                    process.kill()
                    process.join(TIMEOUT)
                process.close()

    def test_recovery_preserves_a_new_save(self):
        for name, damaged in (
            ("contacts.json", b"{invalid"),
            ("calls.json", b'{"schema":1,"calls":[{"id":"invalid"}]}'),
            ("rows.jsonl", b"\xff"),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root:
                path = Path(root, name)
                path.write_bytes(damaged)
                with patch.dict(os.environ, {"OPOS_STATE_DIR": root, "OPOS_CONFIG_DIR": root, "OPOS_APPS": "calls"}):
                    contacts_page.reset()
                    calllog.reset()
                    try:
                        with self.process(read, name) as connection:
                            self.assertEqual(receive(connection), "read")
                            if name == "contacts.json":
                                status, body = contacts_page.post(CONTACT)
                                self.assertEqual(status, 200)
                                self.assertEqual(body["people"][0]["name"], CONTACT["name"])
                            elif name == "calls.json":
                                calllog.record(CALL)
                                self.assertEqual(calllog.snapshot()["calls"][0]["id"], CALL.call_id)
                            else:
                                saved = store.Store(root)
                                rows, note = saved.read_jsonl(name)
                                self.assertEqual(rows, [])
                                self.assertTrue(note)
                                saved.write_jsonl(name, ROWS)
                            latest, _ = snapshot(name)
                            published = path.read_bytes()
                            backups = list(path.parent.glob(f"{name}.bad-*"))
                            self.assertEqual(len(backups), 1)
                            self.assertEqual(backups[0].read_bytes(), damaged)
                            connection.send("write")
                            self.assertEqual(receive(connection), (latest, ""))
                    finally:
                        contacts_page.reset()
                        calllog.reset()
                self.assertEqual(path.read_bytes(), published)
                self.assertEqual(list(path.parent.glob(f"{name}.bad-*")), backups)
                self.assertEqual(backups[0].read_bytes(), damaged)

    def assert_lock_held(self, path):
        sidecar = path.with_name(f".{path.name}.lock")
        fd = os.open(sidecar, os.O_RDONLY if os.name == "nt" else os.O_RDWR)
        try:
            with self.assertRaises(OSError) as blocked:
                if os.name == "nt":
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertIn(blocked.exception.errno, (errno.EACCES, errno.EAGAIN))
        finally:
            os.close(fd)

    def test_writer_holds_the_lock_at_replace(self):
        with tempfile.TemporaryDirectory() as root:
            saved = store.Store(root)
            path = saved.write_json("state.json", {"value": "old"})
            initial = path.read_bytes()
            with self.process(write, root) as connection:
                self.assertEqual(receive(connection), "replace")
                self.assertEqual(path.read_bytes(), initial)
                self.assert_lock_held(path)
                connection.send("read")
            self.assertEqual(saved.read_json(path.name, {}), ({"schema": 1, "value": "new"}, ""))
            self.assertEqual(path.read_bytes(), b'{"value":"new","schema":1}')
            self.assertFalse(list(path.parent.glob(f".{path.name}.*.tmp")))

    def post_concurrently(self, favorite):
        root = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"OPOS_STATE_DIR": root, "OPOS_CONFIG_DIR": root}))
        contacts_page.reset()
        self.addCleanup(contacts_page.reset)
        names = set()
        if favorite:
            for index in range(5):
                name = f"Favorite {index}"
                seed = dict(CONTACT, name=name, number=f"202555011{index}", favorite=True)
                self.assertEqual(contacts_page.post(seed)[0], 200)
                names.add(name)
        first = dict(CONTACT, favorite=favorite)
        second = dict(first, name="Another Contact", number="2025550101")
        with self.process(post_after_release, first) as first_connection:
            self.assertEqual(receive(first_connection), "validate")
            self.assert_lock_held(Path(root, "contacts.json"))
            with self.process(post_while_locked, second) as second_connection:
                self.assertEqual(receive(second_connection), "acquire")
                first_connection.send("save")
                status, body = receive(first_connection)
                self.assertEqual(status, 200)
                names.add(first["name"])
                self.assertEqual({person["name"] for person in body["people"]}, names)
                response = receive(second_connection)
        return names, second, response

    def test_concurrent_contacts_adds_preserve_both_saves(self):
        names, second, (status, body) = self.post_concurrently(favorite=False)
        self.assertEqual(status, 200)
        names.add(second["name"])
        self.assertEqual({person["name"] for person in body["people"]}, names)
        people = contacts_page.snapshot()["people"]
        self.assertEqual(len(people), 2)
        self.assertEqual({person["name"] for person in people}, names)

    def test_concurrent_favorite_adds_respect_the_limit(self):
        names, _, response = self.post_concurrently(favorite=True)
        self.assertEqual(response, (400, {"error": "favorites"}))
        people = contacts_page.snapshot()["people"]
        self.assertEqual(len(people), 6)
        self.assertEqual({person["name"] for person in people}, names)
        self.assertTrue(all(person["favorite"] for person in people))

    def test_valid_and_missing_reads_leave_the_directory_alone(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            saved = store.Store(folder / "missing")
            self.assertEqual(saved.read_json("state.json", None), (None, ""))
            self.assertFalse(saved.root.exists())
            path = folder / "state.json"
            path.write_bytes(b'{"schema":1,"value":42}')
            self.assertEqual(store.Store(folder).read_json(path.name, {}), ({"schema": 1, "value": 42}, ""))
            self.assertEqual(list(folder.iterdir()), [path])

    @unittest.skipIf(os.name == "nt", "Windows has different file mode semantics")
    def test_owner_can_reopen_the_lock_after_restrictive_creation(self):
        with tempfile.TemporaryDirectory() as root:
            saved = store.Store(root)
            previous = os.umask(0o777)
            try:
                path = saved.write_json("state.json", {"value": "old"})
                lock = path.with_name(f".{path.name}.lock")
                self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
                inode = lock.stat().st_ino
                lock.chmod(0)
                saved.write_json(path.name, {"value": "new"})
                self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
                self.assertEqual(lock.stat().st_ino, inode)
                self.assertEqual(saved.read_json(path.name, {}), ({"schema": 1, "value": "new"}, ""))
            finally:
                os.umask(previous)


if __name__ == "__main__":
    unittest.main()
