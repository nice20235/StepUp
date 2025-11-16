from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import QueuePool
from sqlalchemy import text
from typing import AsyncGenerator
import logging
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from app.core.config import settings

logger = logging.getLogger(__name__)

# Database URL - using settings from config
DATABASE_URL = settings.DATABASE_URL

# Create async engine with PostgreSQL optimizations
engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Set to True for SQL debugging
    poolclass=QueuePool,
    pool_size=20,           # Number of connections to maintain
    max_overflow=30,        # Additional connections when pool is exhausted
    pool_pre_ping=True,     # Verify connections before using
    pool_recycle=3600,      # Recycle connections after 1 hour
    pool_timeout=30,        # Timeout when waiting for connection from pool
    connect_args={
        "command_timeout": 60,
        "server_settings": {
            "jit": "off",               # Disable JIT compilation for predictable performance
            "application_name": "stepup_api",
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
    """Initialize database tables and apply migrations"""
    async with engine.begin() as conn:
        # Import all models to ensure they're registered
        from app.models.user import User  # noqa: F401
        from app.models.stepup import StepUp, Category, StepUpImage  # noqa: F401
        from app.models.order import Order, OrderItem  # noqa: F401
        from app.models.cart import Cart, CartItem  # noqa: F401
        from app.models.payment import Payment  # noqa: F401
        
        # Create all tables (indexes are automatically created from model definitions)
        await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Database tables and indexes created successfully!")
        
        # Apply data migrations
        await _apply_data_migrations(conn)


async def _apply_data_migrations(conn):
    """Apply data integrity migrations and cleanups"""
    try:
        # Check if there are any orders before attempting migrations
        result = await conn.execute(text("SELECT COUNT(*) FROM orders"))
        order_count = result.scalar()
        
        if order_count > 0:
            # Normalize order statuses to uppercase standard values
            await conn.execute(text("""
                UPDATE orders 
                SET status = UPPER(status)
                WHERE status IN ('pending', 'paid', 'refunded', 'confirmed', 'preparing', 'ready', 'delivered', 'cancelled')
            """))
            
            await conn.execute(text("""
                UPDATE orders 
                SET status = 'PAID' 
                WHERE status IN ('CONFIRMED', 'PREPARING', 'READY', 'DELIVERED')
            """))
            
            await conn.execute(text("""
                UPDATE orders 
                SET status = 'PENDING' 
                WHERE status IN ('CANCELLED') OR status NOT IN ('PENDING', 'PAID', 'REFUNDED')
            """))
            
            # Fix any null or invalid order_id values
            await conn.execute(text("""
                UPDATE orders 
                SET order_id = CAST(id AS TEXT)
                WHERE order_id IS NULL OR order_id = '' OR order_id = '0'
            """))
            
            logger.info(f"✅ Data migrations applied successfully! ({order_count} orders processed)")
        else:
            logger.info("✅ Data migrations skipped (no existing orders)")
    except Exception as e:
        logger.warning(f"Data migration warning: {e}")

# Close database connections
async def close_db():
    """Close database connections"""
    await engine.dispose()