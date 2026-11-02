import hashlib
import hmac
import secrets

import frappe
from frappe.utils import get_datetime, now_datetime

MANAGERS = ("System Manager", "Biometric Administrator", "Biometric Manager")


def require_manager():
    if not set(MANAGERS).intersection(frappe.get_roles()):
        frappe.throw("Biometric management permission is required", frappe.PermissionError)


def require_admin():
    if not {"System Manager", "Biometric Administrator"}.intersection(frappe.get_roles()):
        frappe.throw("Biometric administrator permission is required", frappe.PermissionError)


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_secret():
    token = secrets.token_urlsafe(48)
    return token, digest(token)


def authenticate_agent(enrollment=False):
    if frappe.request.method != "POST":
        frappe.throw("POST is required", frappe.PermissionError)
    if frappe.request.content_length and frappe.request.content_length > 1024 * 1024:
        frappe.throw("Request exceeds 1 MiB", frappe.ValidationError)
    auth = frappe.get_request_header("X-ZKT-Credential") or ""
    code, separator, token = auth.partition(":")
    if not separator or not token or len(auth) > 300:
        frappe.throw("Agent credential required", frappe.AuthenticationError)
    agent = frappe.get_doc("Biometric Agent", code)
    stored = agent.enrollment_hash if enrollment else agent.token_hash
    if not agent.enabled or agent.revoked or not stored or not hmac.compare_digest(stored, digest(token)):
        frappe.throw("Invalid or revoked agent credential", frappe.AuthenticationError)
    if enrollment and (
        not agent.enrollment_expires or get_datetime(agent.enrollment_expires) <= now_datetime()
    ):
        frappe.throw("Enrollment expired; request a new enrollment code", frappe.AuthenticationError)
    return agent


def assigned_device(agent, name):
    device = frappe.get_doc("Biometric Device", name)
    if not device.enabled or device.connection_mode != "Edge Agent" or device.agent != agent.name:
        frappe.throw("Device is disabled or is not assigned to this agent", frappe.PermissionError)
    return device
