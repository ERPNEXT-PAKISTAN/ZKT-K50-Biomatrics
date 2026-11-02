# ZKT K50 Biomatrics

Frappe v16 / ERPNext v16 / HRMS v16 application and independent Python edge agent.
App ID: `zkt_k50_biomatrics`. See `docs/deployment.md` for configuration and deployment.

Implements device settings, pyzk Pull driver, device-specific employee mapping, durable
raw-event ingestion, standard HRMS Employee Checkin processing, dry runs, job history,
Desk dashboard, eight reports, scoped agent enrollment and SQLite offline delivery.
New devices and agents are disabled. No live-site installation or device polling is
performed during development. No fingerprints, templates, faces or enrollment data
are stored. Device memory is never cleared.

The tested prototype reported K50 connectivity. This application's real-device driver
still needs an operator-controlled hardware acceptance test; no universal device
compatibility is claimed. HTTP/ISAPI and ADMS drivers are not implemented.

License: MIT. Version 0.1.0; review validation results and limitations before production.

- [Implementation handoff](docs/implementation-summary.md)
- [Deployment and K50 setup](docs/deployment.md)
- [Executed checks and pending acceptance tests](docs/validation.md)
