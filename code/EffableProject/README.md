# Telegram Emotion Bot (v2)

Telegram-бот для отслеживания эмоционального состояния пользователя с поддержкой AI-ассистента.

Бот:
- **раз в сутки** (в 21:00 по локальному времени сервера) отправляет пользователю сообщение:  
  "Привет! Как прошел твой день?"
- пишет **всем пользователям, которые хотя бы раз написали боту**
- **общается с пользователем через LLM** — отвечает как мягкий и внимательный психолог: выслушивает, поддерживает, не даёт прямых советов
- хранит **контекст диалога** (последние 40 сообщений) для каждого пользователя

## Установка

1. **Клонируй или скопируй** проект в отдельную папку.
2. Убедись, что установлен **Python 3.10+**.
3. Установи зависимости:

```bash
pip install -r requirements.txt
```

## Настройка

### 1. Telegram-бот

1. Создай Telegram-бота через `@BotFather` и получи **BOT_TOKEN**.

### 2. OpenRouter (LLM)

Бот использует [OpenRouter](https://openrouter.ai/) для доступа к LLM-моделям.

1. Зарегистрируйся на [openrouter.ai](https://openrouter.ai/).
2. Перейди в раздел **Keys** → создай новый API-ключ.
3. Скопируй полученный ключ.

### 3. Файл `.env`

Создай файл `.env` в корне проекта (рядом с `requirements.txt`) со следующим содержимым:

```env
BOT_TOKEN=1234567890:ABCDEF_your_real_token_here
OPENROUTER_API_KEY=sk-or-v1-your_openrouter_key_here
```

По умолчанию используется бесплатная модель `google/gemma-2-9b-it:free`.  
Чтобы выбрать другую модель, добавь переменную:

```env
OPENROUTER_MODEL=meta-llama/llama-3.1-8b-instruct:free
```

Актуальный список бесплатных моделей: [openrouter.ai/models?q=free](https://openrouter.ai/models?q=free)

> **Важно:** файл `.env` добавлен в `.gitignore` и не попадёт в репозиторий.

### 4. Подключение к PostgreSQL (`DATABASE_URL`)

Проект поддерживает:

- `DATABASE_URL` (предпочтительно)
- или раздельные `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`

Пример для локального запуска:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/effable_bot
```

В Docker Compose **`localhost` внутри контейнера не указывает на PostgreSQL контейнер**. Поэтому в `docker-compose.yml` для сервиса `bot` `DATABASE_URL` переопределяется на хост `postgres` (имя сервиса в сети compose).

## Запуск

### Локально (без Docker)

Из папки проекта (где лежит `requirements.txt`) выполни:

```bash
python -m bot.main
```

### Через Docker Compose (PostgreSQL + бот)

Из папки проекта (где лежат `docker-compose.yml` и `Dockerfile`):

1. Создай `.env` по образцу `.env.example`.
2. Запуск:

```bash
docker compose up --build
```

Остановка:

```bash
docker compose down
```

Логи:

```bash
docker compose logs -f
```

Перед стартом бота контейнер выполняет `alembic upgrade head`. PostgreSQL ждётся по `healthcheck` (`pg_isready`), затем стартует бот.

### Проверка, что Docker-версия реально работает

1. Посмотреть статусы:

```bash
docker compose ps
```

2. Посмотреть логи бота:

```bash
docker compose logs -f bot
```

3. Убедиться, что бот отвечает в Telegram: напиши ему любое сообщение.

После успешного запуска (локально или в Docker):
- бот начнет принимать сообщения и **отвечать через AI-ассистента**
- все пользователи, которые написали боту хотя бы раз, будут считаться «подписчиками»
- каждый день в **21:00** по локальному времени сервера бот отправит всем подписчикам вопрос:

> Привет! Как прошел твой день?

## Архитектура

```
Пользователь → Telegram → aiogram (handle_any_message)
                                │
                                ├─ user_id → known_users (подписка на ежедневные сообщения)
                                └─ текст → llm.get_response()
                                              │
                                              ├─ история диалога (в памяти, до 40 сообщений)
                                              ├─ системный промт «мягкого психолога»
                                              └─ OpenRouter API → ответ LLM → пользователю
```

## Ограничения

- История диалогов и список пользователей хранятся **в памяти** — при перезапуске бота они сбрасываются.
- Бесплатные модели OpenRouter могут иметь ограничения по скорости и доступности.
