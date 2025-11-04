from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool, QueuePool
from sqlalchemy import event
from typing import AsyncGenerator
import os
import logging
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError

logger = logging.getLogger(__name__)

# Database URL - use SQLite for development, can be changed to PostgreSQL for production
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./slippers.db")

# Create async engine with optimized settings
if "sqlite" in DATABASE_URL:
    # SQLite-specific optimizations
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,  # Set to True for SQL debugging
        poolclass=StaticPool,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={
            "check_same_thread": False,
            "timeout": 30,  # Increased timeout
            "isolation_level": None,
        }
    )
    # Ensure important PRAGMAs are set for every connection
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: D401
        """Set SQLite PRAGMAs for FK enforcement and better journaling."""
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.close()
        except Exception:
            # Best-effort; continue even if PRAGMAs can't be set
            pass
else:
    # PostgreSQL/MySQL optimizations
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        poolclass=QueuePool,
        pool_size=20,
        max_overflow=30,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_timeout=30,
        connect_args={
            "command_timeout": 60,
            "server_settings": {
                "jit": "off",
            }
        }
    )

# Create async session factory with optimizations
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Better for async operations
    autocommit=False,
    autoflush=False,  # Manual control over when to flush
)

# Base class for all models
class Base(DeclarativeBase):
    pass

# Dependency to get database session
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Optimized dependency to get database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except HTTPException:
            # Expected API error paths: rollback without noisy error logs
            await session.rollback()
            raise
        except RequestValidationError:
            # Validation errors occur before hitting business logic; treat quietly
            await session.rollback()
            raise
        except Exception as e:
            await session.rollback()
            # Log full stack trace and exception details for unexpected errors
            logger.exception("Database session error")
            raise
        # Do not call session.close() here: context manager handles it

