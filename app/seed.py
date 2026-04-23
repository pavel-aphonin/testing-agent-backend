"""Seed script: on first startup, create the initial admin and the two
pre-installed LLM models (Gemma 4 E4B + Qwen 3.5 35B-A3B) if they're missing.

Both models were released by Google DeepMind / Alibaba on April 7, 2026
and are downloaded into LLM_MODELS_DIR by ``make download-models``. The
filenames here MUST match what that script writes to disk, otherwise
llama-swap won't be able to spawn the corresponding llama-server process.
"""

from fastapi_users.exceptions import UserAlreadyExists
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy import select

from app.auth.users import UserManager
from app.config import settings
from app.db import async_session_maker
from app.llm_swap import regenerate_swap_config
from app.models.llm_model import LLMModel
from app.models.role import Role
from app.models.user import User
from app.schemas.user import UserCreate


INITIAL_MODELS = [
    {
        "name": "gemma-4-e4b",
        "family": "gemma-4",
        "description": (
            "Gemma 4 E4B (4.5B effective params, 8B with embeddings). "
            "Released April 7, 2026 by Google DeepMind. 128K context, "
            "vision input, tool use, audio, 140+ languages. The fast "
            "classifier in Hybrid mode — sets PUCT priors over UI elements."
        ),
        "gguf_path": "/var/lib/llm-models/gemma-4-E4B-it-Q4_K_M.gguf",
        "mmproj_path": "/var/lib/llm-models/gemma-4-E4B-it-mmproj-F16.gguf",
        "size_bytes": 5_100_000_000,
        "context_length": 131_072,
        "quantization": "Q4_K_M",
        "supports_vision": True,
        "supports_tool_use": True,
        "default_temperature": 0.4,
        "default_top_p": 0.9,
    },
    {
        "name": "qwen3.5-35b-a3b",
        "family": "qwen-3.5",
        "description": (
            "Qwen 3.5 35B-A3B (MoE: 35B total, 3B active per token). "
            "Released April 7, 2026 by Alibaba. Inference speed close to a "
            "dense 3B model despite the 35B parameter count. 262K context, "
            "vision, tool use, reasoning. The smart actor in AI mode and "
            "the analyzer for Phase 2 graph review."
        ),
        "gguf_path": "/var/lib/llm-models/Qwen3.5-35B-A3B-UD-Q4_K_XL.gguf",
        "mmproj_path": "/var/lib/llm-models/Qwen3.5-35B-A3B-mmproj-F16.gguf",
        "size_bytes": 22_200_000_000,
        "context_length": 262_144,
        "quantization": "UD-Q4_K_XL",
        "supports_vision": True,
        "supports_tool_use": True,
        "default_temperature": 0.6,
        "default_top_p": 0.9,
    },
]


async def seed_initial_admin() -> None:
    """Create the first admin if the users table is empty.

    After creating the user, link them to the system 'admin' role.
    """
    async with async_session_maker() as session:
        result = await session.execute(select(User).limit(1))
        if result.scalar_one_or_none() is not None:
            print("[seed] Users already exist, skipping admin seed.")
            return

    async with async_session_maker() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        user_manager = UserManager(user_db)

        try:
            user = await user_manager.create(
                UserCreate(
                    email=settings.initial_admin_email,
                    password=settings.initial_admin_password,
                    is_superuser=True,
                    is_verified=True,
                    role="admin",
                    must_change_password=False,
                )
            )
            # Link to the system admin role
            admin_role = await session.execute(
                select(Role).where(Role.code == "admin")
            )
            role_obj = admin_role.scalar_one_or_none()
            if role_obj:
                result = await session.execute(
                    select(User).where(User.id == user.id)
                )
                user_obj = result.scalar_one()
                user_obj.role_id = role_obj.id
                await session.commit()

            print(f"[seed] Created initial admin: {user.email}")
        except UserAlreadyExists:
            print(f"[seed] Admin {settings.initial_admin_email} already exists.")


