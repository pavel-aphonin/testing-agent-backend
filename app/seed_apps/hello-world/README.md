# Hello World

Эталонное приложение для платформы «Марков». Показывает:

- Как устроен `manifest.json`
- Как получить installation-token от host-а через `postMessage`
- Как вызвать API Маркова из iframe с этим токеном
- Как оформить слоты в UI (sidebar + corner)
- Шаблон handler-а для backend hooks

## Структура

```
hello-world/
├── manifest.json         # метаданные + слоты + настройки
├── frontend/
│   └── index.html        # UI iframe
├── logic/
│   └── on_defect.py      # пример handler-а (pinboard, не выполняется автоматически)
└── README.md             # этот файл
```

## API

- `GET /api/apps/me/context` — начальный контекст (user_id, workspace_id,
  permissions, settings). Всё что нужно для рендера.
- Любой другой endpoint `/api/...` — работает с тем же Bearer-токеном,
  с правами = пересечение user.permissions и manifest.permissions_required.
