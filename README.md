# PayFlow — Payment Gateway Reference Architecture

Учебный проект, демонстрирующий ключевые паттерны backend-разработки
на примере платёжного шлюза.

## Паттерны и технологии

| Паттерн | Где реализован | Зачем |
|---|---|---|
| **Celery + RabbitMQ** | `src/worker/` | Асинхронная обработка платежей |
| **Transactional Outbox** | `src/services/payment.py` | Гарантированная доставка событий в Kafka |
| **Outbox Relay** | `src/kafka/outbox_relay.py` | `SELECT FOR UPDATE SKIP LOCKED` — конкурентная публикация |
| **SELECT FOR UPDATE** | `src/services/payment.py` | Пессимистическая блокировка — защита от double-spending |
| **Idempotency Key** | `src/services/payment.py` | Защита от дублирования платежей |
| **Kafka Consumer** | `src/kafka/consumer.py` | Event-driven downstream processing |
| **DLQ (Dead Letter Queue)** | `src/worker/celery_app.py` | Обработка сообщений, которые не удалось обработать |
| **Exponential Backoff** | `src/worker/tasks.py` | `retry_backoff=True` + jitter — защита от thundering herd |
| **Structured Logging** | `src/logging_config.py` | structlog + correlation_id — JSON-логи для прода |
| **Prometheus Metrics** | `src/metrics.py` | `/metrics` endpoint + бизнес-метрики |
| **Global Error Handling** | `src/middleware.py` | Exception handlers + request logging middleware |
| **Deep Health Check** | `src/health.py` | Проверка PostgreSQL, RabbitMQ, Kafka |
| **Pagination** | `src/routes/payments.py` | `GET /payments?page=1&size=20&status=PENDING` |
| **testcontainers** | `tests/conftest.py` | Реальные PostgreSQL/RabbitMQ/Kafka в тестах |
| **Factory Boy** | `tests/factories.py` | Генерация тестовых данных |

## Архитектура

```
┌──────────┐    POST /payments     ┌───────────┐    /metrics
│  Client   │ ───────────────────> │  FastAPI   │ ◄──── Prometheus
└──────────┘                       │ middleware │
                                   │ structlog  │
                                   └─────┬─────┘
                                         │
                           ┌─────────────┼──────────────┐
                           │   PostgreSQL Transaction    │
                           │                             │
                           │  1. SELECT FOR UPDATE       │
                           │     (lock accounts)         │
                           │  2. Debit / Credit          │
                           │  3. INSERT payment          │
                           │  4. INSERT outbox_event     │
                           │                             │
                           └─────────────┬──────────────┘
                                         │
                 ┌───────────────────────┼────────────────────────┐
                 │                       │                        │
           ┌─────▼──────┐     ┌─────────▼─────────┐    ┌────────▼────────┐
           │   Celery    │     │   Outbox Relay     │    │ Kafka Consumer  │
           │   Worker    │     │ (Celery Beat)      │    │ (downstream)    │
           │  (RabbitMQ) │     │ SKIP LOCKED→Kafka  │    │ event routing   │
           └──────┬──────┘     └───────────────────┘    └─────────────────┘
                  │
          ┌───────┼────────┐
          │ retry_backoff  │
          │ max_retries=5  │
          │ jitter=True    │
          └───────┬────────┘
                  │ (on failure)
          ┌───────▼────────┐
          │  Dead Letter    │
          │  Queue (DLQ)    │
          └────────────────┘
```

## Быстрый старт

```bash
# С Makefile (рекомендуется)
make up          # Поднять инфраструктуру
make migrate     # Применить миграции
make api         # Запустить API
make worker      # Запустить Celery worker
make beat        # Запустить Celery Beat
make consumer    # Запустить Kafka consumer
make test        # Запустить тесты

# Или вручную:
docker-compose up -d
pip install -r requirements.txt
alembic upgrade head
uvicorn src.app:app --reload
celery -A src.worker.celery_app worker -l info -Q default,dead_letter
celery -A src.worker.celery_app beat -l info
python -m src.kafka.consumer
```

### Docker (всё в контейнерах)

```bash
cp .env.example .env
docker-compose up -d --build    # или: make up-all
```

## Тесты

