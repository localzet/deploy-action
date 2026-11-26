# Localzet Deploy

Универсальный GitHub Action для Docker Compose: Laravel, NestJS, Next.js, React, FastAPI и любые другие приложения.

[English documentation](README.md)

## Быстрый старт

Скопируйте [пример workflow](examples/deploy.yml). На сервере нужны Linux, Python с `tarfile.data_filter`, Docker Compose v2, SSH-пользователь с правом запуска Docker и записи в каталог проекта. Передавайте SSH-параметры через GitHub Secrets: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` или `DEPLOY_PASSWORD`, `DEPLOY_KNOWN_HOSTS`, при необходимости `DEPLOY_PORT`. Поддерживаются `SERVER_*`, `DEPLOY_IP`, `DEPLOY_ADDRESS`. Ключ сервера проверяется по доверенному known_hosts. Не отключайте проверку.

## Параметры

| Параметр | Назначение |
|---|---|
| `compose-files` | Compose-файлы в порядке наложения, по одному на строку |
| `project-name` | Постоянное имя Compose-проекта |
| `deploy-path` | Абсолютный каталог проекта на сервере |
| `shared-paths` | Пути постоянных данных, по одному на строку |
| `profiles` | Compose-профили |
| `build-mode` | `vps` (по умолчанию), `runner` (архив образов), `registry` (публикация образов) |
| `registry-prefix` | Репозиторий registry без тега; например `ghcr.io/owner/project` |
| `pull-policy` | `always`, `missing`, `never` для внешних образов |
| `wait-timeout` / `health-timeout` | Таймауты Compose и HTTP в секундах |
| `health-url` | Дополнительная HTTP-проверка с сервера |
| `keep-releases` | Число сохранённых релизов, по умолчанию 3 |
| `operation` | `deploy` или `rollback` |
| `backup` | Команды в работающих контейнерах предыдущего релиза |
| `pre-deploy` | Одноразовые команды в новых образах до запуска |
| `post-deploy` | Команды в новых контейнерах после проверки готовности |

## Данные и откат

По умолчанию релизы находятся в `~/.local/share/localzet-deploy/PROJECT`. Настройте `shared/.env`, именованные volumes и постоянные bind-mounts через `${DEPLOY_SHARED_DIR}`. Не храните секреты в Git или контексте сборки. Существующие неуправляемые Compose-проекты требуют отдельной миграции с резервным копированием; автоматического захвата нет. При неудаче запуска восстанавливаются предыдущие образы. Данные, миграции и внешние сервисы автоматически не откатываются. Нулевой простой не гарантируется.

При сборке на Runner передавайте только необходимые build-time значения через `DEPLOY_BUILD_ENV` (dotenv). Runtime `.env` остаётся на сервере. Для registry авторизуйте Runner и сервер отдельно; [пример GHCR](examples/ghcr.yml). Проверьте платформу образов и совпадение набора сервисов. Для приватных субмодулей настройте checkout с `submodules: recursive` и подходящими правами.

Хуки задаются как `SERVICE COMMAND ARG...` по одному на строку. Для shell используйте явный `sh -c`. Backup пропускается при первом деплое, pre-hook не запускает зависимости (БД должна быть доступна), post-hook при ошибке запускает откат приложения. Бэкап реализуется проектом и сохраняется в постоянное хранилище. Jump host использует отдельный или общий SSH-ключ и доверенный known_hosts (`DEPLOY_JUMP_HOST`, `DEPLOY_JUMP_USER`, `DEPLOY_JUMP_PORT`, `DEPLOY_JUMP_SSH_KEY`, `DEPLOY_JUMP_KNOWN_HOSTS`).

Подробности, ограничения и тесты: [README.md](README.md).
