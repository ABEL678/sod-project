# S3_MAPPING_EXPORT_PLAN.md — сериализация выгрузки в MinIO (`mapping.py` + `export.py`)

| | |
|---|---|
| Роль | Аудитор (Z.ai / GLM-5.3-Flash, режим Debug). План для Кодера — **в проекте ничего не кодится** |
| База | `HEAD = 4c04e2c` (feat(rfi): stage R1 — RfiTask, RfiStatusLog, 42 tests) |
| Родительский план | `docs/S3_SYNC_PLAN.md` (v1.0) — этот документ детализирует его Шаг 3 (§4) и Этапы B/C раскадки (§7) |
| Проверено по исходникам | Stream-CAD @ `4c04e2c`; ГИП-Ассистент: `backend/src/application/dto/task.py`, `dto/rfi.py`, `domain/value_objects/enums.py`, `adapters/db/models/task.py` |
| Версия документа | v1.0, 16.11.2026 |

---

## 0. Резюме (TL;DR)

Задача: спроектировать модуль сериализации `storage/domain/sync/mapping.py` и
оркестратор `storage/domain/sync/export.py` для выгрузки `tasks.json`,
`rfis.json` и `metadata.json` в **MinIO** (S3-совместимое хранилище на нашем
VDS), с последующим переездом на Cloud.ru только через `.env`.

Ответы на пункты задачи:

| Пункт задачи | Где в документе |
|---|---|
| 1. uuid5 для project / design_task / rfi | §2.3 |
| 2. Маппинг статусов DesignTask → TaskStatus (9 → 6, верхний регистр) | §2.5 |
| 3. `serialize_design_task(task) -> dict` (контракт §3.3.1, соответствие их `TaskRead`) | §2.9, сверка §5.1 |
| 4. `serialize_rfi_task(rfi) -> dict` (контракт §3.3.3, статусы нижнего регистра как их `RfiState`) | §2.10, сверка §5.2 |
| 5. Формирование манифеста `metadata.json` с sha256; учёт обычных S3 PUT в MinIO | §4 (структура и алгоритм), §1 (особенности MinIO) |

Ключевые решения:

1. **Код не меняется из-за MinIO.** Порт/адаптер (Шаг 2 родительского плана)
   написаны против boto3 S3 API — MinIO совместим. Меняются только дефолты
   настроек и добавляются два требования к boto3-клиенту:
   `addressing_style="path"` и `signature_version="s3v4"` (§1.3) — это
   обязательно именно для `http://localhost:9000`.
2. **mapping.py — чистые функции**: принимают объекты (со сделанным
   `select_related`) и скаляры, делают ноль запросов и ноль I/O. Тестируются
   без БД.
3. **NAMESPACE uuid5 зафиксирован литералом**
   `b1ae1443-9dc2-5102-90d2-cbc961a334d2` — обе стороны зашивают одно значение,
   «пересчитать по-другому» невозможно (§2.2).
4. **Манифест — точка коммита поколения**: `tasks.json` → `rfis.json` →
   `metadata.json` (последний, с sha256). MinIO даёт строгую read-after-write
   консистентность и атомарный single-part PUT — схема работает без поправок (§4).
5. **Пропуск без изменений**: перед PUT читаем текущий манифест; если sha256
   обоих файлов и `last_ack_generation` не изменились — выгрузка пропускается,
   generation не растёт (§3.6, новая точка решения 9).

Сверка контрактов по исходникам дала **три поправки к родительскому плану**
(§6): фактический номер RFI — «RFI-07», а не «РИ-007»; `section_code` у RFI —
`null`; поле `last_ack_at` нужно добавить в `SyncState` (Шаг 1).

---

## 1. Контекст и уточнение MinIO

### 1.1. Что уточняется

S3_SYNC_PLAN.md §3.6 писался под Cloud.ru Evolution Object Storage как целевую
площадку. Решение Координатора: тестирование и первый запуск — на **локальном
MinIO на нашем VDS**. Переезд на Cloud.ru позже — только через `.env`
(`S3_ENDPOINT` + ключи), код и контракты не трогаем. Это возможно, потому что:

- и MinIO, и Cloud.ru реализуют один S3 API (`PutObject`, `GetObject`,
  `ListObjectsV2`, `HeadBucket`, `CreateBucket`, `DeleteObject`, `CopyObject`);
- весь код синка обращается к хранилищу только через порт
  `storage/ports/s3_gateway.py` — единственное место, где знает про endpoint,
  это адаптер `storage/adapters/s3_boto3.py` (§3.4.1 родительского плана);
- ни `tasks.json`, ни `rfis.json`, ни `metadata.json` не зависят от площадки.

### 1.2. Настройки (правка §3.6 родительского плана)

`sod_core/settings.py`, конвенция `os.environ.get` (как блок `AI_EMBEDDING_*`,
строки 160–177). Меняются **два дефолта**, остальное — как в §3.6:

| Настройка | Env | Дефолт (обновлён) | Отличие от §3.6 | Смысл |
|---|---|---|---|---|
| `S3_SYNC_ENABLED` | `S3_SYNC_ENABLED` | `False` | — | общий выключатель |
| `S3_ENDPOINT` | `S3_ENDPOINT` | `http://localhost:9000` | **было `https://s3.cloud.ru`** | MinIO на том же VDS, что и Stream-CAD |
| `S3_BUCKET_NAME` | `S3_BUCKET_NAME` | `''` | — | на VDS задаём `stream-cad-sync` в `.env` |
| `S3_ACCESS_KEY` | `S3_ACCESS_KEY` | `''` | — | пользователь MinIO; в `.env.example` — `CHANGE_ME` |
| `S3_SECRET_KEY` | `S3_SECRET_KEY` | `''` | — | только env, никогда не в git |
| `S3_REGION` | `S3_REGION` | `us-east-1` | **было `''`** | MinIO регион не проверяет, но boto3-клиенту нужен валидный; `us-east-1` — безопасный нейтральный |
| `S3_SYNC_SCHEMA_VERSION` | — (константа) | `'1.0'` | — | версия контракта в envelope |

`.env.example` / `.env.production.example` — дополнить блоком «S3 Data Lake
(ГИП-Ассистент)» с `CHANGE_ME`; комментарий в `.env.example` поясняет, что на
VDS сейчас MinIO (localhost:9000), целевая площадка — Cloud.ru.

### 1.3. Требования к boto3-клиенту (дополнение к Шагу 2)

Единственные правки в адаптере `storage/adapters/s3_boto3.py` (псевдокод,
Кодер реализует в Шаге 2):

```python
# storage/adapters/s3_boto3.py — фрагмент (ленивый клиент, как в Шаге 2)
import boto3
from botocore.config import Config

def _get_client(self):
    if self._client is None:
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,        # http://localhost:9000 (MinIO) или https://s3.cloud.ru
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION or "us-east-1",
            config=Config(
                signature_version="s3v4",             # MinIO: только Signature v4
                s3={"addressing_style": "path"},      # ОБЯЗАТЕЛЬНО для localhost (см. ниже)
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=10,                   # как в §3.6
                read_timeout=30,
            ),
        )
    return self._client
```

Почему `addressing_style="path"` — критично именно для MinIO на localhost:

- virtual-host стиль строит адрес `http://<bucket>.localhost:9000/...` —
  DNS-имени `<bucket>.localhost` не существует, клиент упадёт. Path-стиль даёт
  `http://localhost:9000/<bucket>/<key>` — рабочий формат MinIO.
- Cloud.ru поддерживает оба стиля, поэтому path-стиль остаётся корректным и
  после переезда — **один код для обеих площадок**.

Второй нюанс — создание бакета. В MinIO (как и в Cloud.ru) бакет не создаётся
автоматически; добавляем в адаптер метод `ensure_bucket()`:

