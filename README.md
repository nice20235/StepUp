# Slippers API

FastAPI-based slippers ordering & payment system with user authentication, catalog, orders, and OCTO payment integration.

## Features

- **Authentication**: JWT (access + refresh) in HttpOnly cookies
- **Role-Based Access**: Admin vs user protected endpoints
- **Slipper Catalog**: CRUD + pagination, search, sorting, category filter, multi-image upload
- **Categories**: CRUD & caching
- **Orders**: Creation with multiple items, status transitions, finance filter (only paid/refunded)
- **Payments (OCTO)**: One‑stage auto-capture prepare_payment, webhook (notify) handling, refunds
## Maintenance/cleanup notes

- Deprecated/unused modules removed: `app/api/endpoints/food.py`, `app/api/endpoints/system.py`, `app/crud/food.py`, `app/schemas/simple_order.py`.
- Slipper replaces legacy "food" naming everywhere; no public routes were removed.
- Health diagnostics kept at `/health`; extended diagnostics endpoint was removed.
- Requirements pruned slightly; if you need DNS or Alembic templates on deploy, keep `dnspython`, `Mako`, and `PyYAML`.

- **Caching Layer**: In‑memory TTL cache with pattern invalidation
- **Security & Performance**: Rate limiting, security headers, gzip, performance timing headers
- **Async Stack**: FastAPI + SQLAlchemy 2.0 async + SQLite (dev) / PostgreSQL (recommended)

## Setup

1. **Install dependencies**
  ```bash
  pip install -r requirements.txt
  ```

2. **Environment Configuration** – create a `.env` file in project root:
  ```env
  # --- Core ---
  DATABASE_URL=sqlite+aiosqlite:///./slippers.db
  # For production switch to PostgreSQL:
  # DATABASE_URL=postgresql+asyncpg://user:password@localhost/slippers
  SECRET_KEY=change_me_strong_secret
  ALGORITHM=HS256
  ACCESS_TOKEN_EXPIRE_MINUTES=15
  REFRESH_TOKEN_EXPIRE_DAYS=7
  ALLOWED_ORIGINS=http://localhost:3000,https://your-frontend.domain

  # --- OCTO Payments ---
  OCTO_API_BASE=https://secure.octo.uz
  OCTO_SHOP_ID=your_shop_id
  OCTO_SECRET=your_secret
  OCTO_RETURN_URL=https://your-frontend.domain/
  OCTO_NOTIFY_URL=https://your-backend.domain/payments/octo/notify
  OCTO_LANGUAGE=ru
  OCTO_AUTO_CAPTURE=true
  OCTO_CURRENCY=UZS
  OCTO_TEST=true
  # Optional JSON (must be a single line):
  # OCTO_EXTRA_PARAMS={"ui":{"ask_for_email":false}}

  # --- Optional refund min USD logic ---
  # OCTO_USD_UZS_RATE=12600

  # --- Rate limiting ---
  RATE_LIMIT_REQUESTS=100
  RATE_LIMIT_WINDOW_SEC=60
  RATE_LIMIT_EXCLUDE_PATHS=/docs,/redoc,/openapi.json,/favicon.ico,/static
  DEBUG=True
  ```

3. **Database Setup**
  - SQLite: file auto‑created.
  - PostgreSQL: create DB manually; optionally apply Alembic migrations (future improvement).

4. **Run the application**
  ```bash
  python -m uvicorn app.main:app --reload
  ```

5. **Initialize sample data (optional)**
  ```bash
  python init_system.py
  ```

## API Endpoints

### Authentication
- `POST /auth/register` - Register a new user
- `POST /auth/login` - Login and get access/refresh tokens
- `POST /auth/refresh` - Get new access token using refresh token
- `POST /auth/reset-password` - Reset user password
- `POST /auth/logout` - Logout (client-side token deletion)

### Users (Admin only)
- `GET /users/` - List all users
- `GET /users/{user_id}` - Get user details
- `DELETE /users/{user_id}` - Delete user