# Initialize database tables
async def init_db():
    """Initialize database tables"""
    async with engine.begin() as conn:
        # Import all models to ensure they're registered
        from app.models.user import User  # noqa: F401
        from app.models.slipper import Slipper, Category, SlipperImage  # noqa: F401
        from app.models.order import Order, OrderItem  # noqa: F401
        from app.models.cart import Cart, CartItem  # noqa: F401
        from app.models.payment import Payment  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
        print("✅ Database tables created successfully!")

        # --- Legacy status normalization (idempotent) ---
    # We simplified OrderStatus to: PENDING, PAID, REFUNDED (uppercase stored values).
        # Older records may still hold: confirmed, preparing, ready, delivered, cancelled.
        # Map them as follows:
        #   confirmed/preparing/ready/delivered -> paid (they indicate post-payment states)
        #   cancelled -> pending (fallback) if kept, otherwise leave or adjust per business rules.
        # Any unknown statuses -> pending.
        try:
            # Normalize to lowercase first (defensive) then map.
            await conn.exec_driver_sql("""
                UPDATE orders SET status=UPPER(status);
            """)
            await conn.exec_driver_sql("""
                UPDATE orders SET status='PAID' WHERE status IN ('confirmed','preparing','ready','delivered','paid');
            """)
            await conn.exec_driver_sql("""
                UPDATE orders SET status='PENDING' WHERE status IN ('cancelled','pending');
            """)
            await conn.exec_driver_sql("""
                UPDATE orders SET status='PENDING' WHERE status NOT IN ('PENDING','PAID','REFUNDED');
            """)
            logger.info("✅ Order status normalization completed")
        except Exception as e:
            logger.warning("Order status normalization skipped/failed: %s", e)

        # --- Lightweight migration: add payment_uuid column to orders if missing (idempotent) ---
        try:
            res = await conn.exec_driver_sql("PRAGMA table_info(orders);")
            cols = [r[1] for r in res.fetchall()]  # r[1] is column name
            if 'payment_uuid' not in cols:
                logger.info("Adding missing payment_uuid column to orders table (auto-migration)...")
                await conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN payment_uuid VARCHAR(64);")
                try:
                    await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_orders_payment_uuid ON orders(payment_uuid);")
                except Exception:
                    pass
                # Best-effort backfill from payments table when possible
                try:
                    await conn.exec_driver_sql(
                        """
                        UPDATE orders
                        SET payment_uuid = (
                            SELECT p.octo_payment_uuid
                            FROM payments p
                            WHERE p.order_id = orders.id AND p.octo_payment_uuid IS NOT NULL
                            ORDER BY p.created_at DESC
                            LIMIT 1
                        )
                        WHERE payment_uuid IS NULL;
                        """
                    )
                except Exception as e:
                    logger.warning("Backfill payment_uuid skipped: %s", e)
                logger.info("payment_uuid column added & backfill attempted")
            else:
                # Ensure index exists (ignore errors)
                try:
                    await conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_orders_payment_uuid ON orders(payment_uuid);")
                except Exception:
                    pass
        except Exception as e:
            logger.warning("Auto-migration for payment_uuid failed/skipped: %s", e)

        # --- Lightweight migration: add idempotency_key column & unique index if missing (idempotent) ---
        try:
            res = await conn.exec_driver_sql("PRAGMA table_info(orders);")
            cols = [r[1] for r in res.fetchall()]
            if 'idempotency_key' not in cols:
                logger.info("Adding missing idempotency_key column to orders table (auto-migration)...")
                await conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN idempotency_key VARCHAR(64);")
                try:
                    await conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_idempotency_key ON orders(idempotency_key);")
                except Exception:
                    pass
            else:
                try:
                    await conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_idempotency_key ON orders(idempotency_key);")
                except Exception:
                    pass
        except Exception as e:
            logger.warning("Auto-migration for idempotency_key failed/skipped: %s", e)

        # --- Lightweight migration: enforce unique (order_id, slipper_id) on order_items to avoid duplicates ---
        try:
            await conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_order_items_order_slipper ON order_items(order_id, slipper_id);"
            )
        except Exception as e:
            logger.warning("Creating unique index for order_items failed/skipped: %s", e)

        # --- Cleanup migration: fix any bad placeholder order_id values ---
        # Earlier versions temporarily set order_id to '0' before updating, which could violate the unique constraint
        # under concurrency. Normalize such rows to the primary key string, and also fix empty/NULL.
        try:
            await conn.exec_driver_sql(
                """
                UPDATE orders
                SET order_id = CAST(id AS TEXT)
                WHERE order_id IS NULL OR order_id = '' OR order_id = '0';
                """
            )
        except Exception as e:
            logger.warning("Order order_id cleanup skipped/failed: %s", e)

        # --- Data repair: consolidate duplicate order_items per (order_id, slipper_id) and recompute order totals ---
        # Historical bugs could have created multiple rows for the same slipper in one order,
        # inflating totals. We normalize by collapsing duplicates into a single row per pair.
        try:
            # Temp tables: sums per (order_id, slipper_id) and the keeper row id (min id)
            await conn.exec_driver_sql("DROP TABLE IF EXISTS oi_sums;")
            await conn.exec_driver_sql("DROP TABLE IF EXISTS oi_keepers;")
            await conn.exec_driver_sql(
                """
                CREATE TEMP TABLE oi_sums AS
                SELECT order_id, slipper_id, SUM(quantity) AS sum_qty, MAX(unit_price) AS max_unit_price
                FROM order_items
                GROUP BY order_id, slipper_id;
                """
            )
            await conn.exec_driver_sql(
                """
                CREATE TEMP TABLE oi_keepers AS
                SELECT MIN(id) AS keep_id, order_id, slipper_id
                FROM order_items
                GROUP BY order_id, slipper_id;
                """
            )

            # Update keeper rows with consolidated quantities and recomputed totals
            await conn.exec_driver_sql(
                """
                UPDATE order_items
                SET
                  quantity = (
                    SELECT s.sum_qty FROM oi_sums s
                    WHERE s.order_id = order_items.order_id AND s.slipper_id = order_items.slipper_id
                  ),
                  unit_price = (
                    SELECT s.max_unit_price FROM oi_sums s
                    WHERE s.order_id = order_items.order_id AND s.slipper_id = order_items.slipper_id
                  ),
                  total_price = (
                    (SELECT s.sum_qty FROM oi_sums s
                     WHERE s.order_id = order_items.order_id AND s.slipper_id = order_items.slipper_id)
                    *
                    (SELECT s.max_unit_price FROM oi_sums s
                     WHERE s.order_id = order_items.order_id AND s.slipper_id = order_items.slipper_id)
                  )
                WHERE id IN (SELECT keep_id FROM oi_keepers);
                """
            )

            # Delete all non-keeper duplicates
            await conn.exec_driver_sql(
                """
                DELETE FROM order_items
                WHERE id NOT IN (SELECT keep_id FROM oi_keepers);
                """
            )

            # Recompute totals for all orders to reflect consolidated items
            await conn.exec_driver_sql(
                """
                UPDATE orders
                SET total_amount = (
                    SELECT COALESCE(SUM(oi.total_price), 0)
                    FROM order_items oi
                    WHERE oi.order_id = orders.id
                );
                """
            )

            # Retry creating the unique index now that duplicates are gone
            try:
                await conn.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_order_items_order_slipper ON order_items(order_id, slipper_id);"
                )
            except Exception:
                pass

            logger.info("✅ Consolidated duplicate order_items and recomputed order totals")
        except Exception as e:
            logger.warning("Duplicate order_items consolidation skipped/failed: %s", e)

        # --- Safeguards for cart integrity: enforce single cart per user and consolidate duplicate cart_items ---
        try:
            # Ensure at most one cart per user (application logic expects 1:1)
            try:
                await conn.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_carts_user ON carts(user_id);"
                )
            except Exception:
                pass

            # Consolidate duplicate lines by (cart_id, slipper_id)
            await conn.exec_driver_sql("DROP TABLE IF EXISTS ci_sums;")
            await conn.exec_driver_sql("DROP TABLE IF EXISTS ci_keepers;")
            await conn.exec_driver_sql(
                """
                CREATE TEMP TABLE ci_sums AS
                SELECT cart_id, slipper_id, SUM(quantity) AS sum_qty
                FROM cart_items
                GROUP BY cart_id, slipper_id;
                """
            )
            await conn.exec_driver_sql(
                """
                CREATE TEMP TABLE ci_keepers AS
                SELECT MIN(id) AS keep_id, cart_id, slipper_id
                FROM cart_items
                GROUP BY cart_id, slipper_id;
                """
            )
            # Update the keeper rows to consolidated quantity
            await conn.exec_driver_sql(
                """
                UPDATE cart_items
                SET quantity = (
                    SELECT s.sum_qty FROM ci_sums s
                    WHERE s.cart_id = cart_items.cart_id AND s.slipper_id = cart_items.slipper_id
                )
                WHERE id IN (SELECT keep_id FROM ci_keepers);
                """
            )
            # Delete non-keeper duplicates
            await conn.exec_driver_sql(
                """
                DELETE FROM cart_items
                WHERE id NOT IN (SELECT keep_id FROM ci_keepers);
                """
            )
            # Enforce unique constraint to prevent future duplicates
            try:
                await conn.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_cart_items_cart_slipper ON cart_items(cart_id, slipper_id);"
                )
            except Exception:
                pass
            logger.info("✅ Consolidated duplicate cart_items and enforced unique constraints")
        except Exception as e:
            logger.warning("Cart integrity safeguards skipped/failed: %s", e)

# Close database connections
async def close_db():
    """Close database connections"""
    await engine.dispose()