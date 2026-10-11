# Тендер-Сверка

Полная версия разрабатывается в ветке `codex/full-version`; сохранённый MVP —
ветка `main`, тег `mvp-baseline`. [Порядок реализации](docs/full-version/task-plan.md),
[среда разработки](docs/full-version/development.md) и
[состояние требований и приёмки](docs/full-version/implementation-status.md).
Новые модули пока не заменяют весь сценарий MVP. Инструкции ниже относятся к MVP.

Локальный MVP: документация → редактируемый чек-лист → 1–3 заявки → матрица с цитатами → XLSX.

FastAPI + SQLite + OpenAI-совместимый API с Pydantic-валидацией, Next.js + Tailwind + shadcn/ui. Авторизация — заглушка demo. Очередей, миграций и отдельных worker нет.

## Установка

Требуются Python 3.11+ и Node.js 20.9+.

    python -m venv backend/.venv
    backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
    cd frontend
    npm install
    cd ..
    Copy-Item backend/.env.example backend/.env

В backend/.env задайте OPENAI_API_KEY. Модель по умолчанию — gpt-6.1-sol, её можно изменить через OPENAI_MODEL. Ключ остаётся на backend.

## Backend через AgentRouter

Для использования кредитов AgentRouter перенесите настройки из [backend/.env.agentrouter.example](backend/.env.agentrouter.example) в backend/.env:

    AI_MODE=openai
    OPENAI_API_BASE=https://agentrouter.org/v1
    OPENAI_API_STYLE=chat
    OPENAI_MODEL=deepseek-v4-flash
    CODEX_GATEWAY_API_KEY=ваш_ключ_AgentRouter
    OPENAI_API_KEY=

AI_MODE=openai включает OpenAI-совместимый клиент и для шлюза. OPENAI_API_BASE задаёт адрес API; пустое значение использует стандартный адрес https://api.openai.com/v1. Также поддерживается стандартная переменная SDK OPENAI_BASE_URL, если OPENAI_API_BASE не задана.

Для адреса agentrouter.org ключ CODEX_GATEWAY_API_KEY имеет приоритет. Альтернативно можно записать ключ AgentRouter в OPENAI_API_KEY и оставить CODEX_GATEWAY_API_KEY пустой. Для OpenAI и других адресов используется только OPENAI_API_KEY. После изменения backend/.env перезапустите backend.

OPENAI_MODEL принимает ID модели без фиксированного списка: deepseek-v4-flash, gpt-6-astra, claude-opus-5, claude-opus-4-8 или другой ID, доступный вашему ключу AgentRouter. Названия приведены по запрошенной конфигурации; доступность и списание кредитов определяет шлюз.

Для AgentRouter backend по умолчанию использует POST /v1/chat/completions с response_format={"type":"json_object"}. Схема ответа передаётся в системном сообщении; JSON проверяется через Pydantic, затем проверяются цитаты и полнота результатов. Native Structured Outputs шлюзу не требуются. Пустые, обрезанные и не соответствующие схеме ответы дают ошибку с возможностью повторить обработку.

OPENAI_API_STYLE управляет протоколом: chat — Chat Completions с JSON mode; responses — Responses API с native Structured Outputs. Пустое значение выбирает chat для agentrouter.org и responses для остальных адресов. Для другого совместимого шлюза можно явно задать chat. Запрос не перенаправляется на другой сервис при ошибке. Модель и шлюз должны поддерживать выбранный endpoint и JSON mode.

