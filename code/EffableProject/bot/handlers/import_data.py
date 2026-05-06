from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Dict, Optional

from aiogram.types import Document, Message

from ..services.import_service import import_user_data_from_export_payload

logger = logging.getLogger(__name__)


@dataclass
class ImportDraft:
    step: str  # awaiting_document


_drafts: Dict[int, ImportDraft] = {}


async def start_import_flow(message: Message, telegram_user_id: int) -> None:
    _drafts[telegram_user_id] = ImportDraft(step="awaiting_document")
    await message.answer("Отправь JSON-файл с данными для восстановления")


def _is_json_filename(name: Optional[str]) -> bool:
    if not name:
        return False
    return name.lower().endswith(".json")


async def _download_document_to_path(message: Message, document: Document, path: str) -> None:
    """
    Скачивает telegram document в файл `path`.
    Пишем максимально совместимо с aiogram v3 (и с запасным вариантом).
    """
    bot = message.bot
    try:
        # aiogram v3: bot.download(document, destination=...)
        await bot.download(document, destination=path)
        return
    except Exception:
        # Fallback: get_file + download_file
        file = await bot.get_file(document.file_id)
        await bot.download_file(file.file_path, destination=path)


async def try_handle_import_message(message: Message, telegram_user_id: int) -> bool:
    """
    Перехватывает сообщения, если пользователь находится в сценарии /import.
    Возвращает True, если сообщение было обработано и дальше его обрабатывать не нужно.
    """
    draft = _drafts.get(telegram_user_id)
    if draft is None:
        return False

    # Если пользователь ушёл в другую команду — выходим из сценария /import.
    user_text = (message.text or "").strip()
    if user_text.startswith("/"):
        _drafts.pop(telegram_user_id, None)
        return False

    if draft.step != "awaiting_document":
        _drafts.pop(telegram_user_id, None)
        return False

    if message.document is None:
        await message.answer("Я жду JSON-файл (как документ). Пришли файл, пожалуйста.")
        return True

    doc = message.document
    if (doc.mime_type and doc.mime_type != "application/json") and not _is_json_filename(doc.file_name):
        await message.answer("Похоже, это не JSON. Пришли файл `.json`, который был получен через /export.")
        return True

    # Сразу отвечаем, чтобы пользователь понимал, что файл принят и идёт работа.
    await message.answer("Отлично, сейчас импортирую данные...")

    fd, path = tempfile.mkstemp(prefix=f"import_{telegram_user_id}_", suffix=".json")
    os.close(fd)
    try:
        await _download_document_to_path(message, doc, path)
        with open(path, "rb") as f:
            raw = f.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8-sig")

        try:
            payload = json.loads(text)
        except Exception:
            await message.answer("Не смог разобрать JSON. Убедись, что это файл, полученный через /export.")
            return True

        try:
            imported = await import_user_data_from_export_payload(
                telegram_user_id=telegram_user_id,
                payload=payload,
            )
        except Exception:
            logger.exception("Import failed telegram_user_id=%s", telegram_user_id)
            await message.answer(
                "Импорт не удался: файл имеет неправильную структуру или данные невалидны."
            )
            return True

        _drafts.pop(telegram_user_id, None)
        await message.answer("Данные успешно импортированы")
        return True
    finally:
        _drafts.pop(telegram_user_id, None)
        try:
            os.remove(path)
        except Exception:
            logger.exception("Failed to remove temp import file: %s", path)