```python
def ensure_bucket(self):
    client = self._get_client()
    try:
        client.head_bucket(Bucket=settings.S3_BUCKET_NAME)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchBucket"):
            # MinIO с region us-east-1: CreateBucketConfiguration не нужен
            client.create_bucket(Bucket=settings.S3_BUCKET_NAME)
        else:
            raise
```

Вызов: команда `manage.py sync_s3 --ensure-bucket` (Шаг 8) и/или лениво перед
первой выгрузкой. Порт `s3_gateway.py` дополняется методом `ensure_bucket()`.

### 1.4. Свойства MinIO, важные для логики экспорта

| Свойство MinIO | Следствие для нашего кода |
|---|---|
| Строгая read-after-write консистентность (нет eventual consistency старого AWS S3) | Сразу после PUT `metadata.json` читатель видит новое поколение; схема «манифест последним» работает без поправок (§4.3) |
| PUT атомарен для single-part объектов (< 5 ГиБ); наши JSON — единицы КБ…МБ | Читатель видит объект целиком: старую или новую версию — основание для правила сверки sha256 (§4.3) |
| Поддержка версионирования бакета (`mc version enable`) | Соответствует правилу 5 §3.1 родительского плана — включить (§4.4) |
| ETag = MD5 (и непредсказуем при multipart/SSE-KMS) | Целостность считаем **только по sha256 из манифеста**, ETag не используем |
| Нет AWS IAM-политик; свои политики через `mc admin policy` | На MVP — один root-пользователь MinIO (локальный контур доверия VDS); переезд на Cloud.ru — два IAM-пользователя по матрице §3.7.1; код не меняется |
| SSE поддерживается | На этапе MinIO-MVP не включаем (данные в локальной папке VDS); на Cloud.ru включить по §3.7.2.4 — точка решения 12 |
| Размер объекта — не ограничение (наши файлы ≪ лимитов) | Мягкий warn при файле > 10 МБ (защита от «раздувания» items), лимит не блокирует |

### 1.5. Инфраструктура VDS (вне репозитория Stream-CAD)

Не входит в патч Кодера — разворачивается владельцем на VDS. Приводим как
эталон настройки, чтобы Кодер знал окружение.

`docker-compose.minio.yml` (на VDS):

```yaml
services:
  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    ports:
      - "127.0.0.1:9000:9000"   # API — только с самого VDS (за firewall)
      - "127.0.0.1:9001:9001"   # консоль — доступ через SSH-туннель
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}        # сменить стандартные minioadmin/minioadmin!
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes:
      - minio-data:/data
    restart: unless-stopped
volumes:
  minio-data:
```

`.env` Stream-CAD на VDS:

```ini
S3_SYNC_ENABLED=true
S3_ENDPOINT=http://localhost:9000
S3_BUCKET_NAME=stream-cad-sync
S3_ACCESS_KEY=CHANGE_ME            # пользователь MinIO (создать в консоли или mc admin user add)
S3_SECRET_KEY=CHANGE_ME            # только env, никогда не в git
S3_REGION=us-east-1
```

Ручные проверки из консоли VDS (клиент `mc`):

```bash
mc alias set sc http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mb --ignore-existing sc/stream-cad-sync
mc version enable sc/stream-cad-sync          # правило 5 §3.1 родительского плана

mc ls sc/stream-cad-sync/projects/p-25-118/outbox/
mc cat sc/stream-cad-sync/projects/p-25-118/outbox/metadata.json | python3 -m json.tool

# сверка целостности после выгрузки:
mc cat sc/stream-cad-sync/projects/p-25-118/outbox/tasks.json | sha256sum
#   → должно совпасть с files["tasks.json"]["sha256"] из metadata.json
```

### 1.6. Переезд на Cloud.ru (когда понадобится)

1. Бакет + два IAM-пользователя и политики §3.7.1 в консоли Cloud.ru.
2. `.env`: `S3_ENDPOINT=https://s3.cloud.ru`, новые `S3_ACCESS_KEY/S3_SECRET_KEY`,
   регион по документации Cloud.ru.
3. `manage.py sync_s3 --ensure-bucket` и первый полный прогон
   (`sync_s3_full`) зальёт снапшоты всех проектов — перенос данных кодом не
   нужен.
4. Код, контракты, `mapping.py`, `export.py` — **ноль правок**.

---

## 2. Модуль `storage/domain/sync/mapping.py` — чистые функции

### 2.1. Принципы

1. **Ноль I/O и ноль ORM-запросов** внутри функций. Модели Django импортируются
   только для type-hint'ов; объекты приходят со сделанным `select_related` —
   за N+1 отвечает `export.py`.
2. **Детерминизм**: одна и та же строка БД всегда даёт байт-в-байт одинаковый
   dict. Порядок ключей фиксируется литералом dict (он же порядок полей в
   файле — читается глазами так же, как примеры §3.3 родительского плана).
3. **Fail-fast на неизвестном статусе**: `ValueError` с именем статуса. Список
   статусов закрытый и контролируется нашими матрицами переходов; молчаливый
   fallback породил бы невалидное значение enum у партнёра. Одна ошибка
   останавливает выгрузку проекта и попадает в `SyncLog(error)` — это
   диагносцируемо, а не тихо.
4. Даты — `date.isoformat()` (`"YYYY-MM-DD"`), время — UTC с суффиксом `Z`
   (§2.7): у них `TimestampMixin` — `DateTime(timezone=True)`; у нас
   `USE_TZ=True` (settings.py:323), сравнение форматов бесконфликтно.

### 2.2. Константы модуля

```python
# storage/domain/sync/mapping.py

import uuid

# ВАЖНО: литерал, а не uuid5(...) на месте. Значение вычислено один раз и
# зафиксировано в обоих планах; «пересчитать» его нельзя, иначе все uuid5
# проекта изменятся у партнёра (см. тест-защиту §8.1).
S3_SYNC_NAMESPACE = uuid.UUID("b1ae1443-9dc2-5102-90d2-cbc961a334d2")
# эквивалент: uuid.uuid5(uuid.NAMESPACE_URL, "https://stream-cad.ru/s3-sync/v1")

SCHEMA_VERSION = "1.0"          # -> envelope.schema_version, S3_SYNC_SCHEMA_VERSION
SOURCE_SYSTEM  = "stream-cad"   # -> envelope.source_system
TASK_SOURCE    = "stream_cad"   # -> item.source (их String(20))
```

### 2.3. UUID5-идентификаторы (пункт 1 задачи)

Формулы — из родительского плана §3.3.4, без изменений. Seed-строки включают
«kind» (`project:` / `design-task:` / `rfi:`) — защищает от коллизии «одинаковый
pk у разных сущностей»:

```python
def uuid_for_project(project_code: str) -> str:
    """uuid5 проекта. seed: 'project:{code}'."""
    return str(uuid.uuid5(S3_SYNC_NAMESPACE, f"project:{project_code.strip()}"))

def uuid_for_design_task(project_code: str, task_pk: int) -> str:
    """uuid5 задания. seed: 'design-task:{code}:{pk}'."""
    return str(uuid.uuid5(S3_SYNC_NAMESPACE, f"design-task:{project_code.strip()}:{int(task_pk)}"))

def uuid_for_rfi(project_code: str, rfi_pk: int) -> str:
    """uuid5 RFI. seed: 'rfi:{code}:{pk}'."""
    return str(uuid.uuid5(S3_SYNC_NAMESPACE, f"rfi:{project_code.strip()}:{int(rfi_pk)}"))
```

Правила и свойства (фиксируем для Кодера):

1. Возвращаемый тип — `str` (uuid с дефисами, нижний регистр): сразу валиден
   для их `uuid.UUID`-полей pydantic, никакой постобработки.
