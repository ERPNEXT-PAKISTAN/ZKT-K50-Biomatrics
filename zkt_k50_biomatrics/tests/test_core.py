import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from zkt_k50_biomatrics.normalization import (
    clean_event,
    event_key,
    normalize_time,
    punch_type,
    validate_coordinates,
)
from zkt_k50_biomatrics.drivers import DeviceError, get_driver
from zkt_k50_biomatrics.edge_agent.outbox import Outbox
from zkt_k50_biomatrics.edge_agent.client import Client
from zkt_k50_biomatrics.edge_agent.cli import load_config, save_credential, select_records

EVENT = {"user_id": "7", "timestamp": "2026-10-01T09:00:00", "punch": "0", "uid": "12"}


class NormalizationTests(unittest.TestCase):
    def test_karachi_conversion(self):
        self.assertEqual(normalize_time(EVENT["timestamp"], "Asia/Karachi", "UTC"), datetime(2026, 10, 1, 4))

    def test_same_timezone_no_double_conversion(self):
        self.assertEqual(
            normalize_time(EVENT["timestamp"], "Asia/Karachi", "Asia/Karachi"), datetime(2026, 10, 1, 9)
        )

    def test_aware_timestamp(self):
        self.assertEqual(
            normalize_time("2026-10-01T09:00:00+05:00", "UTC", "Asia/Karachi"), datetime(2026, 10, 1, 9)
        )

    def test_dst_requires_review(self):
        for timestamp in ("2026-11-01T01:30:00", "2026-03-08T02:30:00"):
            with self.assertRaises(ValueError):
                normalize_time(timestamp, "America/New_York", "UTC")

    def test_device_scoped_fingerprint(self):
        self.assertNotEqual(event_key("A", EVENT), event_key("B", EVENT))

    def test_native_record_identity(self):
        self.assertNotEqual(
            event_key("A", dict(EVENT, native_id="1")), event_key("A", dict(EVENT, native_id="2"))
        )

    def test_uid_is_not_record_id(self):
        self.assertEqual(event_key("A", EVENT), event_key("A", dict(EVENT, uid="99")))

    def test_second_precision(self):
        self.assertEqual(
            event_key("A", EVENT), event_key("A", dict(EVENT, timestamp="2026-10-01 09:00:00.000000"))
        )

    def test_punch_config_and_unknown(self):
        mappings = [{"punch_code": "4", "log_type": "OUT"}]
        self.assertEqual(punch_type("4", mappings), "OUT")
        self.assertEqual(punch_type("0", mappings), "Unknown")

    def test_sanitized_payload(self):
        self.assertEqual(clean_event(dict(EVENT, fingerprint="SECRET", password="SECRET")), EVENT)

    def test_invalid_events(self):
        for event in ({}, [], dict(EVENT, timestamp="bad"), dict(EVENT, user_id="x" * 141)):
            with self.assertRaises((ValueError, TypeError)):
                clean_event(event)

    def test_coordinates(self):
        self.assertEqual(validate_coordinates(24.8, 67.1), (24.8, 67.1))
        for values in [(0, 0), (91, 50), (None, 4), (float("nan"), 4)]:
            with self.assertRaises(ValueError):
                validate_coordinates(*values)

    def test_cutoff(self):
        device = {
            "site_timezone": "Asia/Karachi",
            "device_timezone": "Asia/Karachi",
            "sync_start": "2026-10-01 10:00:00",
        }
        self.assertEqual(select_records(device, [EVENT]), [])


