# AlfaGen интеграция

Отправляет найденные агентом дефекты в AlfaGen для генерации тест-кейсов.

## Настройка

1. Получите API-токен в AlfaGen.
2. В настройках установленного приложения укажите:
   - **URL AlfaGen**: адрес песочницы (например `https://alfagen-sandbox.alfabank.ru`)
   - **API-токен**: токен доступа
   - **Автоотправка приоритетов**: по умолчанию `P0,P1,P2`.

## Как работает

При событии `defect.created` хэндлер `builtin:alfagen.send_defect` POST-ит payload в `<api_url>/api/v1/defects`. Логи доставки — в таблице `app_event_deliveries`.
