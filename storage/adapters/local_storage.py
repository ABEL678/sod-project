from django.core.files.storage import default_storage


class LocalFileStorage:
    """Адаптер FileStorage поверх Django Storage (локальный диск/S3)."""

    def save(self, path: str, file) -> str:
        return default_storage.save(path, file)

    def get(self, path: str):
        return default_storage.open(path, 'rb')

    def delete(self, path: str) -> None:
        if not path:
            return
        if default_storage.exists(path):
            default_storage.delete(path)
