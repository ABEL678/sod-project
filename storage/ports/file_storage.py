from typing import Protocol, runtime_checkable


@runtime_checkable
class FileStorage(Protocol):
    """Порт файлового хранилища.

    Домен не знает, где лежат файлы: на локальном диске, в S3 или
    в SharePoint. Адаптеры реализуют этот интерфейс.
    """

    def save(self, path: str, file) -> str:
        ...

    def get(self, path: str):
        ...

    def copy(self, source_path: str, target_path: str) -> str:
        ...

    def delete(self, path: str) -> None:
        ...
