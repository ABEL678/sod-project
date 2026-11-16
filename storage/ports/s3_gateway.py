"""Порт S3-шлюза для Data Lake (ГИП-Ассистент).

Абстрактный интерфейс; реализация — storage/adapters/s3_boto3.py.
"""

from abc import ABC, abstractmethod
from typing import Optional


class S3Gateway(ABC):
    """Порт для операций с S3-совместимым хранилищем."""

    @abstractmethod
    def ensure_bucket(self) -> None:
        """Создаёт бакет, если его нет."""

    @abstractmethod
    def put_json_object(self, key: str, data: bytes) -> None:
        """Кладёт объект (JSON) по ключу."""

    @abstractmethod
    def get_json_object(self, key: str) -> Optional[dict]:
        """Читает JSON-объект по ключу; None если не найден."""

    @abstractmethod
    def list_keys(self, prefix: str) -> list[str]:
        """Список ключей с данным префиксом."""

    @abstractmethod
    def move_object(self, source: str, dest: str) -> None:
        """Перемещает/переименовывает объект."""

    @abstractmethod
    def delete_object(self, key: str) -> None:
        """Удаляет объект."""

    @abstractmethod
    def object_exists(self, key: str) -> bool:
        """Проверяет существование объекта."""