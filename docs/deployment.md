# Развёртывание

Сервис работает на любом хосте с Docker и Docker Compose. Образ собран для `linux/amd64`.

## Токен ZenMoney

Сервис не получает токен сам: он берёт готовый токен доступа из `API_TOKEN`. Токен можно получить через OAuth2 в библиотеке `zenmoney_lib`, как в её `examples/fetch_diff.py`:

```python
from zenmoney import ZenmoneyOAuth2

oauth = ZenmoneyOAuth2(consumer_key=..., consumer_secret=..., username=..., password=...)
print(oauth.token.access_token)
```

Логин и пароль на хосте не нужны, там хранится только токен. Запрос к API описан в [архитектуре](architecture.md#запрос-к-api), менять его настройками не требуется.

Когда срок токена истечёт, запросы начнут завершаться ошибкой 401. Порядок замены описан в [эксплуатации](operations.md#замена-токена).

## Хранение копий

Копии лежат в именованном томе Docker `zenmoney-backups`. Docker создаёт его сам и выдаёт права пользователю контейнера, поэтому готовить папку не нужно. Том переживает пересоздание контейнера и обновление образа.

Том находится внутри каталога данных Docker, поэтому копии будут на том диске, где установлен Docker. Проверить путь можно так:

```bash
docker volume inspect zenmoney-backups --format '{{ .Mountpoint }}'
```

Команда `docker compose down -v` и удаление тома уничтожат все копии.

## Запуск через Docker Compose

Готовый образ публикуется в GitHub Container Registry, поэтому репозиторий на хосте не нужен. Создайте отдельный каталог и скачайте в него два файла, указав нужную версию:

```bash
curl -fLO https://raw.githubusercontent.com/nzvekov/zenmoney-backup/v1.0.1/deploy/docker-compose.yml
```

```bash
curl -fL -o .env https://raw.githubusercontent.com/nzvekov/zenmoney-backup/v1.0.1/deploy/.env.example
```

Заполните в `.env` значение `API_TOKEN`. Ограничьте доступ к файлу, в нём лежит секрет:

```bash
chmod 600 .env
```

Описание остальных переменных есть в [README](../README.md#настройки). Запустите контейнер:

```bash
docker compose up -d
```

## Запуск через Portainer

1. Откройте **Stacks → Add stack**, укажите имя `zenmoney-backup` и выберите **Web editor**.
2. Вставьте содержимое [`deploy/docker-compose.yml`](../deploy/docker-compose.yml).
3. В разделе **Environment variables** добавьте `API_TOKEN` и `IMAGE_TAG`, например `1.0.1`. Остальные переменные нужны, только если значения по умолчанию не подходят.
4. Нажмите **Deploy the stack**.

Логи находятся в **Containers → zenmoney-backup → Logs**. Этот способ не проверялся.

## Проверка

1. `docker compose logs --tail=100` показывает `Scheduled daily backup at 03:00`.
2. При пустом томе копия создаётся сразу после старта: в логах есть `Backup saved`.
3. Контрольная сумма сходится:

```bash
docker exec zenmoney-backup sh -c 'cd /backups && sha256sum -c zenmoney_*.sha256'
```

4. После перезагрузки хоста контейнер запускается сам благодаря `restart: unless-stopped`, если служба Docker стартует вместе с системой. Проверить состояние можно так:

```bash
docker compose ps
```

Контейнер, остановленный вручную через `docker stop`, после перезагрузки не запустится: так работает `unless-stopped`.