### Slippers (Admin only for create/update/delete)
- `GET /slippers/` - List slippers (pagination, search, sort, category filter)
- `GET /slippers/{id}` - Retrieve slipper (optional images)
- `POST /slippers/` - Create slipper (no image)
- `PUT /slippers/{id}` - Update slipper
- `DELETE /slippers/{id}` - Delete slipper
- `POST /slippers/{id}/upload-images` - Upload up to 10 images
- `GET /slippers/{id}/images` - List images
- `DELETE /slippers/{id}/images/{image_id}` - Delete image

### Orders
- `GET /orders/` - List orders (user restricted; admin sees all)
- `GET /orders/?finance=paid_refunded` - Finance view (only orders whose latest payment is PAID or REFUNDED)
- `POST /orders/` - Create order with items
- `PUT /orders/{order_id}` - Update order
- `DELETE /orders/{order_id}` - Delete order

### Payments (OCTO)
- `POST /payments/octo/create` - Create OCTO payment (accepts amount / total_sum aliases and orderId)
- `POST /payments/octo/refund` - Refund by `octo_payment_UUID`
- `POST /payments/octo/notify` - Webhook (configured in OCTO panel). Updates payment + order status.

Webhook will set order status to confirmed on final success statuses: `paid,captured,completed,succeeded`.

## Usage Examples

### Register a new user
```bash
curl -X POST "http://localhost:8000/auth/register" \
  -H "Content-Type: application/json" \
  -d '{"username": "john_doe", "email": "john@example.com", "password": "password123"}'
```

### Login
```bash
curl -X POST "http://localhost:8000/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "john_doe", "password": "password123"}'
```

### Create slipper (Admin only)
```bash
curl -X POST "http://localhost:8000/slippers/" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Cozy Home Slipper", "size": "42", "price": 19.99, "quantity": 10, "category_id": 1}'
```

### Create order
```bash
curl -X POST "http://localhost:8000/orders/" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":1, "items":[{"slipper_id":1, "quantity":2}]}'
```

### Create OCTO payment
```bash
curl -X POST "http://localhost:8000/payments/octo/create" \
  -H "Content-Type: application/json" \
  -d '{"amount": 50000, "description": "Order #123", "orderId": 1}'
```

### Refund OCTO payment
```bash
curl -X POST "http://localhost:8000/payments/octo/refund" \
  -H "Content-Type: application/json" \
  -d '{"octo_payment_UUID": "uuid-here", "amount": 50000}'
```

## Token Management

- **Access Token**: Valid for 15 minutes (configurable)
- **Refresh Token**: Valid for 7 days
- Use the refresh token to get a new access token when it expires

## Core Models (simplified)

- **User**: name, surname, phone_number, hashed_password, is_admin, created_at
- **Category**: name, description, is_active
- **Slipper**: name, size, price, quantity, category_id, image, timestamps
- **SlipperImage**: slipper_id, path, order_index, flags
- **Order**: order_id, user_id, status, total_amount + items
- **OrderItem**: order_id, slipper_id, quantity, unit_price
- **Payment**: order_id, amount, status (CREATED,PENDING,PAID,FAILED,CANCELLED,REFUNDED), external refs

## Security & Performance

- Password hashing (bcrypt)
- JWT tokens in HttpOnly cookies (mitigates XSS token theft)
- Rate limiting (global + configurable exclusions)
- Security headers middleware (CSP skeleton, can be extended)
- Input validation via Pydantic v2
- Optional gzip compression
- Caching layer (in-memory) with key prefix + invalidation

## Production Notes

- Prefer PostgreSQL over SQLite (concurrency & reliability)
- Set `DEBUG=False` and tighten `ALLOWED_ORIGINS`
- Use a proper process manager (systemd, supervisor) + reverse proxy (nginx)
- Configure HTTPS for secure cookies (`COOKIE_SECURE=True`)
- Keep `OCTO_NOTIFY_URL` publicly reachable and return 200 quickly
- Add webhook signature validation (TODO enhancement)
- Consider adding Alembic migrations before schema changes

## Future Improvements

- Webhook signature / HMAC verification
- Idempotency key handling for notify events
- Postgres migration & indexes review
- Background task for clearing stale PENDING payments
- Redis cache backend option

---

Happy hacking 🥿