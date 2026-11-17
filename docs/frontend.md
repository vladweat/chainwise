# Веб-интерфейс

## Стек

- Next.js 15 (App Router)
- React 19
- TypeScript 5
- Tailwind CSS 4
- Framer Motion 11
- Shadcn/ui (New York): `Button`, `Card`, `Input`, `Label`, `Badge`, `Separator`

## Структура

```
frontend/
├── Dockerfile
├── package.json
├── components.json
├── scripts/prepare-deps.sh
├── src/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx                 # главная страница и состояние сервисов
│   │   ├── login/page.tsx
│   │   ├── analyze/page.tsx
│   │   ├── history/page.tsx
│   │   ├── contracts/[id]/page.tsx
│   │   └── globals.css
│   ├── hooks/
│   │   ├── use-contract-status.ts
│   │   ├── use-contract-actions.ts
│   │   └── use-contract-chat.ts
│   ├── lib/
│   │   ├── api/                     # auth, contracts, chat, client
│   │   ├── api.ts                   # единая точка экспорта
│   │   ├── contract-status.ts
│   │   ├── contract-storage.ts
│   │   ├── auth-storage.ts
│   │   ├── chat.ts
│   │   ├── findings-filter.ts
│   │   ├── format.ts
│   │   └── utils.ts
│   ├── components/
│   │   ├── app-header.tsx
│   │   ├── auth-provider.tsx
│   │   ├── auth-gate.tsx
│   │   ├── guest-gate.tsx
│   │   ├── auth-panel.tsx
│   │   ├── google-sign-in-button.tsx
│   │   ├── health-status.tsx
│   │   ├── contract-upload-panel.tsx
│   │   ├── contract-workspace.tsx
│   │   ├── contract-indexing-status.tsx
│   │   ├── contract-chat-panel.tsx
│   │   ├── contract-findings-list.tsx
│   │   ├── contract-findings-panel.tsx
│   │   ├── contract-source-viewer.tsx
│   │   ├── history-list.tsx
│   │   ├── contract-status-badge.tsx
│   │   ├── risk-score-badge.tsx
│   │   └── ui/                      # компоненты shadcn
│   ├── lib/
│   │   ├── api.ts
│   │   ├── auth-storage.ts
│   │   ├── contract-storage.ts
│   │   ├── findings-filter.ts
│   │   ├── format.ts
│   │   └── utils.ts                 # cn()
│   └── types/
└── next.config.ts
```

## API-клиент

Базовый URL: `NEXT_PUBLIC_API_URL` (по умолчанию `http://localhost:8001`).

JWT: cookie с HttpOnly `cw_access` / `cw_refresh` (`credentials: 'include'`). Токен Bearer из JSON-ответа входа интерфейс не сохраняет. Старый ключ `localStorage.chainwise_token` сбрасывается при загрузке.

| Функция | Эндпоинт |
|---|---|
| `fetchHealth()` | `GET /health` |
| `register` / `login` / `logout` | `POST /api/v1/auth/register` / `login` / `logout` |
| `loginWithGoogle(idToken)` | `POST /api/v1/auth/google` |
| `fetchMe()` | `GET /api/v1/auth/me` |
| `deleteAccount()` | `DELETE /api/v1/auth/me` |
| `deleteContract(id)` | `DELETE /api/v1/contracts/{id}` |
| `analyzeContract(address, chainId)` | `POST /api/v1/contracts/analyze` |
| `uploadContract(file, chainId)` | `POST /api/v1/contracts/analyze/upload` |
| `fetchContractStatus(id)` | `GET /api/v1/contracts/{id}/status` |
| `fetchContracts()` | `GET /api/v1/contracts` (канонический; `/history` — алиас) |
| `fetchContractChatHistory(id)` | `GET /api/v1/contracts/{id}/chat` |
| `streamContractChat(id, question, onEvent)` | `POST /api/v1/contracts/{id}/chat` SSE |
| `downloadContractReport(id)` | `GET /api/v1/contracts/{id}/report` PDF |
| `fetchContractFindings(id)` | `GET /api/v1/contracts/{id}/findings` |
| `fetchContractSource(id)` | `GET /api/v1/contracts/{id}/source` |

Сети в интерфейсе: Ethereum (1), BSC (56), Abstract (11124). Для файла `chain_id` по умолчанию `0`.

Статус индексации опрашивается каждые 2 с на `/contracts/[id]`, пока не `ready` / `loaded` / `failed`.

## Экраны

| Маршрут | Описание |
|---|---|
| `/` | Главная страница, состояние сервисов и переход к анализу |
| `/login` | Почта и пароль + Google Identity Services |
| `/analyze` | Загрузка адреса или `.sol`/`.zip` |
| `/contracts/[id]` | После `ready`: чат + сворачиваемая панель найденных проблем и исходника (на телефоне — оверлей). «Отчёт» — оверлей шагов. **PDF** скачивается через `GET /contracts/{id}/report` |
| `/history` | Список контрактов пользователя |

Кнопка **PDF** на `/contracts/[id]` (шапка и оверлей) вызывает `downloadContractReport` → `GET /api/v1/contracts/{id}/report`. Контракт должен быть `ready`. После изменения интерфейса — `make up-frontend`.

Оверлей «Отчёт индексации» — шаги пайплайна (закрыть: крестик, фон, Escape).

Работа в интерфейсе: клик по цитате чата подсвечивает те же строки исходника, что и найденная проблема. Пустой индекс — тот же отказ, что `NO_CONTEXT_ANSWER` на бэкенде, без «Печатает…».

Google: `NEXT_PUBLIC_GOOGLE_CLIENT_ID` (тот же `GOOGLE_CLIENT_ID`, что у сервера). Без значения кнопка скрыта. В Google Cloud Console в разделе разрешённых источников JavaScript добавьте `http://localhost:3000` и `http://localhost:3001`.

## Dockerfile для сборки без сети

```dockerfile
COPY node_modules ./node_modules
COPY . .
RUN npm run build
CMD ["node", "node_modules/next/dist/bin/next", "start", "-H", "0.0.0.0", "-p", "3000"]
```

`NEXT_PUBLIC_*` задаются как `ARG` при сборке. После смены URL или идентификатора клиента Google:

```bash
make up-frontend
```

## Локальная разработка

```bash
cd frontend
npm install
NEXT_PUBLIC_GOOGLE_CLIENT_ID='идентификатор клиента Google' npm run dev
```

Сервер разработки слушает порт 3000; интерфейс в Docker доступен на порту 3001. Настройки CORS API разрешают оба адреса.

## Правила интерфейса

- Tailwind 4 через `@import "tailwindcss"` в `globals.css`
- Тёмная тема по умолчанию
- Shadcn-примитивы в `src/components/ui/`; панели с анимацией — `Panel`
- Framer Motion для карточек и шагов индексации
- Адаптив: размер области просмотра `device-width` и `viewportFit: cover`; шапка и чат учитывают безопасную область экрана; отчёт на телефоне — нижняя выдвижная панель; поля ввода 16px (без зума iOS).
