#!/usr/bin/env python3
"""
Auto Reconcile Daemon

Simple long-running Python daemon that polls the acquirer JSON-RPC 'CheckTransaction'
for locally pending transactions and marks orders PAID when the acquirer reports
the transaction performed.

Usage:
  PYTHONPATH=. python3 scripts/auto_reconcile_daemon.py --interval 60

It reads configuration from `app.core.config.settings`, so ensure your `.env` is
present in the project root and contains DATABASE_URL and ACQUIRING_BASE_URL and
credentials. The script is idempotent and safe to run continuously.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Optional

import httpx
from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.models.transaction import Transaction
from app.crud import transaction as transaction_crud
from app.services.rpc_handler import RpcHandler
from app.core.config import settings

logger = logging.getLogger("auto_reconcile")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


async def check_remote_tx(client: httpx.AsyncClient, tx_id: str) -> Optional[dict]:
    base = settings.ACQUIRING_BASE_URL or os.getenv("ACQUIRING_BASE_URL")
    if not base:
        logger.warning("ACQUIRING_BASE_URL not configured")
        return None
    url = base.rstrip("/") + "/rpc"
    payload = {"jsonrpc": "2.0", "method": "CheckTransaction", "params": {"id": tx_id}, "id": 1}
    try:
        r = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            logger.warning("Unexpected acquirer response for %s: %s", tx_id, data)
            return None
        if data.get("error"):
            logger.debug("Acquirer returned error for %s: %s", tx_id, data.get("error"))
            return None
        return data.get("result")
    except Exception as exc:
        logger.warning("Acquirer check failed for %s: %s", tx_id, exc)
        return None


async def reconcile_pass(dry_run: bool = False, interval: int = 60):
    # prepare HTTP client auth
    basic_user = settings.ACQUIRING_RPC_BASIC_USERNAME
    if hasattr(settings.ACQUIRING_RPC_BASIC_PASSWORD, "get_secret_value"):
        basic_pass = settings.ACQUIRING_RPC_BASIC_PASSWORD.get_secret_value()
    else:
        basic_pass = str(settings.ACQUIRING_RPC_BASIC_PASSWORD) if settings.ACQUIRING_RPC_BASIC_PASSWORD else os.getenv("ACQUIRING_RPC_BASIC_PASSWORD")
    auth = (basic_user, basic_pass) if basic_user and basic_pass else None

    async with AsyncSessionLocal() as db:
        async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
            while True:
                try:
                    result = await db.execute(select(Transaction).where(Transaction.state == 1))
                    txs = result.scalars().all()
                except Exception as exc:
                    logger.exception("DB error while fetching pending transactions: %s", exc)
                    txs = []

                if not txs:
                    logger.debug("No pending transactions found")
                else:
                    logger.info("Found %d pending transaction(s) to check", len(txs))

                for tx in txs:
                    tx_id = getattr(tx, "id", None)
                    if not tx_id:
                        continue
                    logger.info("Checking tx %s (local pk=%s)", tx_id, getattr(tx, "pk", None))
                    res = await check_remote_tx(client, tx_id)
                    if not res:
                        logger.debug("No remote result for %s", tx_id)
                        continue

                    remote_state = res.get("state") if isinstance(res, dict) else None
                    perform_time = res.get("perform_time") if isinstance(res, dict) else None

                    if remote_state == 2 or (isinstance(perform_time, int) and perform_time > 0):
                        logger.info("Remote reports tx %s performed -> reconciling", tx_id)
                        if dry_run:
                            logger.info("Dry-run: would set tx %s to paid and mark order PAID", tx_id)
                            continue
                        try:
                            perform_time_ms = int(perform_time) if perform_time else int(__import__("time").time() * 1000)
                            tx = await transaction_crud.update_transaction_state(db, tx=tx, state=2, perform_time=perform_time_ms)
                            handler = RpcHandler(db)
                            await handler._mark_order_paid_from_transaction(tx)
                            await db.commit()
                            logger.info("Reconciled tx %s", tx_id)
                        except Exception as exc:
                            logger.exception("Failed to reconcile tx %s: %s", tx_id, exc)
                    else:
                        logger.debug("Remote tx %s not performed (state=%s)", tx_id, remote_state)

                # sleep interval seconds
                await asyncio.sleep(interval)


def _setup_signal(loop):
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown(loop)))
        except NotImplementedError:
            # Windows
            pass


async def _shutdown(loop):
    logger.info("Shutdown requested, cancelling tasks...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Auto reconcile daemon")
    parser.add_argument("--interval", type=int, default=60, help="Poll interval seconds")
    parser.add_argument("--dry-run", action="store_true", help="Do not apply changes; only log")
    args = parser.parse_args()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _setup_signal(loop)
    try:
        loop.run_until_complete(reconcile_pass(dry_run=args.dry_run, interval=args.interval))
    except asyncio.CancelledError:
        logger.info("Daemon cancelled")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
