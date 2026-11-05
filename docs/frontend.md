# Frontend

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
│   │   ├── page.tsx                 # лендинг + health
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
│   │   ├── api.ts                   # barrel re-export
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
│   │   └── ui/                      # shadcn primitives
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

Базовый URL: `NEXT_PUBLIC_API_URL` (default `http://localhost:8001`).

JWT: HttpOnly cookies `cw_access` / `cw_refresh` (`credentials: 'include'`). Bearer в JSON login-ответа UI не сохраняет. Старый ключ `localStorage.chainwise_token` сбрасывается при загрузке.

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

Сети в UI: Ethereum (1), BSC (56), Abstract (11124). Для файла `chain_id` по умолчанию `0`.

Статус индексации опрашивается каждые 2 с на `/contracts/[id]`, пока не `ready` / `loaded` / `failed`.

## Экраны

| Маршрут | Описание |
|---|---|
| `/` | Лендинг, health, CTA |
| `/login` | Email/пароль + Google Identity Services |
| `/analyze` | Загрузка адреса или `.sol`/`.zip` |
| `/contracts/[id]` | После `ready`: чат + сворачиваемая панель findings/исходник (на мобилке — оверлей). «Отчёт» — оверлей шагов. **PDF** качает `GET /contracts/{id}/report` |
| `/history` | Список контрактов пользователя |

Кнопка **PDF** на `/contracts/[id]` (шапка и оверлей) вызывает `downloadContractReport` → `GET /api/v1/contracts/{id}/report`. Контракт должен быть `ready`. После смены UI — `make up-frontend`.

Оверлей «Отчёт индексации» — шаги пайплайна (закрыть: крестик, фон, Escape).

Дальше по UI: клик по цитате чата подсвечивает те же строки исходника, что и finding. Пустой индекс — тот же отказ, что `NO_CONTEXT_ANSWER` на бэкенде, без «Печатает…».

Google: `NEXT_PUBLIC_GOOGLE_CLIENT_ID` (тот же `GOOGLE_CLIENT_ID`, что у backend). Без значения кнопка скрыта. В Google Cloud Console в Authorized JavaScript origins добавьте `http://localhost:3000` и `http://localhost:3001`.

## Offline Dockerfile

```dockerfile
COPY node_modules ./node_modules
COPY . .
RUN npm run build
CMD ["node", "node_modules/next/dist/bin/next", "start", ...]
```

`NEXT_PUBLIC_*` задаются как `ARG` при сборке. После смены URL или Google Client ID:

```bash
make up-frontend
```

## Локальный dev

```bash
cd frontend
npm install
NEXT_PUBLIC_GOOGLE_CLIENT_ID=<тот же GOOGLE_CLIENT_ID> npm run dev
```

Dev-сервер слушает порт 3000; в Docker frontend проброшен на **3001**. CORS backend разрешает оба.

## UI-конвенции

- Tailwind 4 через `@import "tailwindcss"` в `globals.css`
- Тёмная тема по умолчанию
- Shadcn-примитивы в `src/components/ui/`; панели с анимацией — `Panel`
- Framer Motion для карточек и шагов индексации
- Адаптив: viewport `device-width` + `viewportFit: cover`; шапка и чат учитывают safe-area; отчёт на телефоне — нижний sheet; поля ввода 16px (без зума iOS).