2. Нормализация кода — только `.strip()` (без `lower()`, без замены символов):
   seed обязан использовать **точно** то значение, что в `Project.code`, иначе
   обратный поиск по uuid5 (родительский план §3.3.4, свойство «б») сломается.
3. Стабильность: uuid5 — чистая функция, значение не зависит от времени,
   окружения и порядка вызовов. Примеры (вычислены фактически):

   | seed | результат |
   |---|---|
   | `project:P-25-118` | `b3840f30-787e-5497-8606-2788bff00f3f` |
   | `design-task:P-25-118:42` | `f5ba96e1-1f26-58bf-b93a-fcac3801e4bb` |
   | `rfi:P-25-118:7` | `25c104f0-dab5-5fb6-87fe-97d39bbf350a` |

4. Смена `Project.code` меняет **все** uuid5 проекта — запрет смены шифра при
   включённом s3-sync уже зафиксирован (§6.5 родительского плана), здесь
   сериализатор не проверяет (guard — в сервисе переименования).
5. Удаление и повторное создание задания/RFI даст новый pk → новый uuid: это
   корректно (новая сущность), история на стороне партнёра связывается по
   `stream_cad_id`.

### 2.4. `project_slug(code)` — ключи бакета

По правилу 1 §3.1 родительского плана; здесь фиксируем точный алгоритм (в
родительском плане он не был детализирован):

```python
import re
_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

def project_slug(project_code: str) -> str:
    """Слаг для S3-ключа. Примеры:
    '123-45.67'  -> '123-45.67'            (уже безопасен)
    'ЖК Север'   -> 'zhk_sever-u3f1a2b4'   (замена + детерминированный суффикс)
    """
    code = project_code.strip()
    slug = _SLUG_UNSAFE.sub("_", code).strip("._-")
    if slug == code and slug:
        return slug[:100]
    suffix = uuid.uuid5(S3_SYNC_NAMESPACE, f"slug:{code}").hex[:8]
    return f"{slug[:91]}-u{suffix}"
```

Замечания: (а) кириллица заменяется на `_` посимвольно — читаемость слага не
гарантируется, юридической связкой всегда остаётся `metadata.json.project.code`;
(б) суффикс `-u{hex8}` добавляется **только** когда была замена (совпадает с
§6.6 родительского плана); (в) `[:100]` — санитарная граница длины ключа для
CLI-удобства.

### 2.5. Маппинг статусов задания 9 → 6 (пункт 2 задачи)

Их `TaskStatus` — `StrEnum` со **значениями в верхнем регистре**
(`domain/value_objects/enums.py:15`): `NOT_STARTED, IN_PROGRESS, REVIEW,
REWORK, DONE, CANCELLED`. В JSON мы отдаём строку — ровно значение enum:

```python
TASK_STATUS_MAP = {
    # DesignTask.Status (models.py:707)  ->  TaskStatus ГИП-Ассистента
    "DRAFT":       "NOT_STARTED",   # черновик ещё не выдан
    "ISSUED":      "NOT_STARTED",   # выдано, исполнитель не начал
    "IN_PROGRESS": "IN_PROGRESS",   # прямое соответствие
    "ON_HOLD":     "NOT_STARTED",   # фактически не идёт; отличать по stream_cad_status (§6.2 род. плана)
    "REVIEW":      "REVIEW",       # прямое соответствие
    "RETURNED":    "REWORK",       # «возвращено на доработку» = их REWORK
    "COMPLETED":   "DONE",         # выполнено, для их матрицы готово
    "ACCEPTED":    "DONE",         # принято; различие хранит stream_cad_status
    "CANCELLED":   "CANCELLED",    # прямое соответствие
}

def map_task_status(status: str) -> str:
    try:
        return TASK_STATUS_MAP[status]
    except KeyError:
        raise ValueError(f"map_task_status: неизвестный статус DesignTask: {status!r}")
```

Ключи — значения `DesignTask.Status` (`storage/domain/models.py:707-716`,
ровно 9 значений). Их `MatrixService._aggregate_status` реагирует на
`DONE/REWORK/IN_PROGRESS` и просрочки — все четыре значения нашего маппинга
покрывают эту логику (§3.3.2 родительского плана). Исходный 9-статус всегда
сохраняется в расширении `stream_cad_status`.

### 2.6. Маппинг `RfiState` → `TaskStatus` (для поля `status` у RFI)

Их RFI — это `Task(is_rfi=true)`, и `RfiRead.status` имеет тип `TaskStatus`
(`dto/rfi.py:46`), а `rfi_state` — `RfiState` **в нижнем регистре**
(`enums.py:156`: `open, sent, answered, registered, closed, escalated`).
Наш `RfiTask.status` совпадает с `RfiState` 1:1 по значениям (решение D2 в
RFI_MODULE_PLAN.md, `models.py:957-964`). Но поле `status` задачи у партнёра
нужно отдельно — родительский план (§3.3.3) определил его не до конца, здесь —
полная таблица:

```python
RFI_STATE_TO_TASK_STATUS = {
    # RfiTask.status (= RfiState, нижний регистр) -> TaskStatus (верхний)
    "open":       "NOT_STARTED",  # создан, запрос ещё не отправлен
    "sent":       "IN_PROGRESS",  # отправлен, ждём ответ — работа идёт
    "answered":   "IN_PROGRESS",  # ответ получен, идёт обработка/регистрация ИД
    "registered": "IN_PROGRESS",  # ИД зарегистрированы, но RFI ещё не закрыт
    "closed":     "DONE",         # закрыт
    "escalated":  "IN_PROGRESS",  # просрочен, эскалирован — работа активна
}

def map_rfi_state_to_task_status(rfi_status: str) -> str:
    try:
        return RFI_STATE_TO_TASK_STATUS[rfi_status]
    except KeyError:
        raise ValueError(f"map_rfi_state_to_task_status: неизвестный статус RfiTask: {rfi_status!r}")
```

Обоснование: `DONE` для партнёра означает «по RFI ничего больше не требуется» —
это справедливо только для `closed`. `registered` и `answered` ещё требуют
действий ГИПа (внести ИД в работу), их агент фильтрует активное по
`rfi_state`, а `status` использует для агрегатов матрицы. Подтвердить с
командой ГИП-Ассистента — точка решения 11.

### 2.7. Служебные функции времени

```python
from datetime import date, datetime, timezone as dt_timezone

def iso_utc(value: datetime | None) -> str | None:
    """Aware datetime -> '2026-11-16T06:30:00Z'. У нас USE_TZ=True, значения aware."""
    if value is None:
        return None
    return value.astimezone(dt_timezone.utc).isoformat().replace("+00:00", "Z")

def date_iso(value: date | None) -> str | None:
    """date -> 'YYYY-MM-DD'."""
    return value.isoformat() if value is not None else None

def full_name(user) -> str | None:
    """ФИО исполнителя для assignee_name; без пользователя — None."""
    if user is None:
        return None
    return user.get_full_name() or user.username
```

### 2.8. Вычисляемые показатели дедлайна

Те же поля, что у них в `CrossTaskTaskRead` (`dto/task.py:58-68`):
`overdue_days` — «сколько дней просрочена (0 если не просрочена)»,
`days_until_deadline` — «сколько дней до дедлайна (отрицательно если
просрочено)». Считаем на нашей стороне, чтобы их импортер не пересчитывал:

```python
GA_OPEN_STATUSES = {"NOT_STARTED", "IN_PROGRESS", "REVIEW", "REWORK"}

def compute_deadline_metrics(due_date, ga_status: str, today: date) -> tuple[int, int | None]:
    if due_date is None:
        return 0, None
    if ga_status not in GA_OPEN_STATUSES:        # DONE / CANCELLED
        return 0, None                            # завершённая задача не «просрочена»
    days_until = (due_date - today).days          # < 0 — просрочено (семантика их поля)
    return max(0, -days_until), days_until
```

