# Telemetry

There is a dedicated telemetry installation task.

## Network detection

At system start, network availability is checked.
If network is unavailable, retry interval increases by sqrt(2) each attempt (e.g., 1.0 s, 1.4 s, ...).
When network appears, telemetry attempts to send data.

## Delivery channels

Delivery channels and endpoints come from secrets:
Telegram bot (messages and files)
Google Drive (file uploads)

There are two independent send queues; architecture must allow adding more:
Telegram queue
Google Drive queue

## PDF generation and encryption

Telemetry is generated as encrypted PDF files.
Encryption: AES-256.

PDF encryption password is generated during Pyntara initialization from:
KeePass salt (decrypted with admin password during installation)
hostname

Hostname is generated randomly: 9 characters (as one of the tasks).

Unencrypted PDF versions must never be saved to disk (in-memory generation only).

After send, telemetry files are saved in a dedicated folder.

## Schedule and retry

Telemetry attempts to send immediately after computer boot.

Base accumulation/retry behavior:
telemetry accumulates for one day
after successful send, next send is scheduled for 12:00 local time
if unsent files exist, retries continue with sqrt(2) interval growth
retries with this scheme run only if more than one day has passed since last send

## Collected data

Telemetry additionally includes:
clipboard text (inside encrypted PDF)
startup network information: attempts to detect addresses/channels (Cloudflare, Yggdrasil, IPv6, etc.), machine's own addresses, and connection availability status

## Installation log

Installation log (full install + messages) is sent to telemetry as a separate file.
