# Fitbit Air HR

Self-hosted, single-user web app for Google Fitbit Air workout heart rate:
history from the Google Health API (read-only) and a live Web Bluetooth page.
Reachable only over the tailnet via `tailscale serve`.

Work in progress, built milestone by milestone.

- Google Cloud / OAuth setup and API access probe: [docs/google-setup.md](docs/google-setup.md), [tools/gh_probe.py](tools/gh_probe.py)
- Run on Tower (Docker, loopback-only) and publish with `tailscale serve`: [docs/tower-setup.md](docs/tower-setup.md)
- Tests: `pip install -r requirements.txt -r requirements-dev.txt && python -m pytest tests/py` and `npm test` (BLE parser, Node 18+)
- Local UI against a fake Google API: `python tests/py/dev_server.py 8799`