`today` передаётся явно (`timezone.localdate()` — московская дата, `TIME_ZONE =
Europe/Moscow`, settings.py:298): дедлайны у обеих сторон — календарные даты
(`Date`), сравнение по дню, не по мгновению. Завершённые задачи считаем
не-просроченными — точка решения 10.

### 2.9. `serialize_design_task(task, *, today=None) -> dict` (пункт 3 задачи)

```python
def serialize_design_task(task, *, today=None) -> dict:
    """DesignTask -> запись tasks.json (контракт S3_SYNC_PLAN.md §3.3.1).

    task приходит со select_related('source_section', 'target_section',
    'author', 'assignee'). Порядок ключей = порядок полей в файле; менять
    порядок нельзя — это меняет sha256 файла без изменения данных.
    """
    code = task.project.code
    if today is None:
        today = timezone.localdate()
    ga_status = map_task_status(task.status)
    overdue_days, days_until = compute_deadline_metrics(task.due_date, ga_status, today)
    return {
        # --- их TaskRead (валидация их pydantic, §5.1) ---
        "id":             uuid_for_design_task(code, task.pk),
        "project_id":     uuid_for_project(code),
        "title":          task.title,                    # CharField(255) <= их max_length=500
        "description":    task.description,              # TextField, обязательный у нас; как есть
        "status":         ga_status,                     # маппинг §2.5, верхний регистр
        "assignee_id":    None,                          # наши UUID пользователей - не их executors
        "deadline":       date_iso(task.due_date),       # DateField nullable
        "section_code":   task.source_section.code,      # PROTECT FK - всегда есть
        "to_section_code": task.target_section.code,     # PROTECT FK - всегда есть
        "sort_order":     0,                             # сортировки у нас нет; поле обязательно у них
        "source":         TASK_SOURCE,                   # "stream_cad" (<= String(20))
        "is_complex":     False,                         # аналога нет
        "decision_id":    None,                          # их онтология Decision нам не принадлежит
        "is_rfi":         False,
        # RFI-подмножество у обычного задания всегда пустое (контракт §3.3.1)
        "rfi_cause_decision_id": None,
        "rfi_cause_goal_id":     None,
        "rfi_due_date":          None,
        "rfi_state":             None,
        "premise_ids":           [],
        "created_at":     iso_utc(task.created_at),
        "updated_at":     iso_utc(task.updated_at),
        # --- расширения Stream-CAD (их pydantic игнорирует неизвестные поля) ---
        "stream_cad_id":       task.pk,                  # обратные команды (task.deadline_update)
        "stream_cad_status":   task.status,              # сырой 9-статус
        "stream_cad_priority": task.priority,            # 'low'|'normal'|'high'|'critical'
        "assignee_name":       full_name(task.assignee), # ФИО или username, или None
        "overdue_days":        overdue_days,
        "days_until_deadline": days_until,
    }
```

Правила по полям (то же, что таблица §3.3.1 родительского плана, сверено с
их `TaskRead` — `dto/task.py:43-55`):

- `title` — `DesignTask.title` (`CharField(255)`) влезает в их
  `Field(..., min_length=1, max_length=500)`.
- `description` — их тип `str | None`; у нас `TextField` обязательный — всегда
  строка, передаём как есть.
- `assignee_id=None` + `assignee_name` — их executor-справочник нам не
  принадлежит; имя даёт их агенту человекочитаемость.
- `source="stream_cad"` — их `String(20)`; в их UI видно происхождение записи.
- Состав выгрузки: **все** задания проекта, включая `DRAFT` и `CANCELLED`
  (§3.3.1 родительского плана) — их матрица агрегирует сама; фильтр, если
  попросят, — одна строка в `build_tasks_payload` (точка решения 3 уже
  закрыта).

### 2.10. `serialize_rfi_task(rfi, *, today=None, fingerprints=None) -> dict` (пункт 4 задачи)

```python
def serialize_rfi_task(rfi, *, today=None, fingerprints=None) -> dict:
    """RfiTask -> запись rfis.json (контракт S3_SYNC_PLAN.md §3.3.3).

    rfi приходит со select_related('author', 'assignee', 'document_request',
    'document_response', 'document_request__current_version',
    'document_response__current_version').
    fingerprints — {'request': DocumentFingerprint|None,
                    'response': DocumentFingerprint|None} — собирает export.py
    (там разрешён I/O: чтение хеша версии документа); None-ключ -> null в JSON.
    """
    code = rfi.project.code
    fp = fingerprints or {}
    return {
        # --- их RfiRead (валидация их pydantic, §5.2) ---
        "id":             uuid_for_rfi(code, rfi.pk),
        "project_id":     uuid_for_project(code),
        "title":          rfi.subject,        # CharField(255) <= их max_length=1000
        "description":    rfi.reason,         # TextField («суть запроса»)
        "status":         map_rfi_state_to_task_status(rfi.status),  # TaskStatus, верхний регистр
        "assignee_id":    None,               # как у Task: имя — в assignee_name
        "deadline":       None,               # решение RFI_MODULE_PLAN: у RFI один срок - rfi_due_date
        "section_code":   None,               # поправка к примеру §3.3.3: FK RfiTask->Section в R1 нет
        "to_section_code": None,
        "sort_order":     0,
        "source":         TASK_SOURCE,
        "is_complex":     False,
        "decision_id":    None,
        "is_rfi":         True,
        "rfi_cause_decision_id": None,        # XOR-причина живёт в их БД (ck_tasks_rfi_cause);
        "rfi_cause_goal_id":     None,        # рекомендация их импортеру - §3.3.3 род. плана
        "rfi_due_date":   date_iso(rfi.rfi_due_date),   # срок ответа
        "rfi_state":      rfi.status,         # 1:1, НИЖНИЙ регистр: open/sent/answered/registered/closed/escalated
        "premise_ids":    [],                 # Premise - их онтология; их REGISTERED = наш registered
        "created_at":     iso_utc(rfi.created_at),
        "updated_at":     iso_utc(rfi.updated_at),
        # --- расширения Stream-CAD ---
        "stream_cad_number": f"RFI-{rfi.number:02d}",   # поправка: R1 печатает «RFI-07», не «РИ-007» (§6.1)
        "stream_cad_status": rfi.status,
        "assignee_name":     full_name(rfi.assignee),
        "rfi_cause_design_task_id": None,     # FK RfiTask->DesignTask в R1 нет; связь - фаза 2
        "linked_tasks":      [],              # то же: связи «RFI блокирует задания» появятся в фазе 2
        "sent_at":           iso_utc(rfi.sent_at),
        "answered_at":       iso_utc(rfi.answered_at),
        "registered_at":     iso_utc(rfi.registered_at),
        "closed_at":         iso_utc(rfi.closed_at),
        "escalated_at":      iso_utc(rfi.escalated_at),
        "request_document":  fingerprint_to_dict(fp.get("request")),    # §3.5 export.py
        "response_document": fingerprint_to_dict(fp.get("response")),
    }
```

Правила по полям (сверено с их `RfiRead` — `dto/rfi.py:41-52` и моделью
`Task` — `adapters/db/models/task.py`):

1. **`rfi_state` — 1:1, нижний регистр.** Значения `RfiTask.STATUS_CHOICES`
   (`models.py:957`) совпадают с их `RfiState` по строкам (осознанное
   согласование при проектировании R1, решение D2). В JSON уходит строка
   ровно их enum-значения.
