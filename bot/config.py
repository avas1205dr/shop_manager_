"""
config.py — загрузка настроек из переменных окружения (.env).

Все секреты и параметры платформы берутся из переменных окружения. Файл `.env`
не должен попадать в репозиторий (см. .gitignore). Шаблон лежит в `.env.example`.
"""

import os
from pathlib import Path
from typing import List

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


def _parse_id_list(raw: str) -> List[int]:
    if not raw:
        return []
    out: List[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out


# ── Секреты ──────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
PAYMENTS_TOKEN: str = os.getenv("PAYMENTS_TOKEN", "").strip()

# ── Модерация / Владельцы ────────────────────────────────────────────────
# Telegram-ID владельцев главного бота (могут удалять любые магазины,
# закрывать споры, видят все жалобы). Несколько ID указываются через запятую.
OWNER_IDS: List[int] = _parse_id_list(os.getenv("OWNER_IDS", ""))

# Telegram-ID модераторов: видят жалобы и могут модерировать магазины,
# но не могут удалять магазины (это право только владельцев).
MODERATOR_IDS: List[int] = _parse_id_list(os.getenv("MODERATOR_IDS", ""))

# Порог жалоб, после которого магазин автоматически переводится в статус
# `under_review`. Новые покупки в нём блокируются до решения модератора.
try:
    COMPLAINT_THRESHOLD: int = int(os.getenv("COMPLAINT_THRESHOLD", "5"))
except ValueError:
    COMPLAINT_THRESHOLD = 5
if COMPLAINT_THRESHOLD < 1:
    COMPLAINT_THRESHOLD = 1

# ── База данных ──────────────────────────────────────────────────────────
DB_PATH: str = os.getenv("DB_PATH", "db/shop_manager.db").strip() or "db/shop_manager.db"

# ── Логирование ──────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"


def is_owner(user_id: int) -> bool:
    return isinstance(user_id, int) and user_id in OWNER_IDS


def is_moderator(user_id: int) -> bool:
    return isinstance(user_id, int) and (user_id in OWNER_IDS or user_id in MODERATOR_IDS)


def assert_bot_token() -> None:
    """Бросает RuntimeError, если BOT_TOKEN не задан."""
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан. Создайте файл .env (см. .env.example) и пропишите BOT_TOKEN."
        )
