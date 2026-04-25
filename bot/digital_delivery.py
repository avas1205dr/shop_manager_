# -*- coding: utf-8 -*-
"""
Доставка цифрового контента покупателю.

Поддерживает как одиночные элементы (текст/ссылка/фото/видео/аудио/голос/
видеосообщение/анимация/документ), так и пакеты из нескольких элементов
(`kind == 'bundle'`, content — JSON-список словарей вида
`{"kind": <одиночный_kind>, "content": <строка>}`).

Используется и менеджер-ботом, и магазин-ботами, чтобы логика отправки была
единственной точкой правды.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

logger = logging.getLogger(__name__)


async def send_digital_item(sender: Bot, customer_id: int, kind: str,
                            content: str, caption: Optional[str] = None) -> bool:
    """Шлёт ОДИН элемент цифрового контента указанным ботом.

    `kind` — один из перечисленных в `database.DIGITAL_CONTENT_KINDS`,
    кроме «bundle» (пакет обрабатывается выше).
    """
    try:
        if kind == "text":
            body = (caption + "\n\n" if caption else "") + content
            await sender.send_message(customer_id, body, parse_mode=ParseMode.HTML)
        elif kind == "url":
            body = (caption + "\n\n" if caption else "") + f"🔗 {content}"
            await sender.send_message(customer_id, body, parse_mode=ParseMode.HTML)
        elif kind == "photo_path":
            await sender.send_photo(customer_id, FSInputFile(content),
                                    caption=caption or None, parse_mode=ParseMode.HTML)
        elif kind == "video_path":
            await sender.send_video(customer_id, FSInputFile(content),
                                    caption=caption or None, parse_mode=ParseMode.HTML)
        elif kind == "audio_path":
            await sender.send_audio(customer_id, FSInputFile(content),
                                    caption=caption or None, parse_mode=ParseMode.HTML)
        elif kind == "voice_path":
            await sender.send_voice(customer_id, FSInputFile(content),
                                    caption=caption or None, parse_mode=ParseMode.HTML)
        elif kind == "video_note_path":
            # Кружок не поддерживает caption — отправляем подпись отдельным
            # сообщением, если она задана.
            await sender.send_video_note(customer_id, FSInputFile(content))
            if caption:
                await sender.send_message(customer_id, caption, parse_mode=ParseMode.HTML)
        elif kind == "animation_path":
            await sender.send_animation(customer_id, FSInputFile(content),
                                        caption=caption or None, parse_mode=ParseMode.HTML)
        elif kind == "file_path":
            await sender.send_document(customer_id, FSInputFile(content),
                                       caption=caption or None, parse_mode=ParseMode.HTML)
        elif kind == "photo_id":
            # Старый формат: file_id от того же бота, что грузил файл.
            await sender.send_photo(customer_id, content, caption=caption or None,
                                    parse_mode=ParseMode.HTML)
        elif kind == "file_id":
            await sender.send_document(customer_id, content, caption=caption or None,
                                       parse_mode=ParseMode.HTML)
        else:
            logger.error(f"send_digital_item: unknown kind={kind!r}")
            return False
        return True
    except Exception as e:
        logger.error(f"send_digital_item failed kind={kind} customer={customer_id}: {e}")
        return False


def parse_bundle(content: Optional[str]) -> List[Dict[str, Any]]:
    """Парсит JSON-список элементов bundle. Возвращает пустой список,
    если не валидно — это даёт безопасный fallback при битых данных.

    Каждый элемент — {"kind": str, "content": str, "caption": Optional[str]}."""
    if not content:
        return []
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        k = item.get("kind")
        c = item.get("content")
        cap = item.get("caption")
        if isinstance(k, str) and isinstance(c, str) and k and c:
            entry: Dict[str, Any] = {"kind": k, "content": c}
            if isinstance(cap, str) and cap:
                entry["caption"] = cap
            out.append(entry)
    return out


def serialize_bundle(items: List[Dict[str, Any]]) -> str:
    """Сериализует пакет в JSON-строку для записи в БД."""
    safe: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict) or not it.get("kind") or not it.get("content"):
            continue
        entry: Dict[str, Any] = {
            "kind": str(it["kind"]),
            "content": str(it["content"]),
        }
        cap = it.get("caption")
        if isinstance(cap, str) and cap:
            entry["caption"] = cap
        safe.append(entry)
    return json.dumps(safe, ensure_ascii=False)


async def deliver_digital(sender: Bot, customer_id: int,
                          kind: Optional[str], content: Optional[str],
                          header: str = "") -> bool:
    """Доставляет цифровой контент (одиночный или пакет) покупателю.

    Возвращает True, если удалось отправить хотя бы один элемент.
    `header` — заголовок (название товара, срок действия и т.п.); для пакета
    шлётся отдельным сообщением перед элементами, для одиночного — caption'ом.
    """
    if not kind or not content:
        return False
    if kind == "bundle":
        items = parse_bundle(content)
        if not items:
            return False
        if header:
            try:
                await sender.send_message(customer_id, header, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"deliver_digital: header failed: {e}")
        ok_any = False
        for item in items:
            ok = await send_digital_item(sender, customer_id,
                                         item["kind"], item["content"],
                                         caption=item.get("caption"))
            ok_any = ok_any or ok
        return ok_any
    return await send_digital_item(sender, customer_id, kind, content,
                                   caption=header or None)