2. **`status` — из таблицы §2.6** (верхний регистр); поле обязательно.
3. **`rfi_cause_decision_id` / `rfi_cause_goal_id` = null.** Их CHECK
   `ck_tasks_rfi_cause` (`task.py:46-49`) требует у их RFI причину
   (decision ИЛИ goal) — но это их внутреннее ограничение БД; наш RFI
   проистекает из задания смежнику. Их импортер при вставке строки обязан
   решить: синтетический `Decision`, ослабление CHECK для
   `source='stream_cad'` или отказ от импорта RFI — рекомендация зафиксирована
   в §3.3.3 родительского плана.
4. **`request_document` / `response_document` — метаданные**, не файлы
   (файлы остаются в СОД; зеркалирование — этап F). Структура и сборка — §3.5.
5. **`linked_tasks` = [] и `rfi_cause_design_task_id` = null в MVP**: у
   `RfiTask` @`4c04e2c` нет FK на `DesignTask` (проверено по модели,
   `models.py:950-1023`). Контракт несёт поля с пустыми значениями, чтобы
   фаза 2 (связка «RFI блокирует задания») не меняла схему файла.
6. `stream_cad_number` — дисплей-формат R1: `f"RFI-{number:02d}"` (паддинг —
   минимальная ширина: 7 → «RFI-07», 101 → «RFI-101»). Поправка к примеру
   родительского плана — §6.1.
7. Состав выгрузки: все RFI проекта, включая `open` и `closed`; приоритет
   Координатора «особенно escalated» выполняется сам — escalated лежит в
   общем списке со своим `rfi_state`, их агент фильтрует (§3.3.3 род. плана).

### 2.11. `serialize_section(section) -> dict` — справочник для манифеста

```python
def serialize_section(section) -> dict:
    """Section -> запись metadata.json.sections (§3.3.5)."""
    return {"code": section.code, "name": section.name, "full_code": section.full_code or None}
```

Порядок разделов задаёт `export.py` (`project.sections.order_by('order')` —
поле `Section.order`, `models.py:78`). Их импортер сверяет
`section_code`/`to_section_code` задач со справочником и получает имена
разделов для матрицы.

---

## 3. Модуль `storage/domain/sync/export.py` — сборка payload'ов и выгрузка

### 3.1. Ответственность и границы

`export.py` — единственное место модуля, где разрешены ORM-запросы, чтение
файлов и вызовы порта S3. Здесь же:

- сборка envelope'ов `tasks.json` / `rfis.json` (§3.3, §3.4);
- fingerprint'ы документов RFI (§3.5);
- канон байтов поколения и sha256 (§3.2);
- фасад `export_project_to_s3(project) -> int` (§3.6).

За кадром остаются: порт/адаптер (Шаг 2), inbox (Шаг 4), Celery-обёртки
(Шаг 5) — их подписи не меняются против родительского плана.

### 3.2. Канон байтов поколения (детерминированная сериализация)

```python
import json

def _json_bytes(payload: dict) -> bytes:
    """Единственный способ превратить payload в байты. UTF-8, без BOM,
    компактные разделители, порядок ключей - как в сериализаторе.
    МЕНЯТЬ ПАРАМЕТРЫ ЗАПРЕЩЕНО: любое изменение меняет sha256 всех файлов
    без изменения данных и ломает сверку у импортера."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()   # hex, нижний регистр
```

Решения: (а) `ensure_ascii=False` — кириллица остаётся читаемой в бакете и в
`mc cat`; (б) `sort_keys=False` — порядок ключей управляется сериализаторами
(читается как примеры §3.3), детерминизм обеспечивает фиксированный порядок
литералов dict (Python 3.7+); (в) один `generated_at` на все три файла
поколения (§3.6, шаг 2) — по нему импортер видит целостность набора.

### 3.3. `build_tasks_payload(project, generation, generated_at) -> dict`

```python
def build_tasks_payload(project, generation: int, generated_at: str) -> dict:
    tasks = (project.design_tasks
             .select_related("source_section", "target_section", "author", "assignee")
             .order_by("pk"))                     # стабильный порядок записей (diff-friendly)
    items = [serialize_design_task(t) for t in tasks]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at":   generated_at,
        "generation":     generation,
        "source_system":  SOURCE_SYSTEM,
        "project_code":   project.code,
        "items":          items,
    }
```

Замечания: (а) `order_by("pk")`, не `-created_at` (модельное ordering) —
порядок записей в файле не «пляшет» при правках старых строк; (б) `DRAFT` и
`CANCELLED` включаются (§2.9); (в) сигнатура — уточнение к Шагу 3 родительского
плана: `generation`/`generated_at` передаются явно, чтобы функция была
тестируемой без S3 и БД-состояния.

### 3.4. `build_rfis_payload(project, generation, generated_at) -> dict`

```python
def build_rfis_payload(project, generation: int, generated_at: str) -> dict:
    rfis = (project.rfi_tasks
            .select_related("author", "assignee",
                            "document_request", "document_response",
                            "document_request__current_version",
                            "document_response__current_version")
            .order_by("number"))                  # стабильный порядок по номеру RFI
    items = []
    for r in rfis:
        items.append(serialize_rfi_task(r, fingerprints={
            "request":  build_document_fingerprint(r.document_request),
            "response": build_document_fingerprint(r.document_response),
        }))
    return { ... такой же envelope, items ... }
```

До подключения Этапа C функция возвращает валидный пустой payload
(`items: []`) — контракт пустого `rfis.json` уже согласован (Этап B,
§7 родительского плана).

### 3.5. Fingerprint документов RFI (`request_document` / `response_document`)

Метаданные документа вместо файла (§3.3.3 родительского плана). Источник —
`Document` + текущая `DocumentVersion` (`models.py:347-379`), у версии уже
хранятся `file_size` и `file_hash` (sha256, считается при загрузке —
`models.py:351-352`):

```python
def build_document_fingerprint(document) -> dict | None:
    """Метаданные документа RFI. Файл в бакет НЕ выгружается (MVP, этап F)."""
    if document is None:
        return None
    version = document.current_version
    sha256 = size = updated = None
    if version is not None:
        size = version.file_size
        sha256 = version.file_hash or None
        if sha256 is None and version.file:
            sha256 = _sha256_of_file(version.file.path)   # streaming-хеш, защита от старых записей
        updated = iso_utc(version.uploaded_at)
    return {"code": document.code, "name": document.name,
            "size_bytes": size, "sha256": sha256, "updated_at": updated}

def fingerprint_to_dict(fp) -> dict | None:
    return fp   # fingerprint уже в формате JSON; None -> null
```

Правило: пустой `file_hash` у старых версий добирается потоковым чтением файла
один раз на выгрузку (RFI-документы — единицы PDF, дешёво); в БД при этом
**ничего не пишем** (export только читает).

### 3.6. `export_project_to_s3(project) -> int` — фасад (алгоритм по шагам)

Возвращает `generation` выгрузки; исключения не глотает (Celery-ретраи смотрят
на `ClientError`/`BotoCoreError` напрямую, §3.4.2 род. плана).