class DriverTests(unittest.TestCase):
    def test_registry_rejects_unimplemented_driver(self):
        with self.assertRaises(ValueError):
            get_driver("adms", {})

    def test_tcp_and_udp_options(self):
        for transport in ("TCP", "UDP"):
            with patch("zk.ZK") as zk:
                driver = get_driver(
                    "pyzk",
                    {
                        "host": "device.invalid",
                        "communication_key": "9",
                        "transport": transport,
                        "timeout": 15,
                        "omit_ping": True,
                    },
                )
                driver.connect()
                self.assertEqual(zk.call_args.kwargs["force_udp"], transport == "UDP")
                self.assertEqual(zk.call_args.kwargs["password"], 9)
                self.assertTrue(zk.call_args.kwargs["ommit_ping"])
                driver.close_connection()
                zk.return_value.connect.return_value.disconnect.assert_called_once()

    def test_connection_failures_redacted(self):
        for exception in (TimeoutError("secret key"), RuntimeError("password=99"), ConnectionError("secret")):
            with patch("zk.ZK") as zk:
                zk.return_value.connect.side_effect = exception
                with self.assertRaises(DeviceError) as caught:
                    get_driver("pyzk", {"host": "device.invalid"}).connect()
                self.assertNotIn("secret", str(caught.exception))
                self.assertNotIn("password=99", str(caught.exception))

    def test_attendance_not_deleted(self):
        conn = MagicMock()
        conn.get_attendance.return_value = [
            SimpleNamespace(user_id="7", uid=12, timestamp=datetime(2026, 10, 1, 9), punch=0)
        ]
        driver = get_driver("pyzk", {})
        driver.connection = conn
        self.assertEqual(driver.get_attendance_records(), [EVENT])
        driver.close_connection()
        conn.clear_attendance.assert_not_called()
        conn.disable_device.assert_not_called()

    def test_users_exclude_secrets(self):
        conn = MagicMock()
        conn.get_users.return_value = [
            SimpleNamespace(user_id="7", uid=12, name="Person", password="SECRET", card=99)
        ]
        driver = get_driver("pyzk", {})
        driver.connection = conn
        self.assertEqual(driver.get_users(), [{"user_id": "7", "uid": "12", "name": "Person"}])


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "outbox.sqlite"
        self.box = Outbox(self.path)

    def tearDown(self):
        self.box.close()
        self.temp.cleanup()

    def test_restart_persistence_and_idempotency(self):
        self.box.put("A", [EVENT, EVENT])
        self.box.close()
        self.box = Outbox(self.path)
        self.assertEqual(len(self.box.batch("A")), 1)
        self.assertEqual(self.box.status(), {"pending": 1})

    def test_partial_acknowledgements(self):
        self.box.put("A", [EVENT, dict(EVENT, user_id="8")])
        batch = self.box.batch("A")
        self.box.acknowledge(batch, [{"index": 0, "key": batch[0][0], "accepted": True}])
        self.assertEqual(self.box.status(), {"delivered": 1, "pending": 1})
        self.assertEqual(self.box.batch("A"), [])  # rejected event backs off

    def test_wrong_key_never_discards(self):
        self.box.put("A", [EVENT])
        batch = self.box.batch("A")
        self.box.acknowledge(batch, [{"index": 0, "key": "wrong", "accepted": True}])
        self.assertEqual(self.box.status(), {"pending": 1})

    def test_backoff_retains_rows(self):
        self.box.put("A", [EVENT])
        batch = self.box.batch("A")
        self.box.failure([batch[0][0]])
        self.assertEqual(self.box.batch("A"), [])
        self.assertEqual(self.box.status(), {"pending": 1})

    def test_devices_isolated(self):
        self.box.put("A", [EVENT])
        self.box.put("B", [EVENT])
        self.assertEqual(len(self.box.batch("A")), 1)
        self.assertEqual(len(self.box.batch("B")), 1)

    def test_https_required(self):
        for url in (
            "http://example.invalid",
            "https://user:pass@example.invalid",
            "https://example.invalid?token=x",
        ):
            with self.assertRaises(ValueError):
                Client(url, "secret")

    def test_transport_redacts_errors(self):
        client = Client("https://example.invalid", "secret")
        with patch.object(client.session, "post") as post:
            post.return_value.status_code = 403
            with self.assertRaises(RuntimeError) as caught:
                client.call("heartbeat")
            self.assertNotIn("secret", str(caught.exception))
            self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_protected_credential_file(self):
        path = Path(self.temp.name) / "credential"
        save_credential(path, "test-token")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.read_text(), "test-token")

    def test_config_permissions(self):
        import json

        path = Path(self.temp.name) / "config.json"
        path.write_text(json.dumps({"credential_file": str(Path(self.temp.name) / "credential")}))
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            load_config(path)
        path.chmod(0o600)
        self.assertIn("credential_file", load_config(path))


class OfflineRecoveryTests(unittest.TestCase):
    def test_outage_reboot_and_recovery(self):
        import json
        from zkt_k50_biomatrics.edge_agent.cli import run

        device = {
            "name": "A",
            "port": 4370,
            "transport": "TCP",
            "timeout": 15,
            "omit_ping": True,
            "driver": "pyzk",
            "live_sync_enabled": 1,
            "polling_interval": 300,
            "site_timezone": "Asia/Karachi",
            "device_timezone": "Asia/Karachi",
            "sync_start": "2020-01-01 00:00:00",
        }
        with tempfile.TemporaryDirectory() as folder:
            outbox = Path(folder) / "queue.sqlite"
            credential = Path(folder) / "credential"
            save_credential(credential, "agent:secret")
            Path(str(outbox) + ".devices.json").write_text(json.dumps([device]))
            config = {
                "site_url": "https://example.invalid",
                "credential_file": str(credential),
                "outbox": str(outbox),
                "devices": {"A": {"host": "device.invalid"}},
            }
            driver = MagicMock()
            driver.__enter__.return_value.get_attendance_records.return_value = [EVENT]
            with patch("zkt_k50_biomatrics.edge_agent.cli.driver_for", return_value=driver):
                with patch.object(Client, "call", side_effect=ConnectionError("offline")):
                    run(config, once=True)
                    run(config, once=True)  # restart while cloud remains unavailable
                box = Outbox(outbox)
                self.assertEqual(box.status(), {"pending": 1})
                box.close()

                def recovered(method, **kwargs):
                    if method == "heartbeat":
                        return {"devices": [device], "commands": []}
                    return {
                        "acknowledgements": [
                            {"index": i, "key": event_key("A", event), "accepted": True}
                            for i, event in enumerate(kwargs["events"])
                        ]
                    }

                with patch.object(Client, "call", side_effect=recovered):
                    run(config, once=True)
                box = Outbox(outbox)
                self.assertEqual(box.status(), {"delivered": 1})
                box.close()


if __name__ == "__main__":
    unittest.main()