async def seed_demo_apps() -> None:
    """Pack and register every bundle in app/seed_apps/ as an approved
    public app. Idempotent — skips apps that already exist."""
    import io
    import json as _json
    import zipfile
    from datetime import datetime, timezone
    from pathlib import Path

    from app.models.app_package import (
        AppApprovalStatus,
        AppPackage,
        AppPackageVersion,
    )
    from app.services.app_bundle import extract_and_validate

    apps_root = Path(__file__).parent / "seed_apps"
    if not apps_root.exists():
        return

    async with async_session_maker() as session:
        for seed_dir in sorted(apps_root.iterdir()):
            if not seed_dir.is_dir() or not (seed_dir / "manifest.json").exists():
                continue

            # Peek at the manifest to check uniqueness before doing work.
            try:
                manifest_raw = _json.loads((seed_dir / "manifest.json").read_text())
                code = manifest_raw.get("code")
            except Exception:  # noqa: BLE001
                continue
            if not code:
                continue

            q = await session.execute(select(AppPackage).where(AppPackage.code == code))
            existing_pkg = q.scalar_one_or_none()

            # Skip only if a version row with the manifest's version
            # already exists — allows bumping the version in seed_apps
            # to publish an upgrade.
            manifest_version = manifest_raw.get("version")
            if existing_pkg and manifest_version:
                from app.models.app_package import AppPackageVersion as _V
                vq = await session.execute(
                    select(_V).where(
                        _V.app_package_id == existing_pkg.id,
                        _V.version == manifest_version,
                    )
                )
                if vq.scalar_one_or_none() is not None:
                    continue  # this exact version already seeded

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in seed_dir.rglob("*"):
                    if path.is_file():
                        zf.write(path, path.relative_to(seed_dir).as_posix())
            zip_bytes = buf.getvalue()

            try:
                extracted = extract_and_validate(zip_bytes)
            except Exception as exc:  # noqa: BLE001
                print(f"[seed] {code} extract failed: {exc}")
                continue

            if existing_pkg:
                # Upgrade path: same code, new version. Update metadata.
                existing_pkg.name = extracted.manifest.name
                existing_pkg.description = extracted.manifest.description
                existing_pkg.category = extracted.manifest.category
                existing_pkg.author = extracted.manifest.author
                if extracted.logo_relpath:
                    existing_pkg.logo_path = extracted.logo_relpath
                if extracted.cover_relpath:
                    existing_pkg.cover_path = extracted.cover_relpath
                pkg = existing_pkg
            else:
                pkg = AppPackage(
                    code=extracted.manifest.code,
                    name=extracted.manifest.name,
                    description=extracted.manifest.description,
                    category=extracted.manifest.category,
                    author=extracted.manifest.author,
                    logo_path=extracted.logo_relpath,
                    cover_path=extracted.cover_relpath,
                    is_public=True,
                    approval_status=AppApprovalStatus.APPROVED.value,
                    approved_at=datetime.now(timezone.utc),
                )
                session.add(pkg)
            await session.flush()
            version = AppPackageVersion(
                app_package_id=pkg.id,
                version=extracted.manifest.version,
                manifest=extracted.manifest.model_dump(),
                bundle_path=extracted.bundle_relpath,
                size_bytes=extracted.size_bytes,
                changelog=extracted.manifest.changelog,
            )
            session.add(version)
            await session.commit()
            print(f"[seed] Registered app: {pkg.name} v{version.version}")