```python
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

LOCK_TTL = 300   # сек; больше максимальной длительности выгрузки на порядок

def export_project_to_s3(project) -> int:
    # 0. Выключатель (дублирует проверку порта - fail-fast без S3-клиента)
    if not settings.S3_SYNC_ENABLED:
        raise SyncDisabledError("S3_SYNC_ENABLED=false")

    prefix = f"projects/{project_slug(project.code)}/outbox"
    gateway = get_s3_gateway()                        # storage/ports/s3_gateway.py (DI как в проекте)
    gateway.ensure_bucket()                           # §1.3 (MinIO: бакет не создаётся сам)

    # 1. Single-flight: beat (раз в 15 мин) + ручной запуск не должны пересекаться.
    #    Redis уже в стеке (Celery broker) - используем django cache.
    lock_key = f"s3sync:{project.pk}"
    if not cache.add(lock_key, "1", timeout=LOCK_TTL):
        log_warn(project, "export skipped: already running")
        return SyncState.objects.get(project=project).generation
    try:
        # 2. Сборка (ORM читает; БД-транзакция НЕ открыта - внешний I/O вне транзакций).
        #    Лок шага 1 гарантирует single-flight, поэтому generation читаем без FOR UPDATE.
        sync_state = SyncState.objects.get(project=project)
        new_generation = sync_state.generation + 1   # резервируется заранее: при падении
                                                     # после PUT БД не менялась, повтор соберёт то же N+1
        generated_at = iso_utc(timezone.now())
        tasks_payload = build_tasks_payload(project, new_generation, generated_at)
        rfis_payload  = build_rfis_payload(project, new_generation, generated_at)
        tasks_bytes   = _json_bytes(tasks_payload)
        rfis_bytes    = _json_bytes(rfis_payload)
        metadata_payload = build_metadata(                # §4.2: sha256 от ТЕХ ЖЕ байтов
            project, new_generation, generated_at, tasks_bytes, rfis_bytes,
            counts={"tasks": len(tasks_payload["items"]),
                    "rfis": len(rfis_payload["items"]),
                    "rfis_escalated": project.rfi_tasks.filter(status="escalated").count()},
            sync_state=sync_state,
        )
        metadata_bytes = _json_bytes(metadata_payload)

        # 3. Пропуск без изменений (точка решения 9)
        current = gateway.get_json_object(f"{prefix}/metadata.json") \
                  if gateway.object_exists(f"{prefix}/metadata.json") else None
        if current and not _manifest_changed(current, tasks_bytes, rfis_bytes, sync_state):
            # файлы в бакете уже содержат это состояние; generation не растёт
            sync_state.dirty = False
            sync_state.last_success_at = timezone.now()
            sync_state.consecutive_failures = 0
            sync_state.last_error = ""
            sync_state.save(update_fields=["dirty", "last_success_at",
                                           "consecutive_failures", "last_error"])
            SyncLog.objects.create(project=project, direction="out", level="info",
                                   generation=sync_state.generation,
                                   message="no changes, skipped")
            return sync_state.generation

        # 4. PUT в порядке §3.1: tasks.json -> rfis.json -> metadata.json (коммит, §4.3)
        gateway.put_json_object(f"{prefix}/tasks.json",    tasks_bytes)
        gateway.put_json_object(f"{prefix}/rfis.json",     rfis_bytes)
        gateway.put_json_object(f"{prefix}/metadata.json", metadata_bytes)

        # 5. Фиксация (короткая транзакция; §3.4.2 род. плана)
        with transaction.atomic():
            sync_state.generation = F("generation") + 1
            sync_state.dirty = False
            sync_state.last_success_at = timezone.now()
            sync_state.consecutive_failures = 0
            sync_state.last_error = ""
            sync_state.save(update_fields=["generation", "dirty", "last_success_at",
                                           "consecutive_failures", "last_error"])
            SyncLog.objects.create(project=project, direction="out", level="info",
                                   generation=new_generation,
                                   message="snapshot exported",
                                   detail={"tasks": len(tasks_payload["items"]),
                                           "rfis": len(rfis_payload["items"]),
                                           "bytes": [len(tasks_bytes), len(rfis_bytes),
                                                     len(metadata_bytes)],
                                           "changed": True})
            sync_state.refresh_from_db()
        return sync_state.generation
    finally:
        cache.delete(lock_key)
```

Пояснения к шагам:

- **2.** `new_generation = (SyncState.generation текущее) + 1` резервируется в
  начале; если выгрузка падает после PUT, БД не менялась и повтор соберёт то
  же `N+1` (детерминированно); фиксация — только после успешных трёх PUT
  (§3.4.2 род. плана: инкремент в транзакции). `log_info`/`log_warn` в
  псевдокоде — условные обёртки над `SyncLog.objects.create(...)` для
  читаемости.
- **3.** `_manifest_changed` сравнивает sha256 обоих файлов и
  `sync_state.last_ack_generation`; если совпало — PUT не делаем вовсе. Дешёвый
  GET заменяет 3 PUT и не порождает «пустых» поколений у партнёра.
- **Ошибки** (любое исключение в 2–4): в `storage.tasks.sync_project_to_s3`
  обработчик пишет `SyncLog(direction=out, level=error, last_error=str(e))`,
  `consecutive_failures += 1`; при `>= 3` — уведомление ГИП через
  Notification (§3.4.2.4 род. плана). Лок снимается в `finally`.
- Параллельность между **разными проектами** — полная (лок per-project);
  очередь Celery по воркерам — без ограничений.

---

## 4. Манифест `metadata.json` с sha256 (пункт 5 задачи)

### 4.1. Структура (по §3.3.5 родительского плана, поля закреплены)

```json
{
  "schema_version": "1.0",
  "source_system": "stream-cad",
  "generation": 42,
  "generated_at": "2026-11-16T06:30:00Z",
  "project": {
    "code": "123-45.67",
    "name": "Жилой комплекс на Проспекте",
    "customer": "ООО «Заказчик»",
    "gip_username": "gip.ivanov",
    "status": "active"
  },
  "sections": [
    {"code": "АР", "name": "Архитектура", "full_code": "АР-01"},
    {"code": "ОВ", "name": "Отопление и вентиляция", "full_code": "ОВ-01"}
  ],
  "counts": {"tasks": 118, "rfis": 7, "rfis_escalated": 2},
  "files": {
    "tasks.json": {"sha256": "<64 hex-символа>", "bytes": 148000},
    "rfis.json":  {"sha256": "<64 hex-символа>", "bytes": 9000}
  },
  "sync_state": {
    "last_ack_generation": 41,
    "last_ack_at": "2026-11-16T05:05:00Z"
  }
}
```

Правила:

1. Ключи в `files` — **имена файлов** внутри `outbox/` (полный путь строит
   импортер: он и так знает префикс `projects/{slug}/outbox/`). Юридическая
   связка — `project.code` (оригинальный шифр), слаг — техническая деталь пути.
2. `metadata.json` **не хэширует сам себя** (круговая ссылка невозможна);
   целостность манифеста = валидный JSON + валидные поля.
3. `counts` — быстрые валидаторы импортера (число прочитанных строк против
   заявленного); `rfis_escalated` — прямой счёт `RfiTask.filter(status='escalated')`.
4. `sections` — справочник для их матрицы (`order_by('order')`).
5. `sync_state.last_ack_generation` / `last_ack_at` — последняя квитанция от
   ГИП-Ассистента (`ack.import`, §3.5 род. плана): обе стороны видят, где стоит
   конвейер. Поля должны появиться в `SyncState` (поправка 3, §6.3).
6. `generated_at` один и тот же во всех трёх файлах поколения.

### 4.2. Как собирается (псевдокод)

```python
def build_metadata(project, generation: int, generated_at: str,
                   tasks_bytes: bytes, rfis_bytes: bytes,
                   counts: dict, sync_state) -> dict:
    """Сборка манифеста. sha256 считается по ТЕМ ЖЕ байтам, которые будут
    выгружены (после сериализации, до PUT) - поэтому сверка у читателя
    всегда сходится."""
    return {
        "schema_version": SCHEMA_VERSION,
        "source_system":  SOURCE_SYSTEM,
        "generation":     generation,
        "generated_at":   generated_at,
        "project": {
            "code":        project.code,
            "name":        project.name,
            "customer":    project.customer or None,
            "gip_username": project.gip.username if project.gip else None,
            "status":      project.status,               # 'active' | 'archived'
        },
        "sections": [serialize_section(s) for s in project.sections.order_by("order")],
        "counts": counts,                                # {'tasks': N, 'rfis': M, 'rfis_escalated': K}
        "files": {
            "tasks.json": {"sha256": sha256_hex(tasks_bytes), "bytes": len(tasks_bytes)},
            "rfis.json":  {"sha256": sha256_hex(rfis_bytes),  "bytes": len(rfis_bytes)},
        },
        "sync_state": {
            "last_ack_generation": sync_state.last_ack_generation,
            "last_ack_at": iso_utc(sync_state.last_ack_at),
        },
    }
```

