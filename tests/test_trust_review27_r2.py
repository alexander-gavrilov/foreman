"""Round 2 of the review of PR 27: each finding as a test."""
import errno
import json
import os
from unittest import mock

from tests.trustbase import TrustCase, read, write
from foreman import trust, trustfile  # noqa: E402


class FailureAfterRenameTest(TrustCase):
    """R2-1. When the entry is already in the file, a failure afterwards keeps the pending record: never an unrecorded
    entry that uninstall cannot find."""

    def test_a_record_update_that_fails_after_the_rename_leaves_the_entry_pending(self):
        write(self.agy, '{"trustedWorkspaces": []}\n')
        with mock.patch.object(trust, "_set_state", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            with self.assertRaises(trust.TrustRefused) as cm:
                trust.agy_add("/p/one", "proj", "project")
        self.assertIn("could not be checked or recorded", cm.exception.reason)
        self.assertNotIn("nothing was changed", cm.exception.reason)
        self.assertEqual(json.loads(read(self.agy))["trustedWorkspaces"], ["/p/one"])
        rows = self.record()
        self.assertEqual([r["state"] for r in rows], ["pending"], rows)
        lines = trust.remove_all()  # uninstall reports it (spec 5.5: a pending entry is reported, then dropped)
        self.assertTrue(any("left the agy-workspace /p/one" in l for l in lines), lines)
        self.assertEqual(self.record(), [])

    def test_a_read_back_that_fails_after_the_rename_leaves_the_entry_pending(self):
        write(self.agy, "{}")
        real_read, real_write = trustfile.read_bytes, trustfile.atomic_write
        done = {"renamed": False}

        def atomic_write(*a, **kw):
            real_write(*a, **kw)
            done["renamed"] = True

        def read_bytes(path):
            if done["renamed"]:  # the read-back after the rename
                raise OSError(errno.EIO, "I/O error")
            return real_read(path)
        with mock.patch.object(trustfile, "atomic_write", side_effect=atomic_write), \
                mock.patch.object(trustfile, "read_bytes", side_effect=read_bytes):
            with self.assertRaises(trust.TrustRefused):
                trust.agy_add("/p/one", "proj", "project")
        self.assertEqual([r["state"] for r in self.record()], ["pending"])
        self.assertIn("/p/one", read(self.agy))


class NoHardLinksTest(TrustCase):
    """R2-2. A file system without hard links still gets its backup."""

    def test_the_backup_is_made_without_os_link(self):
        write(self.agy, '{"a": 1}\n')
        with mock.patch.object(os, "link", side_effect=OSError(errno.EPERM, "Operation not permitted")):
            trust.agy_add("/p/one", "proj", "project")
        self.assertEqual(read(self.agy + trustfile.BACKUP_SUFFIX), '{"a": 1}\n')
        self.assertIn("/p/one", read(self.agy))
        self.assertEqual(sorted(f for f in os.listdir(self.agy_dir) if ".tmp" in f), [])

    def test_an_existing_backup_is_never_overwritten(self):
        write(self.agy, "{}")
        write(self.agy + trustfile.BACKUP_SUFFIX, "kept")
        with mock.patch.object(os, "link", side_effect=OSError(errno.EPERM, "no")):
            trust.agy_add("/p/one", "proj", "project")
        self.assertEqual(read(self.agy + trustfile.BACKUP_SUFFIX), "kept")
