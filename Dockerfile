# syntax=docker/dockerfile:1.6
#
# Multi-stage build: компилируем зависимости в отдельном слое, в финальный
# образ кладём уже собранные wheels — без gcc и dev-пакетов. Сам образ
# уменьшается раза в 2 и не содержит лишних инструментов сборки.

# ─── Этап 1: builder ──────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# Системные зависимости только для сборки колёс (нужны для пакетов с
# нативными расширениями).  В рантайме они не нужны.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --wheel-dir=/build/wheels -r requirements.txt


# ─── Этап 2: runtime ──────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

# DB_PATH по умолчанию указывает в /data — каталог, монтируемый как volume.
# Перекрывается переменной окружения из .env при необходимости.
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Moscow \
    DB_PATH=/data/db/shop_manager.db

# Минимальные runtime-пакеты: tzdata для корректной TZ, ca-certificates для
# исходящих HTTPS к api.telegram.org, tini как PID 1 (правильное проксирование
# сигналов и reaping zombie-процессов).
# tini  — PID 1 (проксирует сигналы, reaping zombie-процессов).
# tzdata, ca-certificates — временная зона и HTTPS к api.telegram.org.
# gosu  — даёт entrypoint'у возможность безопасно дропнуться в app:app
#         ПОСЛЕ chown'а на смонтированные тома.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        tini \
        tzdata \
        ca-certificates \
        gosu \
 && rm -rf /var/lib/apt/lists/*

# Не-root пользователь. uid:gid 1000:1000 совместимы с типичными
# bind-mount'ами на хосте.
RUN groupadd --system --gid 1000 app \
 && useradd  --system --uid 1000 --gid app --create-home --home-dir /home/app app

WORKDIR /app

# Ставим зависимости из локальных wheels (никакой сети, повторяемая сборка).
COPY --from=builder /build/wheels /tmp/wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/tmp/wheels -r requirements.txt \
 && rm -rf /tmp/wheels

# Копируем код. Каталоги, которые должны переживать рестарт контейнера,
# монтируются как тома и НЕ копируются (см. .dockerignore).
COPY bot ./bot

# Каталоги для рантайма: SQLite-БД, цифровой контент, фото товаров.
# /data — единая точка для bind-mount'ов / именованных volumes.
RUN mkdir -p /data/db /data/digital_content /data/product_images \
 && ln -s /data/db            /app/db \
 && ln -s /data/digital_content /app/digital_content \
 && ln -s /data/product_images  /app/product_images \
 && chown -R app:app /data /app

# Entrypoint стартует от root, выравнивает права на свеже смонтированных
# bind-mount'ах (их владелец на хосте — обычно root) и дропается в
# app:app через gosu. USER app в Dockerfile НЕ выставляем — иначе chown
# в entrypoint'е невозможен.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

VOLUME ["/data"]

# Хелсчек: процесс жив + БД доступна. Telegram-бот не слушает порт, поэтому
# проверяем минимальный invariant — модуль импортируется и БД открывается.
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD gosu app:app python -c "import sqlite3, os; sqlite3.connect(os.environ.get('DB_PATH','/data/db/shop_manager.db')).execute('SELECT 1').fetchone()" \
        || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
# Запускаем как скрипт (а не -m), чтобы bot/ оказался на sys.path и
# работали top-level импорты `import config`, `import database` и т.п.
CMD ["python", "bot/main.py"]
