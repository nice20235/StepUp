# StepUp API

FastAPI-based stepup ordering & payment system with user authentication, catalog, orders, and Stripe payment integration.

## Features

- **Authentication**: JWT (access + refresh) in HttpOnly cookies
- **Role-Based Access**: Admin vs user protected endpoints
- **StepUp Catalog**: CRUD + pagination, search, sorting, category filter, multi-image upload
- **Categories**: CRUD & caching
- **Orders**: Creation with multiple items, status transitions, finance filter (only paid/refunded)
- **Payments (Stripe)**: Stripe Checkout (hosted) + webhooks for asynchronous status updates and refunds
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
- `GET /stepups/` - List stepups (pagination, search, sort, category filter)
- `GET /stepups/{id}` - Retrieve stepup (optional images)
- `POST /stepups/` - Create stepup (no image)
- `PUT /stepups/{id}` - Update stepup
- `DELETE /stepups/{id}` - Delete stepup
- `POST /stepups/{id}/upload-images` - Upload up to 10 images
- `GET /stepups/{id}/images` - List images
- `DELETE /stepups/{id}/images/{image_id}` - Delete image
- `POST /orders/` - Create order with items
- `PUT /orders/{order_id}` - Update order
- `DELETE /orders/{order_id}` - Delete order

### Payments
Payment flows are handled via Stripe Checkout and webhook endpoints under `/api`.

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

### Create stepup (Admin only)
```bash
curl -X POST "http://localhost:8000/stepups/" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
```

### Create order
```bash
curl -X POST "http://localhost:8000/orders/" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":1, "items":[{"slipper_id":1, "quantity":2}]}'
```

### Create payment (Stripe Checkout)
Use the Stripe Checkout creation endpoint (see `/api/payments/*`) to create a hosted session for payment.

## Token Management

- **Access Token**: Valid for 15 minutes (configurable)
- **Refresh Token**: Valid for 7 days
- Use the refresh token to get a new access token when it expires

## Core Models (simplified)

- **User**: name, surname, phone_number, hashed_password, is_admin, created_at
- **Category**: name, description, is_active
  - **StepUp**: name, size, price, quantity, category_id, image, timestamps
  - **StepUpImage**: slipper_id, path, order_index, flags
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
- Keep webhook endpoints publicly reachable and return 200 quickly
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
