# StepUp API

StepUp — это backend API на FastAPI для интернет‑магазина обуви:
аутентификация пользователей, каталог товаров (StepUp/slippers), корзина, заказы
и интеграция с интернет‑эквайрингом через JSON‑RPC.

## Основные возможности

- **Аутентификация**: JWT (access + refresh), HttpOnly cookies, сессионные ограничения.
- **Роли**: обычный пользователь и администратор (админ‑эндпоинты защищены).
- **Каталог StepUp**: CRUD, пагинация, поиск, сортировка, фильтрация по категориям,
  мультизагрузка изображений.
- **Категории**: CRUD + кэширование для ускорения списков.
- **Корзина**: добавление товаров по `product_id`, объединение позиций,
  автоматический пересчёт `subtotal` и `total_amount` в UZS.
- **Заказы**: создание заказов на основе корзины, статусы заказов.
- **Интернет‑эквайринг**:
  - универсальный JSON‑RPC 2.0 эндпоинт `/api/rpc` для методов
    `CheckPerformTransaction`, `CreateTransaction`, `PerformTransaction`,
    `CancelTransaction`, `CheckTransaction`, `GetStatement`;
  - отдельные HTTP‑клиенты для REST‑API эквайера (`AcquiringClient`, `EkayringClient`).
- **Кэширование**: in‑memory TTL‑кэш с инвалидцией по ключам.
- **Безопасность и производительность**: rate limiting, security‑заголовки,
  gzip‑сжатие, метрики времени обработки.

## Технологический стек

- **Ядро**: Python 3.11+, FastAPI, Starlette, Uvicorn.
- **БД**: PostgreSQL (async), SQLAlchemy 2.0 + asyncpg.
- **Валидация и настройки**: Pydantic v2, pydantic‑settings, python‑dotenv.
- **Аутентификация**: JSON Web Token (python‑jose), bcrypt (через зависимости FastAPI).
- **HTTP‑клиенты**: httpx (внутри сервисов эквайринга).

Список зависимостей и их версии см. в `requirements.txt`.

## Быстрый старт

1. **Установить зависимости**

   ```bash
   pip install -r requirements.txt
   ```

2. **Создать `.env` на основе примера**

   ```bash
   cp .env.example .env
   # затем отредактировать .env и поставить реальные значения
   ```

3. **Настроить базу данных**

   - локально можно использовать PostgreSQL на `localhost`;
   - строка подключения задаётся через `DATABASE_URL` в `.env`.

4. **Запустить приложение**

   ```bash
   python -m uvicorn app.main:app --reload
   ```

5. **Инициализировать тестовые данные (опционально)**

   ```bash
   python init_system.py
   ```

После запуска swagger‑документация доступна по адресу `http://127.0.0.1:8000/docs`.

## Краткий обзор API

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

### Каталог и заказы

- `GET /stepups/` — список товаров с фильтрами и пагинацией.
- `GET /stepups/{id}` — карточка товара с изображениями.
- `POST /stepups/` (admin) — создать товар.
- `PUT /stepups/{id}` (admin) — обновить товар.
- `DELETE /stepups/{id}` (admin) — удалить товар.
- `POST /stepups/{id}/upload-images` (admin) — загрузить до 10 изображений.

### Корзина

- `POST /cart/items` — добавить товар в корзину по `product_id` и `quantity`.
  Возвращает агрегированную корзину с суммами в UZS.
- `GET /cart` — получить текущую корзину пользователя.
- `GET /cart/total` — только агрегированные суммы.
- `PUT /cart/items/{cart_item_id}` — изменить количество.
- `DELETE /cart/items/{cart_item_id}` — удалить позицию.
- `DELETE /cart/clear` — очистить корзину.

### Заказы

- `POST /orders/` — создать заказ (обычно на основе корзины).
- `PUT /orders/{order_id}` — обновить заказ/статус.
- `DELETE /orders/{order_id}` — удалить заказ (админ).

### JSON‑RPC / Эквайринг

- `POST /api/rpc` — универсальный JSON‑RPC 2.0 эндпоинт для методов:
  `CheckPerformTransaction`, `CreateTransaction`, `PerformTransaction`,
  `CancelTransaction`, `CheckTransaction`, `GetStatement`.

Для этого эндпоинта используется HTTP Basic Auth с логином/паролем из настроек
`RPC_USERNAME` и `RPC_PASSWORD`.

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

## Token Management

- **Access Token**: Valid for 15 minutes (configurable)
- **Refresh Token**: Valid for 7 days
- Use the refresh token to get a new access token when it expires

## Core Models (simplified)

- **User**: name, surname, phone_number, hashed_password, is_admin, created_at
- **Category**: name, description, is_active
  - **StepUp**: name, size, price, quantity, category_id, image, timestamps
  - **StepUpImage**: slipper_id, path, order_index, flags
  - **Cart**: cart + cart items, связанные с пользователем и с товарами.
  - **Transaction**: запись о платёжной транзакции для интеграции с эквайрингом.

## Security & Performance

- Password hashing (bcrypt)
- JWT tokens in HttpOnly cookies (mitigates XSS token theft)
- Rate limiting (global + configurable exclusions)
- Security headers middleware (CSP skeleton, can be extended)
- Input validation via Pydantic v2
- Optional gzip compression
- Caching layer (in-memory) with key prefix + invalidation

## Заметки по безопасности и продакшену

- **Никогда не коммитьте файл `.env`** — он уже добавлен в `.gitignore`.
- Все секреты (пароли БД, JWT‑ключи, логины/пароли для эквайринга) должны
  задаваться только через переменные окружения или `.env`.
- В `app/core/config.py` прописаны только безопасные плейсхолдеры, которые можно
  публиковать на GitHub; реальные значения нужно переопределять в окружении.
- Перед деплоем установите `DEBUG=False` и ограничьте `ALLOWED_ORIGINS` боевыми
  доменами.
- Запускайте приложение за reverse‑proxy (nginx, Caddy) с TLS, чтобы
  `COOKIE_SECURE=True` работало корректно.

---

Happy hacking 🥿