async def seed_help_articles() -> None:
    """Seed a small initial set of help articles so the portal isn't empty.

    Idempotent — skip an article whose slug already exists. We also do
    NOT update existing rows on re-seed (content can be edited in the DB
    after launch and we don't want to clobber those edits). To push a
    rewrite, bump the slug.
    """
    from app.models.help import HelpArticle, HelpArticleSection as S

    seeds = [
        {
            "slug": "welcome",
            "section": S.GETTING_STARTED.value,
            "sort_order": 0,
            "title": "Знакомство с Марковом",
            "excerpt": "Что умеет платформа и с чего начать первый запуск.",
            "body_md": (
                "**Марков** — платформа автоматического исследования мобильных приложений.\n\n"
                "С чего начать:\n\n"
                "1. Создайте рабочее пространство или присоединитесь к существующему.\n"
                "2. Перейдите в раздел **Запуски** и нажмите **Новый запуск**.\n"
                "3. Загрузите сборку приложения (`.app.zip`, `.ipa`, `.apk`).\n"
                "4. Выберите устройство и режим работы агента.\n"
                "5. Нажмите **Запустить** — агент сам пройдётся по экранам.\n\n"
                "Остальные статьи в этом разделе помогут освоить инструмент глубже."
            ),
        },
        {
            "slug": "roles",
            "section": S.GETTING_STARTED.value,
            "sort_order": 1,
            "title": "Роли и права доступа",
            "excerpt": "Администратор, тестировщик, наблюдатель — кому что доступно.",
            "body_md": (
                "В системе три базовые роли, плюс можно создать свои.\n\n"
                "- **Администратор** — полный доступ, в том числе к пользователям, моделям, устройствам.\n"
                "- **Тестировщик** — создание и запуск исследований, сценарии, тестовые данные.\n"
                "- **Наблюдатель** — только чтение результатов.\n\n"
                "Управление ролями — в разделе **Пользователи → Роли** (только для администратора)."
            ),
        },
        {
            "slug": "new-run",
            "section": S.RUNS.value,
            "sort_order": 0,
            "title": "Как запустить исследование",
            "excerpt": "Подробный разбор окна создания запуска.",
            "body_md": (
                "Нажмите **Новый запуск** в разделе **Запуски**.\n\n"
                "### Что заполнить\n"
                "1. **Сборка** — `.app.zip` / `.ipa` для iOS или `.apk` для Android.\n"
                "2. **Устройство** — из списка, который поддерживает администратор.\n"
                "3. **Режим агента** — AI / MC / Hybrid (Hybrid — по умолчанию).\n"
                "4. **Максимум шагов** — сколько экранов агент обойдёт прежде, чем остановиться.\n\n"
                "### Дополнительно\n"
                "- **Сценарии** — можно включить и выбрать те, что нужно пройти.\n"
                "- **Property-Based Testing** — систематическая проверка валидаций форм.\n\n"
                "Прогресс запуска виден в реальном времени на странице «Прогресс»."
            ),
        },
        {
            "slug": "run-modes",
            "section": S.RUNS.value,
            "sort_order": 1,
            "title": "Режимы агента: AI, MC, Hybrid",
            "excerpt": "Чем отличаются и когда что выбирать.",
            "body_md": (
                "- **AI** — на каждом экране языковая модель (Gemma 4 / Qwen 3.5) выбирает действие по скриншоту. Медленнее, но глубже.\n"
                "- **MC** (Monte-Carlo) — случайный обход с приоритетом новых состояний. Быстрый, без использования модели.\n"
                "- **Hybrid** — MC для уже виденных экранов, AI для новых. Рекомендуется по умолчанию."
            ),
        },
        {
            "slug": "scenarios",
            "section": S.SCENARIOS.value,
            "sort_order": 0,
            "title": "Что такое сценарии",
            "excerpt": "Повторяемые маршруты, которые агент проходит перед исследованием.",
            "body_md": (
                "Сценарий — это последовательность действий, описанная на естественном языке. "
                "Агент исполняет сценарий как «разминку» перед свободным обходом, чтобы попасть в нужное состояние.\n\n"
                "### Пример\n"
                "```\n"
                "1. Ввести в поле «Телефон» значение +7 (987) 654-32-10\n"
                "2. Нажать «Далее»\n"
                "3. Ввести код 1234 в поле «Код из SMS»\n"
                "4. Нажать «Войти»\n"
                "```\n\n"
                "Сценарии лежат в разделе **Сценарии** и могут использовать переменные из тестовых данных."
            ),
        },
        {
            "slug": "install-app",
            "section": S.APPS.value,
            "sort_order": 0,
            "title": "Установка приложений из магазина",
            "excerpt": "Магазин расширений, установка, настройки.",
            "body_md": (
                "**Магазин приложений** — раздел, где можно установить интеграции (AlfaGen, Jira) "
                "и прочие расширения в рабочее пространство.\n\n"
                "Как установить:\n\n"
                "1. Откройте **Магазин приложений**.\n"
                "2. Найдите нужное в поиске или выберите из категорий.\n"
                "3. Нажмите **Установить** — появится в **Приложения пространства**.\n"
                "4. Откройте настройки установки (🔧) и заполните обязательные поля.\n\n"
                "Установка возможна только модератором пространства."
            ),
        },
        {
            "slug": "upload-bundle",
            "section": S.APPS.value,
            "sort_order": 1,
            "title": "Загрузка собственного приложения",
            "excerpt": "Формат бандла, манифест, модерация.",
            "body_md": (
                "Для загрузки нужно право `apps.upload`.\n\n"
                "### Структура ZIP-архива\n"
                "```\n"
                "/manifest.json         метаданные, UI-слоты, настройки, хуки\n"
                "/frontend/             статические файлы iframe\n"
                "/logic/                серверные скрипты (Python)\n"
                "/logo.png              иконка (обязательно)\n"
                "/cover.jpg             обложка (опционально)\n"
                "/screenshots/          картинки для карточки\n"
                "/README.md             рендерится на детальной странице\n"
                "```\n\n"
                "После загрузки приложение попадает в статус **На модерации**. "
                "Админ (право `apps.moderate`) может его одобрить или отклонить."
            ),
        },
        {
            "slug": "rbac",
            "section": S.ADMIN.value,
            "sort_order": 0,
            "title": "Управление правами доступа",
            "excerpt": "Системные роли, кастомные роли, тонкие права.",
            "body_md": (
                "Права доступа в Маркове — это набор строк вида `resource.action` "
                "(например, `runs.create`, `apps.upload`).\n\n"
                "Роли — это именованные наборы прав. Системные роли: `admin`, `tester`, `viewer` — "
                "изменять их нельзя, но можно создать свои.\n\n"
                "Управление — в **Пользователи → Роли**."
            ),
        },
        {
            "slug": "api-auth",
            "section": S.API.value,
            "sort_order": 0,
            "title": "Аутентификация в API",
            "excerpt": "Bearer JWT, как получить и использовать.",
            "body_md": (
                "Все эндпоинты (кроме `/auth/jwt/login`) требуют заголовка `Authorization: Bearer <token>`.\n\n"
                "Получить токен:\n\n"
                "```bash\n"
                "curl -X POST https://markov.example.com/auth/jwt/login \\\n"
                "  -H 'Content-Type: application/x-www-form-urlencoded' \\\n"
                "  -d 'username=you@example.com&password=...'\n"
                "```\n\n"
                "В ответе `access_token`. Токен живёт ~24 часа. "
                "Полный каталог эндпоинтов — во вкладке **API** раздела **Настройки**."
            ),
        },
        {
            "slug": "slow-runs",
            "section": S.TROUBLESHOOTING.value,
            "sort_order": 0,
            "title": "Запуск идёт слишком медленно",
            "excerpt": "Что проверить, если агент ползёт.",
            "body_md": (
                "Основные причины:\n\n"
                "1. **Режим AI** — каждое действие проходит через языковую модель. Попробуйте **Hybrid** или **MC**.\n"
                "2. **Слабый хост** — обход на macOS Simulator/Android Emulator требует CPU. Проверьте `Activity Monitor`.\n"
                "3. **Сетевые запросы в приложении** — если ваш бэкенд медленный, агент ждёт ответа экрана.\n"
                "4. **Максимум шагов слишком большой** — начните с 200, увеличивайте, если нужно больше покрытия.\n\n"
                "Обратитесь через форму внизу страницы справки, если ничего не помогло."
            ),
        },
        {
            "slug": "iframe-401",
            "section": S.TROUBLESHOOTING.value,
            "sort_order": 1,
            "title": "Приложение в магазине падает с 401",
            "excerpt": "Iframe не может загрузить ресурсы — что делать.",
            "body_md": (
                "Если вы видите `401 Unauthorized` при открытии приложения из магазина — "
                "ваша установка на старой версии, где iframe требовал Bearer-токен.\n\n"
                "Обновите приложение через **Приложения пространства → Обновления**. "
                "В версии 2.0.1 и выше iframe грузится через статический mount `/app-bundles/` без авторизации, "
                "а API-вызовы идут через короткоживущий installation-токен."
            ),
        },
    ]

    from app.models.help import HelpArticle as _H
    async with async_session_maker() as session:
        for item in seeds:
            q = await session.execute(select(_H).where(_H.slug == item["slug"]))
            if q.scalar_one_or_none() is not None:
                continue
            session.add(_H(**item))
        await session.commit()