Сигнатура — уточнение к Шагу 3 (`build_metadata(project, generation)`): байты
и счётчики передаются явно, чтобы манифест строился из **тех же** payload'ов,
что пойдут в PUT, и тестировался без БД.

### 4.3. Порядок записи и роль «точки коммита»

```
PUT outbox/tasks.json       (поколение N+1)
PUT outbox/rfis.json        (поколение N+1)
PUT outbox/metadata.json    (поколение N+1, sha256 обоих файлов)  <- КОММИТ
```

- Читатель (импортер ГИП-Ассистента) работает так: прочитать `metadata.json`
  → сравнить `generation` с последним импортированным → скачать
  `tasks.json`/`rfis.json` → сверить sha256 с манифестом → импортировать.
- Гонка «прочитал tasks.json нового поколения, а rfis.json ещё старого»
  закрыта: несовпадение sha256 вынуждает перечитать (правило 3 §3.1 род. плана).
- Падение между PUT-ами не коммитит поколение: старый манифест остаётся
  валидным для старых файлов; следующая выгрузка перезапишет всё три.
- Манифест пишется **последним** — это единственное обязательное правило
  порядка; сами PUT независимы и не требуют транзакций.

### 4.4. Почему это работает на MinIO без поправок

| Свойство MinIO | Роль в схеме |
|---|---|
| Атомарный single-part PUT (объект заменяется целиком) | Читатель никогда не видит «половину» JSON |
| Строгая read-after-write консистентность | Сразу после коммит-PUT манифест отдаёт новое поколение — «отставания» реплик нет |
| Версионирование бакета (`mc version enable`) | Предыдущие поколения сохраняются сервером (защита от случайной перезаписи, правило 5 §3.1) |
| Path-style + s3v4 | Работает и на localhost:9000, и на Cloud.ru — порядок записи не меняется при переезде |

Дополнительно: маленькие файлы (единицы КБ…МБ) всегда идут single-part,
никаких multipart-тонкостей; мягкий warn при > 10 МБ — контроль «раздувания».

---

## 5. Сверка контрактов (проверено по исходникам @4c04e2c)

### 5.1. `tasks.json`-item ↔ их `TaskRead` (`dto/task.py:11-55`)

| Поле их `TaskRead` | Тип у них | Что отправляем | Валидность |
|---|---|---|---|
| `title` | `str`, 1..500 | `DesignTask.title` (255) | ок |
| `description` | `str \| None` | `DesignTask.description` (обязательный TextField) | ок |
| `deadline` | `date \| None` | `due_date` -> `"YYYY-MM-DD"` | ок |
| `section_code` | `str \| None`, ≤50 | `source_section.code` (≤20) | ок |
| `to_section_code` | `str \| None`, ≤50 | `target_section.code` (≤20) | ок |
| `assignee_id` | `UUID \| None` | `null` (+ имя в `assignee_name`) | ок |
| `is_complex` | `bool` | `False` | ок |
| `id` | `UUID` | uuid5 `design-task:` | ок |
| `project_id` | `UUID` | uuid5 `project:` | ок |
| `status` | `TaskStatus` (StrEnum, верхний регистр) | таблица §2.5 | ок — строка = значение enum |
| `sort_order` | `int` | `0` | ок |
| `source` | `str` (их `String(20)`) | `"stream_cad"` | ок |
| `created_at` / `updated_at` | `datetime` (timezone=True) | `iso_utc(...)` с `Z` | ок |

Дополнительные поля записи (`rfi_*`, `premise_ids`, `stream_cad_*`,
`assignee_name`, `overdue_days`, `days_until_deadline`) их pydantic
**игнорирует** (у их моделей `model_config = ConfigDict(from_attributes=True)`,
`extra` по умолчанию — `'ignore'`) — контракт родительского плана сохранён.
Значение `source="stream_cad"` (с подчёркиванием) согласовано с их
`Task.source` (`String(20)`, дефолт `"manual"`).

### 5.2. `rfis.json`-item ↔ их `RfiRead` (`dto/rfi.py:41-52`)

| Поле их `RfiRead` | Тип у них | Что отправляем | Валидность |
|---|---|---|---|
| `title` | `str`, 1..1000 | `RfiTask.subject` (255) | ок |
| `description` | `str \| None` | `RfiTask.reason` (обязательный TextField) | ок |
| `deadline` | `date \| None` | `null` (решение RFI_MODULE_PLAN: один срок — `rfi_due_date`) | ок |
| `rfi_due_date` | `date \| None` | `RfiTask.rfi_due_date` | ок |
| `rfi_state` | `RfiState \| None` (нижний регистр) | `rfi.status` 1:1 | ок |
| `status` | `TaskStatus` | таблица §2.6 | ок |
| `rfi_cause_decision_id` / `rfi_cause_goal_id` | `UUID \| None` | `null` (XOR — их БД, `ck_tasks_rfi_cause` `task.py:46`) | ок в JSON; обход CHECK — на их импортере (рекомендация §3.3.3 род. плана) |
| `premise_ids` | `list[UUID]` | `[]` | ок |
| `assignee_id`, `is_complex`, `sort_order`, `source`, `id`, `project_id`, `created_at`, `updated_at` | как у Task | как в §5.1 | ок |

### 5.3. Enum-значения (зафиксировано)

| Enum | Сторона | Значения |
|---|---|---|
| `TaskStatus` | ГА (`enums.py:15`) | `NOT_STARTED, IN_PROGRESS, REVIEW, REWORK, DONE, CANCELLED` (верхний регистр) |
| `RfiState` | ГА (`enums.py:156`) | `open, sent, answered, registered, closed, escalated` (нижний регистр) |
| `DesignTask.Status` | СОД (`models.py:707`) | `DRAFT, ISSUED, IN_PROGRESS, ON_HOLD, COMPLETED, REVIEW, ACCEPTED, RETURNED, CANCELLED` (9) |
| `RfiTask.status` | СОД (`models.py:957`) | `open, sent, answered, registered, closed, escalated` — 1:1 с их `RfiState` |

---

## 6. Поправки к S3_SYNC_PLAN.md (найдены при сверке с кодом @4c04e2c)

1. **`stream_cad_number`: «RFI-07», не «РИ-007».** Пример §3.3.3 родительского
   плана писался до RFI-модуля; R1 использует дисплей `RFI-{number}` с
   паддингом 2 (RFI_MODULE_PLAN.md, `models.py:970`, `__str__` в
   `models.py:1023`). Сериализатор отдаёт `f"RFI-{rfi.number:02d}"` (§2.10.6).
   Рекомендуется поправить пример в §3.3.3 при следующей редакции плана.
2. **`section_code` у RFI — `null`.** В примере §3.3.3 стояло `"ОВ"`, но у
   `RfiTask` нет FK на `Section` (проверено: `models.py:950-1023`). Их
   `RfiBase.section_code` nullable — `null` валиден. Правка примера — там же.
3. **`last_ack_at` в `SyncState`.** Манифест §3.3.5 содержит
   `sync_state.last_ack_at`, но перечень полей Шага 1 его не называет. Рекомендация:
   добавить `last_ack_at = DateTimeField(null=True)` в `SyncState` (миграция
   0033 ещё не создана — изменения бесплатны); заполняется обработчиком
   `ack.import` (Шаг 4).
