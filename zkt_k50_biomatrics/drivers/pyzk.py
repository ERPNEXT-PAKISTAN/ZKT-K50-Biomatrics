from . import DeviceError, Driver


class PyZKDriver(Driver):
    def __init__(self, config):
        self.config = config
        self.connection = None

    def connect(self):
        if self.connection is None:
            try:
                from zk import ZK

                c = self.config
                self.connection = ZK(
                    c["host"],
                    port=int(c.get("port", 4370)),
                    timeout=int(c.get("timeout", 15)),
                    password=int(c.get("communication_key") or 0),
                    force_udp=c.get("transport", "TCP") == "UDP",
                    ommit_ping=bool(c.get("omit_ping", True)),
                ).connect()
            except Exception as exc:
                # Library exceptions may contain request details; expose category only.
                raise DeviceError(
                    f"Connection failed ({type(exc).__name__}). Check network/VPN routing, "
                    "transport, communication key, port and timeout."
                ) from None
        return self.connection

    def get_device_info(self):
        conn = self.connect()
        try:
            return {
                "serial_number": conn.get_serialnumber(),
                "firmware": conn.get_firmware_version(),
                "capabilities": self.get_capabilities(),
            }
        except Exception as exc:
            raise DeviceError(f"Device information unavailable ({type(exc).__name__})") from None

    def get_users(self):
        try:
            # Do not retain passwords, cards, fingerprints or other enrollment material.
            return [
                {"user_id": str(u.user_id), "uid": str(u.uid), "name": u.name}
                for u in self.connect().get_users()
            ]
        except DeviceError:
            raise
        except Exception as exc:
            raise DeviceError(f"User discovery failed ({type(exc).__name__})") from None

    @staticmethod
    def normalize_attendance_record(record):
        return {
            "user_id": str(record.user_id),
            "uid": str(record.uid),
            "timestamp": record.timestamp.isoformat(),
            "punch": str(record.punch),
        }

    def get_attendance_records(self):
        try:
            return [self.normalize_attendance_record(r) for r in self.connect().get_attendance()]
        except DeviceError:
            raise
        except Exception as exc:
            raise DeviceError(f"Attendance retrieval failed ({type(exc).__name__})") from None

    def close_connection(self):
        if self.connection is not None:
            try:
                self.connection.disconnect()
            finally:
                self.connection = None
