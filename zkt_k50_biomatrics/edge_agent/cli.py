import argparse
import fcntl
import json
import logging
import os
import socket
import time
from pathlib import Path

from .. import __version__
from ..drivers import get_driver
from ..normalization import normalize_time, event_key
from .client import Client
from .outbox import Outbox

LOG = logging.getLogger("zkt-agent")


def load_config(path):
    path = Path(path)
    if path.stat().st_mode & 0o077:
        raise ValueError("Agent configuration contains secrets: chmod 600 the configuration file")
    config = json.loads(path.read_text())
    credential_path = Path(config["credential_file"])
    if credential_path.exists() and credential_path.stat().st_mode & 0o077:
        raise ValueError("Credential file must have permissions 600")
    return config


def save_credential(path, credential):
    path = Path(path)
    tmp = path.with_suffix(".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(credential)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def select_records(device, records, start=None, end=None):
    # Server sends cutoffs in site timezone, separately from terminal timezone.
    from datetime import datetime

    timezone = device["site_timezone"]
    lower = (
        max(datetime.fromisoformat(str(device["sync_start"])), datetime.fromisoformat(start))
        if start
        else datetime.fromisoformat(str(device["sync_start"]))
    )
    upper = datetime.fromisoformat(end) if end else None
    result = []
    for event in records:
        try:
            stamp = normalize_time(event["timestamp"], device["device_timezone"], timezone)
        except ValueError:
            result.append(event)
            continue
        if stamp >= lower and (upper is None or stamp <= upper):
            result.append(event)
    return result


def driver_for(config, device):
    local = config.get("devices", {}).get(device["name"])
    if local is None:
        raise ValueError("Assigned device is missing protected local configuration")
    # Network destination and communication key remain under local administrator control.
    settings = {key: device[key] for key in ("port", "transport", "timeout", "omit_ping")}
    settings.update(local)
    return get_driver(device["driver"], settings)


def execute_command(client, config, device, command, outbox):
    args = dict(device=device["name"], run=command["run"], lease=command["lease"])
    result = {}
    with driver_for(config, device) as driver:
        operation = command["operation"]
        if operation in ("Test Connection", "Device Info"):
            result = driver.get_device_info()
        elif operation == "Fetch Users":
            result = {"users": driver.get_users()}
        else:
            events = select_records(
                device, driver.get_attendance_records(), command.get("start"), command.get("end")
            )
            if command["mode"] == "Dry Run":
                for offset in range(0, len(events), 200):
                    client.call("preview", events=events[offset : offset + 200], **args)
            else:
                if not device["live_sync_enabled"]:
                    raise ValueError("Live synchronization disabled")
                outbox.put(device["name"], events)
                # All rows, including interrupted older deliveries, retain durable per-event acknowledgements.
                for offset in range(0, len(events), 200):
                    keys = [event_key(device["name"], event) for event in events[offset : offset + 200]]
                    upload(client, outbox, device["name"], args, keys=keys)
                result["pending_events"] = outbox.pending_count(device["name"])
    client.call("complete", result=result, **args)


def upload(client, outbox, device, command=None, keys=None):
    while batch := outbox.batch(device, keys=keys):
        try:
            result = client.call("ingest", **(command or {"device": device}), events=[r[1] for r in batch])
            outbox.acknowledge(batch, result["acknowledgements"])
        except Exception:
            outbox.failure([r[0] for r in batch])
            raise


def run(config, once=False):
    client = Client(config["site_url"], Path(config["credential_file"]).read_text().strip())
    outbox = Outbox(config["outbox"])
    due = {}
    # A second process must not duplicate command execution or race credential/state files.
    lock = open(str(config["outbox"]) + ".lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    cached_devices = []
    try:
        while True:
            try:
                heartbeat = client.call("heartbeat", version=__version__, hostname=socket.gethostname())
                cached_devices = heartbeat["devices"]
                devices = {d["name"]: d for d in cached_devices}
                for command in heartbeat["commands"]:
                    try:
                        execute_command(client, config, devices[command["device"]], command, outbox)
                    except Exception as exc:
                        LOG.error("Command failed (%s); lease will permit retry", type(exc).__name__)
                        # A transport failure leaves the command leased for safe re-delivery.
                online = True
            except Exception as exc:
                online = False
                LOG.warning("Cloud unavailable (%s); continuing local device polling", type(exc).__name__)
            # Persist last authorized device configuration to resume offline after a reboot.
            cache_path = Path(str(config["outbox"]) + ".devices.json")
            if online:
                save_credential(cache_path, json.dumps(cached_devices))
            elif not cached_devices and cache_path.exists():
                cached_devices = json.loads(cache_path.read_text())
            for device in cached_devices:
                if not device["live_sync_enabled"]:
                    continue
                name = device["name"]
                if time.monotonic() >= due.get(name, 0):
                    try:
                        with driver_for(config, device) as driver:
                            outbox.put(name, select_records(device, driver.get_attendance_records()))
                    except Exception as exc:
                        LOG.error("Device %s poll failed (%s)", name, type(exc).__name__)
                    due[name] = time.monotonic() + int(device["polling_interval"])
                if online:
                    try:
                        upload(client, outbox, name)
                    except Exception as exc:
                        LOG.warning("Upload deferred (%s)", type(exc).__name__)
            if once:
                break
            time.sleep(30)
    finally:
        outbox.close()
        lock.close()


def main():
    parser = argparse.ArgumentParser(description="ZKT K50 Biomatrics edge agent")
    parser.add_argument("--config", required=True)
    parser.add_argument("command", choices=["enroll", "run", "once", "status", "test-device"])
    parser.add_argument("--device")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.umask(0o077)
    config = load_config(args.config)
    if args.command == "enroll":
        from getpass import getpass

        # Prompt prevents credentials appearing in shell history or process arguments.
        enrollment = getpass("One-time enrollment credential: ")
        response = Client(config["site_url"], enrollment).call("enroll")
        save_credential(config["credential_file"], response["credential"])
        print("Enrolled. Existing enrollment codes and credentials have been replaced.")
    elif args.command == "status":
        outbox = Outbox(config["outbox"])
        print(json.dumps(outbox.status()))
        outbox.close()
    elif args.command == "test-device":
        local = config["devices"][args.device]
        with get_driver("pyzk", local) as driver:
            print(json.dumps(driver.test_connection()))
    else:
        run(config, once=args.command == "once")


if __name__ == "__main__":
    main()