async def seed_release_notes() -> None:
    """Seed product changelog. Idempotent — skip versions we already have.

    Keep the body readable as markdown: the frontend renders with
    ``react-markdown`` so we can use lists, bold, code spans, links.
    """
    from datetime import datetime as _dt, timezone as _tz
    from app.models.release_note import ReleaseNote

    notes = [
        {
            "version": "0.5.0",
            "title": "Аватар, навигация и расширенная палитра",
            "excerpt": "Личный аватар, выбор пунктов сайдбара, цвета панели и ссылок, размер шрифта.",
            "released_at": _dt(2026, 4, 23, 4, 0, tzinfo=_tz.utc),
            "body_md": (
                "## Что нового\n\n"
                "### 👤 Аватар пользователя\n"
                "В **Профиль → Аватар** загружается своя картинка (PNG/JPG/WebP/GIF, "
                "до 2 МБ). Без загруженного аватара — стандартный красный круг с "
                "первой буквой почты. Видна вам в сайдбаре и всем коллегам в "
                "пространстве.\n\n"
                "### 🧭 Настройка навигации\n"
                "В **Профиль → Навигация** — список встроенных пунктов сайдбара с "
                "чекбоксами. Снимаете галочку — пункт пропадает только у вас. "
                "Доступ при этом сохраняется (можно зайти по прямой ссылке) — "
                "просто экономим место в меню.\n\n"
                "### 🎨 Расширенная палитра темы\n"
                "Семантические цвета (success/warning/error/info) ушли в "
                "справочники, где им и место. Взамен в палитре появилось то, "
                "что реально хотелось менять:\n\n"
                "- **Акценты и ссылки** — основной цвет, ссылки в обычном "
                "состоянии и при наведении\n"
                "- **Поверхности** — фон карточек и фон страницы\n"
                "- **Боковая панель** — фон сайдбара, подсветка пункта при "
                "наведении, активный пункт\n\n"
                "Плюс к этому — общие параметры: скругление углов и шрифт. "
                "Всё это работает как для системного бренда, так и для "
                "персональных оверрайдов в профиле.\n\n"
                "### 🔠 Размер шрифта\n"
                "В **Профиль → Моя тема** — слайдер 12-18 px. Применяется "
                "глобально ко всему интерфейсу. Хранится на сервере, переезжает "
                "вместе с вами на любое устройство.\n\n"
                "### 🛠 Под капотом\n"
                "- Favicon наконец чинится — `public/` теперь действительно "
                "копируется в Docker-сборку, плюс версионный URL дёргает "
                "Safari из его favicon-кэша\n"
                "- Цвета сайдбара передаются в AntD через CSS-переменные — "
                "`--ta-sidebar-bg`, `--ta-sidebar-hover`, `--ta-sidebar-active`\n"
                "- Описания в тёмной теме читаются через `colorTextTertiary` "
                "(обычный `Typography type=\"secondary\"` слишком тусклый)"
            ),
        },
        {
            "version": "0.4.0",
            "title": "Бренд, темы и «Что нового»",
            "excerpt": "Полная кастомизация бренда компании, темы интерфейса и новый центр релиз-нот.",
            "released_at": _dt(2026, 4, 23, 1, 0, tzinfo=_tz.utc),
            "body_md": (
                "## Что нового\n\n"
                "### 🎨 Бренд на всю систему\n"
                "Админ в **Настройки → Бренд** задаёт название продукта, логотип "
                "(со всеми вариантами: основной, обратная сторона флипа, favicon), "
                "а также палитру темы. Удобно, если Марков разворачивается не только "
                "для Альфа-Банка — достаточно один раз подставить свой бренд.\n\n"
                "### 🌗 Светлая и тёмная темы\n"
                "Переключатель в верхней панели: **Светлая / Тёмная / Как в системе**. "
                "Мода запоминается в браузере. Тёмная тема использует собственные "
                "оттенки для контраста.\n\n"
                "### 🖌️ Персональные оверрайды\n"
                "В **Профиль → Моя тема** каждый пользователь может переопределить "
                "любой оттенок или размер шрифта под свой монитор. Изменения видны "
                "только этому пользователю и сохраняются на сервере — переезжают "
                "вместе с вами с устройства на устройство.\n\n"
                "### 📣 Что нового\n"
                "Этот раздел как раз и есть «Что нового». Плашка в сайдбаре подсвечивает "
                "непрочитанные релизы красной точкой. Прочитать и закрыть — клик на X "
                "в углу заметки. Также доступно из клика по версии в нижнем углу."
            ),
        },
        {
            "version": "0.3.0",
            "title": "Магазин приложений",
            "excerpt": "AlfaGen, Jira-интеграция, Hello World — каталог, установка, моё и все.",
            "released_at": _dt(2026, 4, 22, 18, 0, tzinfo=_tz.utc),
            "body_md": (
                "## Что нового\n\n"
                "- **Магазин приложений** — Apple-style витрина с разделами "
                "«Рекомендуем», «Популярные», «Новое». Поиск, категории из справочника, "
                "hero-карточки с обложками.\n"
                "- **Установка в рабочее пространство** — с подписью от админа через "
                "новый флоу модерации (`apps.upload` / `apps.moderate`).\n"
                "- **AlfaGen Sandbox** — полноценный клиент корпоративного LLM: чат, "
                "загрузка файлов для RAG, переключение модели, демо-режим с моками.\n"
                "- **Jira-интеграция** — автоматически заводит баги из найденных "
                "агентом дефектов.\n"
                "- **История установок** — журнал кто/что/когда установил, обновил, "
                "выключил или удалил. Видна в «Приложения пространства → История».\n"
                "- **Обратная связь и справка** — Apple-style справочный центр с "
                "поиском, популярными статьями и формой обращения. Обращения видны "
                "админу в разделе «Обращения»."
            ),
        },
        {
            "version": "0.2.0",
            "title": "Справочники, RBAC, рабочие пространства",
            "excerpt": "Системные справочники, гибкие роли, мультитенантность.",
            "released_at": _dt(2026, 4, 21, 12, 0, tzinfo=_tz.utc),
            "body_md": (
                "## Что нового\n\n"
                "- **Рабочие пространства** — мультитенантная изоляция запусков, "
                "сценариев и тестовых данных. Переключение в шапке.\n"
                "- **RBAC** — системные роли (admin / tester / viewer) плюс "
                "пользовательские с cherry-pick конкретных прав. Установки приложений, "
                "загрузка моделей, модерация расширений — всё по своим правам.\n"
                "- **Системные справочники** — платформы, версии ОС, типы устройств, "
                "типы действий, категории приложений. Редактируются админом, "
                "используются везде как единый источник правды.\n"
                "- **Пользовательские справочники** — свои словари с правами доступа "
                "и ролями редактирования."
            ),
        },
        {
            "version": "0.1.0",
            "title": "Автоматизация устройств",
            "excerpt": "iOS Simulator, Android Emulator, автоматический lifecycle запуска.",
            "released_at": _dt(2026, 4, 20, 15, 0, tzinfo=_tz.utc),
            "body_md": (
                "## Что нового\n\n"
                "- **iOS Simulator** — автосоздание, boot, install, launch, cleanup "
                "через `xcrun simctl`. Поддерживаются iPhone и iPad, iOS 18+.\n"
                "- **Android Emulator** — то же самое через `avdmanager` / `adb`. "
                "Pixel 9 Pro XL, Android 36.\n"
                "- **Загрузка сборки через UI** — `.app.zip`, `.ipa`, `.apk`; "
                "bundle_id извлекается автоматически.\n"
                "- **Администрирование устройств** — админ выбирает, какие устройства "
                "и версии ОС видят тестировщики при создании запуска."
            ),
        },
        {
            "version": "0.0.1",
            "title": "Прототип Маркова",
            "excerpt": "Первая версия: MCTS, AI/MC/Hybrid режимы, два режима вывода.",
            "released_at": _dt(2026, 4, 15, 10, 0, tzinfo=_tz.utc),
            "body_md": (
                "## Что нового\n\n"
                "- **Три режима агента**: AI (языковая модель на каждом шаге), "
                "MC (случайный обход по Monte-Carlo с приоритетом новых состояний), "
                "Hybrid (MC + AI для новых экранов — рекомендуется).\n"
                "- **Gemma 4 E4B + Qwen 3.5 35B-A3B** — две встраиваемые модели "
                "по умолчанию, обе релизнулись 7 апреля 2026.\n"
                "- **Граф переходов** — real-time визуализация исследования "
                "с возможностью открыть экран и увидеть скриншот.\n"
                "- **Сценарии** — повторяемые маршруты на естественном языке, "
                "исполняемые агентом перед свободным обходом.\n"
                "- **База знаний (RAG)** — загрузка PDF/DOCX/TXT для «сверки» "
                "поведения приложения со спецификацией."
            ),
        },
    ]

    async with async_session_maker() as session:
        for item in notes:
            r = await session.execute(
                select(ReleaseNote).where(ReleaseNote.version == item["version"])
            )
            if r.scalar_one_or_none() is not None:
                continue
            session.add(ReleaseNote(**item))
        await session.commit()


async def seed_initial_models() -> None:
    """Insert the two pre-installed LLM models if they're not in the table yet."""
    inserted_any = False
    async with async_session_maker() as session:
        for spec in INITIAL_MODELS:
            existing = await session.execute(
                select(LLMModel).where(LLMModel.name == spec["name"])
            )
            if existing.scalar_one_or_none() is not None:
                print(f"[seed] LLM model {spec['name']} already exists.")
                continue
            session.add(LLMModel(**spec, is_active=True))
            inserted_any = True
            print(f"[seed] Created LLM model: {spec['name']}")
        await session.commit()

        try:
            await regenerate_swap_config(session)
            print(f"[seed] Wrote {settings.llm_swap_config_path}")
        except Exception as exc:
            print(f"[seed] Failed to write swap config: {exc}")
