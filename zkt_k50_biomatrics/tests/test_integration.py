"""Run on an isolated site only: bench --site TEST run-tests --app zkt_k50_biomatrics.

Uses real Frappe documents and real HRMS EmployeeCheckin validation, mocked hardware.
"""

from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.utils import now_datetime
from frappe.tests import IntegrationTestCase

from zkt_k50_biomatrics import api, agent_api
from zkt_k50_biomatrics.controllers import configuration_hash
from zkt_k50_biomatrics.engine import ingest_batch, inspect_event, persist_event, process_raw
from zkt_k50_biomatrics.jobs import preview_events
from zkt_k50_biomatrics.security import assigned_device, digest


class TestIntegration(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        frappe.set_user("Administrator")
        self.suffix = frappe.generate_hash(length=8)
        self.device = frappe.get_doc(
            {
                "doctype": "Biometric Device",
                "device_code": "TEST-" + self.suffix,
                "device_name": "Mocked K50",
                "enabled": 1,
                "live_sync_enabled": 0,
                "driver": "pyzk",
                "host": "device.invalid",
                "port": 4370,
                "transport": "TCP",
                "timeout": 15,
                "omit_ping": 1,
                "device_timezone": "Asia/Karachi",
                "polling_interval": 300,
                "connection_mode": "Direct",
                "sync_start": "2020-01-01 00:00:00",
                "punch_mappings": [{"punch_code": "4", "log_type": "IN"}],
                "geolocation_policy": "Fixed Coordinates",
                "latitude": 24.86,
                "longitude": 67.01,
            }
        ).insert()
        self.timestamp = now_datetime().replace(microsecond=0) - timedelta(days=2)
        # Device local time matches site timezone for fixture; normalize using explicit offset.
        from zoneinfo import ZoneInfo
        from frappe.utils import get_system_timezone

        aware = self.timestamp.replace(tzinfo=ZoneInfo(get_system_timezone())).astimezone(
            ZoneInfo("Asia/Karachi")
        )
        self.event = {"user_id": "100", "timestamp": aware.isoformat(), "punch": "4", "uid": "12"}

    def make_employee(self, inactive=False):
        for doctype, field, value in [
            ("Gender", "gender", "Male"),
            ("Warehouse Type", "name", "Transit"),
        ]:
            if not frappe.db.exists(doctype, value):
                frappe.get_doc({"doctype": doctype, field: value}).insert()
        name = "_Test ZKT Integration Company Complete"
        if not frappe.db.exists("Company", name):
            frappe.get_doc(
                {
                    "doctype": "Company",
                    "company_name": name,
                    "abbr": "ZKIC",
                    "country": "Pakistan",
                    "default_currency": "PKR",
                }
            ).insert()
        employee = frappe.get_doc(
            {
                "doctype": "Employee",
                "first_name": "ZKT Test " + self.suffix,
                "gender": "Male",
                "date_of_birth": "1990-01-01",
                "date_of_joining": "2019-01-01",
                "company": name,
                "status": "Left" if inactive else "Active",
                "relieving_date": "2020-01-01" if inactive else None,
            }
        ).insert()
        return employee

    def mapping(self, employee, device=None):
        return frappe.get_doc(
            {
                "doctype": "Biometric Employee Mapping",
                "device": (device or self.device).name,
                "biometric_user_id": "100",
                "employee": employee.name,
                "enabled": 1,
                "effective_from": "2020-01-01 00:00:00",
            }
        ).insert()

    def enable_live(self):
        self.device.previewed_at = now_datetime()
        self.device.preview_config_hash = configuration_hash(self.device)
        self.device.live_sync_enabled = 1
        self.device.save()

    def test_unmapped_raw_event_preserved(self):
        self.enable_live()
        name, created = persist_event(self.device, self.event)
        self.assertTrue(created)
        self.assertEqual(process_raw(name), "Unmapped")
        self.assertFalse(frappe.db.get_value("Biometric Raw Log", name, "employee_checkin"))

    def test_real_hrms_creation_and_idempotency(self):
        employee = self.make_employee()
        self.mapping(employee)
        self.enable_live()
        name, _ = persist_event(self.device, self.event)
        self.assertEqual(process_raw(name), "Created")
        raw = frappe.get_doc("Biometric Raw Log", name)
        checkin = frappe.get_doc("Employee Checkin", raw.employee_checkin)
        self.assertEqual(checkin.employee, employee.name)
        self.assertEqual(checkin.device_id, self.device.device_code)
        self.assertEqual(checkin.log_type, "IN")
        name2, created = persist_event(self.device, self.event)
        self.assertFalse(created)
        self.assertEqual(name2, name)
        self.assertEqual(process_raw(name), "Created")

    def test_unknown_punch_and_inactive_employee(self):
        self.mapping(self.make_employee())
        self.assertEqual(
            inspect_event(self.device, dict(self.event, punch="99"))["status"], "Requires Review"
        )
        employee = self.make_employee(inactive=True)
        frappe.db.set_value(
            "Biometric Employee Mapping", {"device": self.device.name}, "employee", employee.name
        )
        self.assertEqual(inspect_event(self.device, self.event)["status"], "Requires Review")

    def test_hrms_failure_keeps_raw_and_no_checkin(self):
        self.mapping(self.make_employee())
        self.enable_live()
        name, _ = persist_event(self.device, self.event)
        target = "hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field"
        with patch(target, side_effect=frappe.ValidationError("Outside permitted shift radius")):
            self.assertEqual(process_raw(name), "Failed")
        raw = frappe.get_doc("Biometric Raw Log", name)
        self.assertIn("shift radius", raw.processing_error)
        self.assertFalse(raw.employee_checkin)

    def test_overlapping_mapping_rejected(self):
        employee = self.make_employee()
        self.mapping(employee)
        with self.assertRaises(frappe.ValidationError):
            self.mapping(employee)

    def test_mapping_device_context(self):
        self.mapping(self.make_employee())
        other = frappe.copy_doc(self.device)
        other.device_code = "OTHER-" + self.suffix
        other.insert()
        self.assertEqual(inspect_event(other, self.event)["status"], "Unmapped")

    def test_dry_run_creates_no_raw_or_checkins(self):
        before = frappe.db.count("Biometric Raw Log")
        checkins = frappe.db.count("Employee Checkin")
        self.assertEqual(preview_events(self.device, [self.event])["counts"], {"Unmapped": 1})
        self.assertEqual(frappe.db.count("Biometric Raw Log"), before)
        self.assertEqual(frappe.db.count("Employee Checkin"), checkins)

    def test_no_live_without_preview(self):
        self.device.live_sync_enabled = 1
        with self.assertRaises(frappe.ValidationError):
            self.device.save()

    def test_missing_required_geolocation(self):
        self.mapping(self.make_employee())
        self.device.geolocation_policy = "Unavailable / Review"
        with patch.object(frappe.db, "get_single_value", return_value=1):
            result = inspect_event(self.device, self.event)
        self.assertEqual(result["status"], "Requires Review")

    def test_cutoff(self):
        self.device.sync_start = now_datetime()
        self.assertEqual(inspect_event(self.device, self.event)["status"], "Skipped")

    def test_permission_gates(self):
        with patch("frappe.get_roles", return_value=["Biometric Viewer"]):
            with self.assertRaises(frappe.PermissionError):
                api.start_operation(self.device.name)
            with self.assertRaises(frappe.PermissionError):
                api.create_enrollment("nonexistent")
        self.assertFalse(frappe.has_permission("Biometric Raw Log", "delete", user="Guest"))

    def test_agent_device_assignment(self):
        agent = frappe.get_doc(
            {
                "doctype": "Biometric Agent",
                "agent_code": "AGENT-" + self.suffix,
                "agent_name": "Test Agent",
                "enabled": 1,
            }
        ).insert()
        with self.assertRaises(frappe.PermissionError):
            assigned_device(agent, self.device.name)
        self.device.connection_mode = "Edge Agent"
        self.device.agent = agent.name
        self.device.save()
        self.assertEqual(assigned_device(agent, self.device.name).name, self.device.name)

    def test_enrollment_rotation_revocation(self):
        agent = frappe.get_doc(
            {
                "doctype": "Biometric Agent",
                "agent_code": "AGENT-" + self.suffix,
                "agent_name": "Test Agent",
                "enabled": 1,
            }
        ).insert()
        enrollment = api.create_enrollment(agent.name)
        self.assertIn("credential", enrollment)
        stored = frappe.get_doc("Biometric Agent", agent.name)
        self.assertEqual(stored.enrollment_hash, digest(enrollment["credential"].split(":", 1)[1]))
        with patch("zkt_k50_biomatrics.agent_api.authenticate_agent", return_value=stored):
            credential = agent_api.enroll()["credential"]
        stored.reload()
        self.assertEqual(stored.token_hash, digest(credential.split(":", 1)[1]))
        self.assertFalse(stored.enrollment_hash)
        api.revoke_agent(agent.name)
        stored.reload()
        self.assertTrue(stored.revoked)
        self.assertFalse(stored.token_hash)

    def test_partial_batch_acknowledgement(self):
        with patch("frappe.enqueue"):
            result = ingest_batch(self.device, [self.event, {"user_id": "1", "timestamp": "bad"}])
        self.assertTrue(result[0]["accepted"])
        self.assertFalse(result[1]["accepted"])
        self.assertTrue(frappe.db.exists("Biometric Raw Log", result[0]["raw_log"]))

    def test_direct_background_preview_and_live_run(self):
        from zkt_k50_biomatrics.jobs import queue_run, run_direct

        self.mapping(self.make_employee())
        driver = patch("zkt_k50_biomatrics.jobs.get_driver")
        with driver as factory, patch("frappe.enqueue"):
            factory.return_value.__enter__.return_value.get_attendance_records.return_value = [self.event]
            preview = queue_run(self.device, dry_run=True)
            before = frappe.db.count("Biometric Raw Log")
            run_direct(preview)
            run = frappe.get_doc("Biometric Sync Run", preview)
            self.assertEqual(run.status, "Success", run.error_summary)
            self.assertEqual(frappe.db.count("Biometric Raw Log"), before)
            self.device.reload()
            self.assertTrue(self.device.previewed_at)
            self.device.live_sync_enabled = 1
            self.device.save()
            live = queue_run(self.device, dry_run=False)
            run_direct(live)
            run = frappe.get_doc("Biometric Sync Run", live)
            self.assertEqual(run.status, "Success", run.error_summary)
            self.assertEqual(run.created_count, 1)
            self.assertEqual(run.events_received, 1)

    def test_actual_hrms_geolocation_radius_validation(self):
        if frappe.db.db_type == "sqlite":
            self.skipTest(
                "Installed HRMS shift arithmetic requires MariaDB timedelta values; SQLite returns datetime.time"
            )
        employee = self.make_employee()
        self.mapping(employee)
        shift = frappe.get_doc(
            {
                "doctype": "Shift Type",
                "name": "ZKT-" + self.suffix,
                "start_time": "00:00:00",
                "end_time": "23:59:00",
                "enable_auto_attendance": 0,
                "begin_check_in_before_shift_start_time": 0,
                "allow_check_out_after_shift_end_time": 0,
            }
        ).insert()
        location = frappe.get_doc(
            {
                "doctype": "Shift Location",
                "location_name": "ZKT-" + self.suffix,
                "latitude": 25,
                "longitude": 75,
                "checkin_radius": 100,
            }
        ).insert()
        assignment = frappe.get_doc(
            {
                "doctype": "Shift Assignment",
                "employee": employee.name,
                "shift_type": shift.name,
                "start_date": self.timestamp.date(),
                "shift_location": location.name,
                "status": "Active",
            }
        ).insert()
        assignment.submit()
        self.enable_live()
        previous = frappe.db.get_single_value("HR Settings", "allow_geolocation_tracking")
        try:
            frappe.db.set_single_value("HR Settings", "allow_geolocation_tracking", 1)
            from hrms.hr.doctype.employee_checkin.employee_checkin import (
                add_log_based_on_employee_field,
                CheckinRadiusExceededError,
            )

            with self.assertRaises(CheckinRadiusExceededError):
                add_log_based_on_employee_field(
                    employee_field_value=employee.name,
                    employee_fieldname="name",
                    timestamp=self.timestamp,
                    log_type="IN",
                    latitude=self.device.latitude,
                    longitude=self.device.longitude,
                )
            name, _ = persist_event(self.device, self.event)
            self.assertEqual(process_raw(name), "Failed")
            self.assertIn("meters", frappe.db.get_value("Biometric Raw Log", name, "processing_error"))
            self.assertFalse(frappe.db.get_value("Biometric Raw Log", name, "employee_checkin"))
        finally:
            frappe.db.set_single_value("HR Settings", "allow_geolocation_tracking", previous or 0)

    def test_existing_hrms_checkin_links_duplicate(self):
        self.mapping(self.make_employee())
        self.enable_live()
        name, _ = persist_event(self.device, self.event)
        self.assertEqual(process_raw(name), "Created")
        # Distinct physical native event remains in raw history, HRMS duplicate is linked.
        second, _ = persist_event(self.device, dict(self.event, native_id="different-record"))
        self.assertNotEqual(name, second)
        self.assertEqual(process_raw(second), "Duplicate")
        self.assertEqual(
            frappe.db.get_value("Biometric Raw Log", name, "employee_checkin"),
            frappe.db.get_value("Biometric Raw Log", second, "employee_checkin"),
        )

    def test_raw_history_cannot_be_deleted(self):
        name, _ = persist_event(self.device, self.event)
        with self.assertRaises(frappe.ValidationError):
            frappe.delete_doc("Biometric Raw Log", name, ignore_permissions=True)

    def test_api_agent_authentication_and_revocation(self):
        from types import SimpleNamespace
        from zkt_k50_biomatrics.security import authenticate_agent

        agent = frappe.get_doc(
            {
                "doctype": "Biometric Agent",
                "agent_code": "AUTH-" + self.suffix,
                "agent_name": "Auth Test",
                "enabled": 1,
                "token_hash": digest("test-secret"),
            }
        ).insert()
        request = SimpleNamespace(method="POST", content_length=100)
        with (
            patch("frappe.request", request),
            patch("frappe.get_request_header", return_value=f"{agent.name}:wrong"),
        ):
            with self.assertRaises(frappe.AuthenticationError):
                authenticate_agent()
        with (
            patch("frappe.request", request),
            patch("frappe.get_request_header", return_value=f"{agent.name}:test-secret"),
        ):
            self.assertEqual(authenticate_agent().name, agent.name)
            api.revoke_agent(agent.name)
            with self.assertRaises(frappe.AuthenticationError):
                authenticate_agent()

    def test_report_queries_and_dashboard(self):
        from zkt_k50_biomatrics.reports import execute_report
        from zkt_k50_biomatrics.install import setup

        setup()  # repeatable installation/migration hooks
        setup()
        self.assertIn("Total Devices", api.dashboard())
        columns, rows, message = execute_report(
            "Device Health Report", "Biometric Device", ["name", "last_status"], {}
        )
        self.assertEqual(columns[0]["options"], "Biometric Device")
        self.assertTrue(rows)
        self.assertNotIn("communication_key", str(rows))

    def test_concurrent_mariadb_ingestion(self):
        if frappe.db.db_type != "mariadb":
            self.skipTest("Requires MariaDB row locks; SQLite cannot validate multi-worker serialization")
        from concurrent.futures import ThreadPoolExecutor

        site = frappe.local.site
        sites_path = frappe.local.sites_path
        device_name = self.device.name
        event = dict(self.event)
        frappe.db.commit()

        def worker():
            frappe.init(site, sites_path=sites_path)
            frappe.connect()
            frappe.set_user("Administrator")
            frappe.flags.in_test = True
            try:
                frappe.db.get_value("Biometric Device", device_name, "name", for_update=True)
                result = persist_event(frappe.get_doc("Biometric Device", device_name), event)
                frappe.db.commit()
                return result
            finally:
                frappe.destroy()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sum(created for _, created in results), 1)

    def test_hrms_radius_rejection_is_preserved(self):
        self.mapping(self.make_employee())
        self.enable_live()
        name, _ = persist_event(self.device, self.event)
        from hrms.hr.doctype.employee_checkin.employee_checkin import CheckinRadiusExceededError

        target = "hrms.hr.doctype.employee_checkin.employee_checkin.EmployeeCheckin.validate_distance_from_shift_location"
        with patch(target, side_effect=CheckinRadiusExceededError("Outside shift radius")):
            self.assertEqual(process_raw(name), "Failed")
        self.assertIn("radius", frappe.db.get_value("Biometric Raw Log", name, "processing_error"))

    def test_edge_command_preview_and_live_delivery(self):
        from zkt_k50_biomatrics.jobs import queue_run

        self.mapping(self.make_employee())
        agent = frappe.get_doc(
            {
                "doctype": "Biometric Agent",
                "agent_code": "EDGE-" + self.suffix,
                "agent_name": "Mock Edge",
                "enabled": 1,
            }
        ).insert()
        self.device.connection_mode = "Edge Agent"
        self.device.agent = agent.name
        self.device.save()
        preview_name = queue_run(self.device, dry_run=True)
        with (
            patch("zkt_k50_biomatrics.agent_api.authenticate_agent", return_value=agent),
            patch("frappe.enqueue"),
        ):
            heartbeat = agent_api.heartbeat(version="test")
            command = next(c for c in heartbeat["commands"] if c["run"] == preview_name)
            args = {"device": self.device.name, "run": preview_name, "lease": command["lease"]}
            before = frappe.db.count("Biometric Raw Log")
            agent_api.preview(events=[self.event], **args)
            agent_api.preview(events=[self.event], **args)  # lost-response retry must not inflate preview
            agent_api.complete(**args)
            run = frappe.get_doc("Biometric Sync Run", preview_name)
            self.assertEqual(run.records_retrieved, 1)
            self.assertEqual(frappe.db.count("Biometric Raw Log"), before)
            self.device.reload()
            self.device.live_sync_enabled = 1
            self.device.save()
            live_name = queue_run(self.device, dry_run=False)
            heartbeat = agent_api.heartbeat()
            command = next(c for c in heartbeat["commands"] if c["run"] == live_name)
            args = {"device": self.device.name, "run": live_name, "lease": command["lease"]}
            receipt = agent_api.ingest(events=[self.event], **args)
            self.assertTrue(receipt["acknowledgements"][0]["accepted"])
            self.assertEqual(process_raw(receipt["acknowledgements"][0]["raw_log"]), "Created")
            agent_api.complete(**args)
            run = frappe.get_doc("Biometric Sync Run", live_name)
            self.assertEqual(run.status, "Success")
            self.assertEqual(run.events_received, 1)
            self.assertEqual(run.created_count, 1)
