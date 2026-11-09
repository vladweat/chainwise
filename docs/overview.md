# Обзор проекта

## ChainWise

**ChainWise** — веб-приложение для аудита смарт-контрактов Solidity. Пользователь загружает контракт (по адресу в блокчейне или файлом) и задаёт вопросы о безопасности и логике кода на естественном языке.

## Ключевые возможности

| Возможность | Статус |
|---|---|
| Загрузка по адресу (Etherscan: Ethereum, BSC, Abstract) | ✅ |
| Загрузка `.sol` / `.zip` | ✅ |
| Auth: email/password JWT, роли `user` / `admin` | ✅ |
| Google OAuth (backend; кнопка GIS на `/login`) | ✅ |
| Индексация: solc → AST, Slither JSON, semantic split | ✅ |
| Эмбеддинги MiniLM → Qdrant `code_chunks` | ✅ |
| Статус индексации в UI (polling) | ✅ |
| Экспериментальный advisory-индикатор Risk Score A/B/C/D | ✅ |
| RAG-чат с цитатами и SSE | ✅ |
| Кликабельные цитаты → строки исходника | ✅ |
| История контрактов | ✅ |
| PDF-отчёт | ✅ |
| JSON findings + фильтры в UI | ✅ |
| Явный `risk_model` (xgboost / heuristic) | ✅ |
| CI (lint + pytest) | ✅ |

## Целевая аудитория

- Разработчики Solidity, проводящие быстрый pre-audit
- Аудиторы, нуждающиеся в AI-помощнике с привязкой к исходникам
- Инженеры, изучающие полный containerized stack анализа контрактов

## Ограничения текущей версии

- ChainWise — pre-audit helper, не замена аудита. RAG отвечает только по проиндексированным чанкам.
- Risk Score A/B/C/D — эвристически сформированный advisory-индикатор предварительной проверки, не вероятность эксплуатации или аудит. XGBoost обучен на синтетических эвристических метках; fallback — эвристика (`risk_model`). Sensitivity/specificity на реальных уязвимостях неизвестны. См. [model card](./risk-score-model-card.md).
- Чат использует OpenAI-compatible Chat Completions API; по умолчанию настроены OpenAI и GPT-4o-mini. Endpoint и модель можно переопределить через окружение.
- PDF-отчёт не включает исходный код; findings в UI доступны с фильтрами и привязкой к строкам.
- Offline-сборка Docker: зависимости скачиваются на хост, образы собираются без интернета
- Данные контрактов изолируются по `user_id`; квоты настраиваются через окружение.
- Python 3.12 на хосте обязателен для backend venv
- Node.js 20+ на хосте обязателен для frontend
- Эмбеддинги: FastEmbed (ONNX `all-MiniLM-L6-v2`), не PyTorch Sentence-Transformers
- solc: `py-solc-x`, не `solc-select`
- Зависимости backend: `requirements.txt` (pip), не Poetry
- Compose предназначен для локального запуска; production deployment, backup/restore и monitoring stack не поставляются.
