"""Run the backend: python -m backend [--host 0.0.0.0] [--port 8000] [--tcp-port 5000] [--db var/bako.db]"""

import argparse
import logging
import os

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="BAKO production monitoring backend")
    ap.add_argument("--host", default="0.0.0.0", help="HTTP host (app, dashboard, API)")
    ap.add_argument("--port", type=int, default=8000, help="HTTP port")
    ap.add_argument("--tcp-port", type=int, default=5000, help="gateway TCP ingest port")
    ap.add_argument("--db", help="SQLite database path (default var/bako.db)")
    ap.add_argument("--config", help="reference data JSON (default config/reference.json)")
    args = ap.parse_args()
    os.environ["BAKO_TCP_PORT"] = str(args.tcp_port)
    if args.db:
        os.environ["BAKO_DB"] = args.db
    if args.config:
        os.environ["BAKO_CONFIG"] = args.config
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .main import create_app
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info", timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()