```bash
# Все тесты (нужен Docker для testcontainers)
make test                                     # или: pytest -v

# Отдельные группы
pytest tests/test_concurrency.py -v           # SELECT FOR UPDATE
pytest tests/test_outbox_relay.py -v          # Outbox → Kafka
pytest tests/test_celery_tasks.py -v          # Celery retry/DLQ
pytest tests/test_api.py -v                   # API + pagination
pytest tests/test_payment_service.py -v       # Бизнес-логика

# С покрытием
make test-cov
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/accounts` | Создать аккаунт |
| `GET` | `/accounts/{id}` | Получить аккаунт |
| `POST` | `/payments` | Создать платёж (idempotency_key опционален) |
| `GET` | `/payments` | Список платежей (pagination + фильтры) |
| `GET` | `/payments/{id}` | Получить статус платежа |
| `GET` | `/health` | Deep health check (PG + RMQ + Kafka) |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/docs` | Swagger UI |

### Примеры

```bash
# Создать аккаунты
curl -X POST localhost:8000/accounts -H 'Content-Type: application/json' \
  -d '{"name": "Alice", "balance": "1000.00"}'

curl -X POST localhost:8000/accounts -H 'Content-Type: application/json' \
  -d '{"name": "Bob", "balance": "500.00"}'

# Создать платёж (с idempotency key)
curl -X POST localhost:8000/payments -H 'Content-Type: application/json' \
  -H 'X-Request-ID: my-correlation-id' \
  -d '{"from_account_id": "<alice_id>", "to_account_id": "<bob_id>", "amount": "100.00", "idempotency_key": "order-123"}'

# Список платежей с фильтрами
curl 'localhost:8000/payments?page=1&size=10&status=PENDING'

# Health check
curl localhost:8000/health

# Metrics
curl localhost:8000/metrics
```

## Ключевые файлы

```
src/
├── app.py                      # FastAPI + middleware + Prometheus
├── config.py                   # Pydantic Settings (.env)
├── models.py                   # Account, Payment, OutboxEvent
├── database.py                 # SQLAlchemy engine + session
├── schemas.py                  # Pydantic request/response + pagination
├── middleware.py                # ★ Global error handlers + request logging + correlation_id
├── logging_config.py           # ★ structlog (JSON prod / colored dev)
├── health.py                   # ★ Deep health check (PG, RMQ, Kafka)
├── metrics.py                  # ★ Prometheus counters/histograms/gauges
├── routes/
│   └── payments.py             # REST endpoints + pagination
├── services/
│   └── payment.py              # ★ SELECT FOR UPDATE + Transactional Outbox + Idempotency
├── worker/
│   ├── celery_app.py           # ★ Celery config + DLQ routing (RabbitMQ)
│   └── tasks.py                # ★ Exponential backoff + DLQ + metrics
└── kafka/
    ├── consumer.py             # ★ Kafka consumer with event routing
    └── outbox_relay.py         # ★ SELECT FOR UPDATE SKIP LOCKED → Kafka

tests/
├── conftest.py                 # ★ testcontainers (PG, RMQ, Kafka)
├── factories.py                # ★ Factory Boy
├── test_payment_service.py     # Unit-тесты сервиса
├── test_api.py                 # Integration-тесты API + pagination
├── test_concurrency.py         # ★ SELECT FOR UPDATE (threading)
├── test_outbox_relay.py        # Outbox → Kafka (mock + real)
└── test_celery_tasks.py        # ★ Celery retry/DLQ flow

Dockerfile                      # Python 3.12 + app image
docker-compose.yml              # PG + RMQ + Kafka + app + worker + beat + consumer
Makefile                        # make up / test / migrate / api / worker / ...
alembic/                        # DB migrations
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/payments` | PostgreSQL connection |
| `CELERY_BROKER_URL` | `amqp://guest:guest@localhost:5672//` | RabbitMQ broker |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka brokers |
| `KAFKA_PAYMENT_TOPIC` | `payment-events` | Kafka topic |
| `LOG_FORMAT` | `console` | `console` or `json` |
| `LOG_LEVEL` | `INFO` | Logging level |
| `OUTBOX_RELAY_INTERVAL_SECONDS` | `5.0` | Outbox polling interval |