JSON mode обеспечивает JSON, но не гарантирует соблюдение схемы: её проверяет backend. Различие описано в [документации OpenAI](https://learn.chatgpt.com/api/docs/guides/structured-outputs#json-mode).

Ошибки шлюза: content-blocked означает блокировку содержимого запроса на стороне AgentRouter; unauthorized client detected — отказ этому API-клиенту. Смена Responses на Chat Completions сама по себе эти отказы не устраняет. Приложение показывает их отдельно; проверьте ограничения шлюза или обратитесь в его поддержку.

## Codex CLI через AgentRouter

Это отдельная настройка инструмента разработки: backend не читает config.toml, а Codex CLI не загружает backend/.env.

Готовый пример: [docs/agentrouter/config.toml.example](docs/agentrouter/config.toml.example). Перенесите его настройки в пользовательский ~/.codex/config.toml (Windows: $HOME/.codex/config.toml), сохранив остальные существующие настройки:

    model = "deepseek-v4-flash"
    model_provider = "AgentRouter"

    [model_providers.AgentRouter]
    base_url = "https://agentrouter.org/v1"
    env_key = "CODEX_GATEWAY_API_KEY"
    name = "AgentRouter"
    wire_api = "responses"

wire_api относится только к Codex CLI: этот клиент требует совместимый Responses endpoint. Настройка backend OPENAI_API_STYLE=chat не меняет протокол CLI. Поддержка Chat Completions моделью в приложении сама по себе не означает совместимость этой модели с Codex CLI.

Провайдера следует задавать в пользовательском config.toml: Codex игнорирует настройки провайдера в проектном .codex/config.toml. Поля base_url, env_key и wire_api описаны в [официальной документации Codex](https://developers.openai.com/codex/config-reference).

Перед запуском CLI задайте ключ в текущем терминале PowerShell:

    $env:CODEX_GATEWAY_API_KEY = "ваш_ключ_AgentRouter"
    codex

Стандартный каталог для команды /model содержит модели OpenAI; этот пример не заменяет каталог. Для выбора модели AgentRouter запускайте CLI с переопределением конфигурации:

    codex --config model='"deepseek-v4-flash"'
    codex --config model='"gpt-6-astra"'
    codex --config model='"claude-opus-5"'
    codex --config model='"claude-opus-4-8"'

Эти команды предполагают model_provider = "AgentRouter" в пользовательском config.toml. Если провайдер по умолчанию другой, укажите оба параметра:

    codex --config 'model_provider="AgentRouter"' --config 'model="deepseek-v4-flash"'

Таблица model_providers.AgentRouter.models не является поддерживаемым полем Codex CLI и не добавляет модели в /model. Поэтому запрошенный справочник названий сохранён в примере как комментарии:

    # [model_providers.AgentRouter.models]
    # deepseek-v4-flash = { name = "DeepSeek V4 Flash" }
    # "gpt-6-astra" = { name = "GPT-6 Astra" }
    # "claude-opus-5" = { name = "Claude Opus 5" }
    # "claude-opus-4-8" = { name = "Claude Opus 4.8" }

Актуальный список полей и отдельная настройка каталога model_catalog_json приведены в [справочнике конфигурации Codex](https://developers.openai.com/codex/config-reference). Для переключения через --config отдельный каталог не нужен.

## Запуск с OpenAI

Первый терминал:

    cd backend
    .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

Второй терминал:

    cd frontend
    npm run dev

Откройте http://127.0.0.1:3000. API-документация: http://127.0.0.1:8000/docs.

## Учебный режим без API-ключа

Запустите backend с переменной AI_MODE=demo:

    cd backend
    $env:AI_MODE = "demo"
    .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

Ссылки на учебные файлы доступны в интерфейсе. Они также лежат в frontend/public/demo/. ТЗ и три заявки вымышлены; учебный провайдер работает только с комплектом DEMO-001 и не вызывает OpenAI. Автоматического перехода из OpenAI в учебный режим нет.

Порядок:

1. Загрузите tender.docx, проверьте и подтвердите шесть требований.
2. Загрузите bid-a.docx, bid-b.pdf, bid-c.xlsx в три слота с названиями компаний.
3. Сравните: Альфа — 100%, Бета — 32% с риском, Гамма — 92,3% по проверенным пунктам, диапазон 80–93,3%.
4. Нажмите статус и проверьте цитаты; скачайте XLSX.
5. Для проверки пересчёта измените порог памяти на «не менее 8 ГБ»: оценка Беты станет 52%.

Для проверки собственных документов используйте AI_MODE=openai. После изменения конфигурации перезапустите backend.

Учебные файлы можно пересоздать:

    backend/.venv/Scripts/python.exe scripts/generate_demo.py

## Ограничения и поведение

- PDF с текстовым слоем, DOCX, XLSX. До пяти файлов на пакет, 10 МБ на файл, 100 000 извлечённых символов на пакет.
- Сканы, изображения, чертежи и формулы не анализируются. Пустая страница PDF выдаёт ошибку вместо тихого пропуска.
- AI определяет статусы; Python детерминированно вычисляет скоринг. Цитаты проверяются по соответствующему пакету участника.
- ID блоков привязан к странице PDF, абзацу/строке таблицы DOCX или строке листа XLSX. Координат и PDF-viewer нет.
- Изменение чек-листа и замена заявки очищают результаты. Версий и ролей нет.
- Данные сохраняются в backend/data/. BackgroundTasks работают в процессе FastAPI; незавершённая задача после перезапуска становится ошибкой с возможностью повторить.
- Используйте один процесс Uvicorn, без --workers и без --reload во время обработки. Приложение предназначено для локального/закрытого демо.
- Запросы API отправляются с store=false; это не отменяет все политики хранения выбранного провайдера.

## Проверки

    cd backend
    .venv/Scripts/python.exe -m pytest -q
    cd ../frontend
    npm run check
    npm run build

Тесты покрывают скоринг, все три парсера, цитаты, три заявки, редактирование, инвалидирование результатов, восстановление после перезапуска и XLSX. В API-тестах заменяется только внешний AI-вызов; документы, SQLite и экспорт обрабатываются реально. Отдельные тесты с настоящим OpenAI SDK и HTTP-заглушкой проверяют Chat Completions/Responses, JSON mode и валидацию, передачу четырёх ID моделей, приоритет ключей и отсутствие отправки ключа шлюза на другие адреса.
