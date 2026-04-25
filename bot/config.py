"""
config.py — загрузка настроек из переменных окружения (.env).

Все секреты и параметры платформы берутся из переменных окружения. Файл `.env`
не должен попадать в репозиторий (см. .gitignore). Шаблон лежит в `.env.example`.
"""

import logging
import os
from pathlib import Path
from typing import List, Optional

# Диагностика: куда именно мы посмотрели и какой `.env` подхватили.
ENV_FILE_LOADED: Optional[str] = None
ENV_PATHS_TRIED: List[str] = []


def _load_env_file() -> None:
    """Ищет `.env` в нескольких вероятных местах и загружает первый найденный."""
    global ENV_FILE_LOADED, ENV_PATHS_TRIED

    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    candidates: List[Path] = []
    here = Path(__file__).resolve()
    candidates.append(here.parent.parent / ".env")              # <repo>/.env
    candidates.append(here.parent / ".env")                     # <repo>/bot/.env
    try:
        candidates.append(Path.cwd() / ".env")                  # CWD/.env
    except OSError:
        pass

    seen: set = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if str(resolved) in seen:
            continue
        seen.add(str(resolved))
        ENV_PATHS_TRIED.append(str(resolved))
        if resolved.is_file():
            # override=True: перезаписываем существующие переменные окружения
            # на значения из .env (полезно при перезапуске из IDE).
            load_dotenv(resolved, override=True)
            ENV_FILE_LOADED = str(resolved)
            return


_load_env_file()


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

# ── Финансы ──────────────────────────────────────────────────────────────
# Минимальная сумма вывода в рублях. Запросы на меньшую сумму бот не пропускает.
try:
    MIN_WITHDRAWAL: int = int(os.getenv("MIN_WITHDRAWAL", "1000"))
except ValueError:
    MIN_WITHDRAWAL = 1000
if MIN_WITHDRAWAL < 0:
    MIN_WITHDRAWAL = 0

# ── Логирование ──────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"


def mask_secret(value: Optional[str], visible: int = 4) -> str:
    """Возвращает безопасную для показа маску секрета: `1234…dPjK`.

    Используется для отображения токенов, которые нельзя показывать пользователю
    целиком (бот-токены магазинов, реквизиты и т.п.).
    """
    if not value:
        return "—"
    s = str(value).strip()
    if len(s) <= visible * 2:
        return "…" + s[-visible:] if s else "—"
    return f"{s[:visible]}…{s[-visible:]}"


def is_owner(user_id: int) -> bool:
    return isinstance(user_id, int) and user_id in OWNER_IDS


def is_moderator(user_id: int) -> bool:
    return isinstance(user_id, int) and (user_id in OWNER_IDS or user_id in MODERATOR_IDS)


def assert_bot_token() -> None:
    """Бросает RuntimeError, если BOT_TOKEN не задан, и пишет диагностику путей."""
    if BOT_TOKEN:
        if ENV_FILE_LOADED:
            logging.getLogger(__name__).info("Загружен .env: %s", ENV_FILE_LOADED)
        return
    tried = "\n".join(f"  - {p}" for p in ENV_PATHS_TRIED) or "  (поиск не выполнялся — нет python-dotenv?)"
    raise RuntimeError(
        "BOT_TOKEN не задан.\n"
        "Я искал .env в следующих местах:\n"
        f"{tried}\n"
        f"Найден и загружен: {ENV_FILE_LOADED or 'НЕТ'}\n\n"
        "Положите файл .env (см. .env.example) рядом с папкой bot/ и пропишите BOT_TOKEN.\n"
        "На Windows проверьте, что файл назван именно `.env` (без `.txt` на конце) и в кодировке UTF-8 без BOM."
    )
