"""Exercise transaction completion and cancellation without privileged services."""
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


class Variant:
    def __init__(self, *args):
        self.value = args[-1]

    def unpack(self):
        return self.value


class BusError(Exception):
    @property
    def message(self):
        return str(self)


def transaction_module():
    gi = ModuleType("gi")
    gi.require_version = lambda *_: None
    repository = ModuleType("gi.repository")
    repository.Gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(ALLOW_INTERACTIVE_AUTHORIZATION=1))
    repository.GLib = SimpleNamespace(Variant=Variant, Error=BusError)
    spec = importlib.util.spec_from_file_location("store_transaction_test", Path(__file__).resolve().parents[1] / "src/typix_store/transactions.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}):
        spec.loader.exec_module(module)
    return module


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.module = transaction_module()
        self.progress, self.finished = Mock(), Mock()
        self.tx = self.module.PackageTransaction(self.progress, self.finished)
        self.tx.connection = Mock()
        self.tx.path = "/transaction/1"

    def signal(self, name, *values):
        self.tx._signal(None, None, None, None, name, Variant(values))

    def test_cancel_never_sent_during_commit(self):
        self.assertFalse(self.tx.cancel())
        self.tx.connection.call.assert_not_called()

    def test_cancel_uses_daemon_api_when_allowed(self):
        self.tx.allow_cancel = True
        self.assertTrue(self.tx.cancel())
        self.assertEqual(self.tx.connection.call.call_args.args[3], "Cancel")
        self.assertFalse(self.tx.allow_cancel)
        self.assertFalse(self.tx.cancel())
        self.finished.assert_not_called()

    def test_error_followed_by_success_signal_still_fails(self):
        self.signal("ErrorCode", 1, "authorization denied")
        self.signal("Finished", 1, 0)
        self.finished.assert_called_once_with(False, "authorization denied", [])

    def test_finished_and_destroy_complete_once(self):
        self.signal("Finished", 1, 0)
        self.signal("Destroy")
        self.finished.assert_called_once_with(True, "", [])

    def test_method_timeout_does_not_terminate_or_release_transaction(self):
        self.tx.connection.call_finish.side_effect = BusError("Operation timed out")
        self.tx._started(self.tx.connection, None)
        self.assertFalse(self.tx.finished)
        self.finished.assert_not_called()
        self.tx.connection.signal_unsubscribe.assert_not_called()

    def test_authorization_denial_is_reported(self):
        self.tx.connection.call_finish.side_effect = BusError("Not authorized")
        self.tx._started(self.tx.connection, None)
        self.assertTrue(self.tx.finished)
        self.assertIn("Not authorized", self.finished.call_args.args[1])

    def test_lost_daemon_reports_uncertain_state(self):
        self.tx._owner_changed(None, None, None, None, None, Variant((self.module.SERVICE, ":1.20", "")))
        self.assertFalse(self.finished.call_args.args[0])
        self.assertIn("核对安装状态", self.finished.call_args.args[1])

    def test_cancel_rejected_by_daemon_does_not_fake_completion(self):
        self.tx.connection.call_finish.side_effect = BusError("Cannot cancel")
        self.tx._cancel_done(self.tx.connection, None)
        self.finished.assert_not_called()
        self.assertIn("不可取消", self.progress.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
