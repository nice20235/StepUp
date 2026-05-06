#!/usr/bin/env python3
"""
Poll acquirer for pending transactions and reconcile them locally.

Usage: set environment variables (DATABASE_URL, ACQUIRING_BASE_URL, ACQUIRING_RPC_BASIC_USERNAME, ACQUIRING_RPC_BASIC_PASSWORD)
and run:

PYTHONPATH=. python3 scripts/poll_acquirer_and_reconcile.py

This script attempts to contact the acquirer via JSON-RPC CheckTransaction
method (POST to {ACQUIRING_BASE_URL}/rpc) using HTTP Basic auth. If the
acquirer reports the transaction as performed/paid, the script updates the
local transaction record and marks the associated order PAID (and clears the cart).
"""
import asyncio
import logging
import os
from typing import Optional

import httpx
from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.models.transaction import Transaction
from app.crud import transaction as transaction_crud
from app.crud.order import update_order_status
from app.services.rpc_handler import RpcHandler
from app.core.config import settings

logger = logging.getLogger("poll_acquirer")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


async def check_remote_tx(client: httpx.AsyncClient, tx_id: str) -> Optional[dict]:
    """Call the acquirer CheckTransaction JSON-RPC and return response result dict or None."""
    base = settings.ACQUIRING_BASE_URL or os.getenv('ACQUIRING_BASE_URL')
    if not base:
        logger.warning("ACQUIRING_BASE_URL is not configured")
        return None

    url = base.rstrip('/') + '/rpc'
    payload = {
        "jsonrpc": "2.0",
        "method": "CheckTransaction",
        "params": {"id": tx_id},
        "id": 1,
    }

    try:
        r = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            logger.warning("Unexpected acquirer response for tx %s: %s", tx_id, data)
            return None
        if data.get('error'):
            logger.warning("Acquirer returned error for tx %s: %s", tx_id, data.get('error'))
            return None
        # JSON-RPC success response contains 'result'
        return data.get('result')
    except Exception as exc:
        logger.warning("Acquirer check failed for tx %s: %s", tx_id, exc)
        return None


async def poll_once(dry_run: bool = False):
    """Single pass: query local transactions in state=1 and reconcile via acquirer."""
    basic_user = settings.ACQUIRING_RPC_BASIC_USERNAME
    # SecretStr vs plain string handling
    if hasattr(settings.ACQUIRING_RPC_BASIC_PASSWORD, 'get_secret_value'):
        basic_pass = settings.ACQUIRING_RPC_BASIC_PASSWORD.get_secret_value()
    else:
        basic_pass = str(settings.ACQUIRING_RPC_BASIC_PASSWORD) if settings.ACQUIRING_RPC_BASIC_PASSWORD else os.getenv('ACQUIRING_RPC_BASIC_PASSWORD')

    auth = (basic_user, basic_pass) if basic_user and basic_pass else None

    async with AsyncSessionLocal() as db:
        # Find transactions that are still in 'created' state
        result = await db.execute(select(Transaction).where(Transaction.state == 1))
        txs = result.scalars().all()

        if not txs:
            logger.info("No pending transactions to check")
            return

        async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
            for tx in txs:
                tx_id = getattr(tx, 'id', None)
                if not tx_id:
                    continue
                logger.info("Checking remote status for tx %s (local pk=%s)", tx_id, getattr(tx, 'pk', None))
                res = await check_remote_tx(client, tx_id)
                if not res:
                    logger.debug("No response or error for tx %s", tx_id)
                    continue

                # Heuristic: if remote result contains state==2 or 'performed' flag, treat as paid
                remote_state = res.get('state') if isinstance(res, dict) else None
                # Some acquirers return perform_time or similar
                perform_time = res.get('perform_time') if isinstance(res, dict) else None

                if remote_state == 2 or (isinstance(perform_time, int) and perform_time > 0):
                    logger.info("Remote acquirer reports tx %s performed/paid - reconciling locally", tx_id)
                    if dry_run:
                        logger.info("Dry-run: would mark tx %s as paid and update order", tx_id)
                        continue

                    # Update local transaction state to 2
                    perform_time_ms = int(perform_time) if perform_time else None
                    tx = await transaction_crud.update_transaction_state(
                        db,
                        tx=tx,
                        state=2,
                        perform_time=perform_time_ms or int(__import__('time').time() * 1000),
                    )
                    # Try to mark order PAID using RpcHandler helper
                    try:
                        handler = RpcHandler(db)
                        await handler._mark_order_paid_from_transaction(tx)
                    except Exception as exc:
                        logger.exception("Failed to mark order PAID from tx %s: %s", tx_id, exc)
                    await db.commit()
                else:
                    logger.debug("Remote state for tx %s is not performed: %s", tx_id, remote_state)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Poll acquirer for pending transactions and reconcile locally")
    parser.add_argument('--dry-run', action='store_true', help='Do not apply changes; only log what would happen')
    args = parser.parse_args()

    asyncio.run(poll_once(dry_run=args.dry_run))


if __name__ == '__main__':
    main()