4. **Маппинг `status` для RFI доопределён.** §3.3.3 задавал `status` только
   для open/sent/escalated/closed; для `answered`/`registered` фиксируется
   `IN_PROGRESS` (§2.6 этого документа; подтверждение у команды ГА — точка
   решения 11).
5. Пример §3.3.1 (`"stream_cad_priority": "high"`) — согласован с
   `DesignTask.Priority` (`models.py:718`), правки не требует.

---

## 7. Крайние случаи (сводно)

| # | Случай | Поведение |
|---|---|---|
| 1 | Проект без заданий и/или RFI | `items: []`, `counts: 0` — валидные файлы; `metadata.json` обязателен всегда |
| 2 | Раздел удалён | Невозможно: `source_section`/`target_section` — `PROTECT` (`models.py:730-743`) |
| 3 | RFI в `open`, запрос не сформирован | `request_document: null`, `sent_at: null` — валидно |
| 4 | `DocumentVersion.file_hash` пуст (старые записи) | Потоковый sha256 файла на лету (§3.5); в БД ничего не пишем |
| 5 | `current_version` у документа отсутствует | Fingerprint с `null`-ами + `SyncLog(warn)` |
| 6 | Смена `Project.code` при включённом sync | Запрещена вне модуля (§6.5 род. плана); сериализатор не проверяет |
| 7 | `rfi.number > 99` | `f"{n:02d}"` — паддинг-минимум: «RFI-101»; обрезки нет |
| 8 | Часовые пояса | `USE_TZ=True` (settings.py:323) — значения aware; файлы — UTC/`Z`; `today` для метрик — `timezone.localdate()` (Moscow) |
| 9 | Неизвестный статус в БД | `ValueError` (fail-fast), выгрузка проекта падает целиком, `SyncLog(error)` — статус-листы закрытые матрицами переходов, случай должен быть диагносцируем |
| 10 | Конкурентные запуски (beat + ручной) | Redis-лок per-project, второй выходит сразу (§3.6.1) |
| 11 | `S3_SYNC_ENABLED=false` | `SyncDisabledError` в фасаде до любых I/O; beat-задачи выходят сами |

---

## 8. Тест-план (дополнение к Шагу 9 родительского плана)

1. **uuid** (unit): (а) `uuid_for_project("X")` вычислен от литерала
   `b1ae1443-...` — тест-защита от «пересчёта» namespace; (б) те же pk у
   разных kind дают разные uuid (`design-task:P:1` ≠ `rfi:P:1`); (в) код
   участвует в seed (`P-1` ≠ `P-2`); (г) формат — str-UUID с дефисами.
2. **Маппинги** (unit, параметрические): все 9 строк §2.5; все 6 строк §2.6;
   неизвестные значения → `ValueError`.
3. **serialize_design_task** (unit): полный объект; без `assignee`;
   `due_date=None`; метрики с переданным `today` (DONE → `0/None`; просрочка
   на 3 дня → `3/-3`); даты/время в формате §2.7.
4. **serialize_rfi_task** (unit): все 6 статусов (`rfi_state` в JSON —
   нижний регистр, `status` — верхний); fingerprint null / request-only;
   `stream_cad_number` паддинг (7 → «RFI-07»).
5. **build_\*** (unit): envelope-поля; `counts`; детерминизм — двойная
   сериализация даёт одинаковый sha256; `order_by("pk")`/`order_by("number")`.
6. **export** (интеграция на moto): (а) round-trip «как их импортер»:
   GET `metadata.json` → сверка sha256 обоих файлов; (б) в бакете ровно три
   объекта outbox; (в) повторный вызов без изменений — generation не вырос
   (skip); (г) изменение задания — generation+1 и новый sha256;
   (д) `S3_SYNC_ENABLED=false` → исключение; (е) лок: второй вызов выходит
   сразу. moto поддерживает path-style и версионирование — тесты одинаковы
   для moto и MinIO.
7. **MinIO-интеграция на VDS** (вне CI, вручную): `manage.py sync_s3 --project
   <code>` → команды `mc` из §1.5 (ls/cat/sha256sum) → сверка манифеста.
8. Конвенции проекта: `makemigrations --check`, отсутствие `RuntimeWarning`.

---

## 9. Порядок реализации для Кодера

| Файл | Этап | Содержание | ~строк |
|---|---|---|---|
| `storage/domain/sync/__init__.py` | B | пустой | 1 |
| `storage/domain/sync/mapping.py` | B | константы (§2.2), `uuid_for_*` (§2.3), `project_slug` (§2.4), `map_task_status` (§2.5), `iso_utc`/`date_iso`/`full_name` (§2.7), `compute_deadline_metrics` (§2.8), `serialize_design_task` (§2.9), `serialize_section` (§2.11) | ~150 |
| `storage/domain/sync/mapping.py` | C | `map_rfi_state_to_task_status` (§2.6), `serialize_rfi_task` (§2.10) | ~70 |
| `storage/domain/sync/export.py` | B | `_json_bytes`/`sha256_hex` (§3.2), `build_tasks_payload` (§3.3), `build_metadata` (§4.2), `_manifest_changed`, `export_project_to_s3` (§3.6) | ~160 |
| `storage/domain/sync/export.py` | C | `build_rfis_payload` (§3.4), `build_document_fingerprint` (§3.5) | ~40 |
| `storage/tests/test_s3_sync.py` | B/C | кейсы §8 | ~350 |

Зависимости и порядок:

1. Шаг 1 род. плана (модели `SyncState`/`SyncLog`; **+ `last_ack_at`**, §6.3)
   и Шаг 2 (порт `s3_gateway` с `ensure_bucket`, адаптер `s3_boto3` с
   path-style/s3v4 — §1.3), `boto3`/`moto` в requirements.
2. Этап B: `tasks.json` + `metadata.json` + Celery/beat + dirty-сигналы
   (`rfis.json` — валидный пустой).
3. Этап C: `serialize_rfi_task` — код RFI-модуля уже в базе (`4c04e2c`,
   42 теста), блокировки нет.
4. Соответствие раскадке §7 род. плана сохранено: этот документ закрывает
   Этапы B и C (сериализация), не трогая A (инфраструктура) и D (inbox).

Оценка объёма совпадает с §8 род. плана: `mapping.py + export.py ≈ 350-420
строк` вместе с тестами модуля.

---

## 10. Точки решения для Координатора (новые, 9–13)

| # | Вопрос | Рекомендация |
|---|---|---|
| 9 | **Пропуск без изменений** (GET текущего `metadata.json` перед PUT; при совпадении sha256 и `last_ack_generation` — PUT не делаем, generation не растёт) | Включить: меньше «пустых» поколений, их импортер не перечитывает одинаковое; минус — один GET на выгрузку |
| 10 | Метрики дедлайна для `DONE`/`CANCELLED`: `overdue_days=0`, `days_until_deadline=null` | Да: «просрочена» — свойство активной задачи; их `CrossTaskTaskRead` тоже описывает просрочку активных |
| 11 | `answered`/`registered` → `IN_PROGRESS` (§2.6) | Да; подтвердить у команды ГА, что их агент фильтрует по `rfi_state`, а `status` — агрегат |
| 12 | MinIO-MVP: версионирование бакета включить (`mc version enable`), SSE не включать | Да (SSE — на Cloud.ru, §3.7.2.4 род. плана) |
| 13 | Redis-лок выгрузки (`django.core.cache`, TTL 300 с) | Да: beat + ручной запуск не должны пересекаться; Redis уже в стеке |

---

**Конец плана S3_MAPPING_EXPORT_PLAN.md (v1.0, 16.11.2026).**
