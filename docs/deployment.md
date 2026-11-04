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

## Каталог для копий

Копии должны лежать на постоянном диске хоста, а не внутри контейнера. Контейнер работает от UID 10001, поэтому этому пользователю нужно право записи в папку.

Выполните команды на хосте, подставив свой путь:

```bash
sudo mkdir -p /srv/backups/zenmoney
```

```bash
sudo chown 10001:10001 /srv/backups/zenmoney
```

Копии содержат всю финансовую историю, поэтому закройте папку от других пользователей:

```bash
sudo chmod 750 /srv/backups/zenmoney
```

Если не создать папку заранее, Docker создаст её от root, и запись завершится ошибкой `Permission denied`.

После `chown` файлы принадлежат UID 10001. Чтобы открывать копии от своего пользователя, настройте доступ к этой папке отдельно.

## Запуск через Docker Compose

Готовый образ публикуется в GitHub Container Registry, поэтому репозиторий на хосте не нужен. Создайте отдельный каталог и скачайте в него два файла, указав нужную версию:

```bash
curl -fLO https://raw.githubusercontent.com/nzvekov/zenmoney-backup/v1.0.0/deploy/docker-compose.yml
```

```bash
curl -fL -o .env https://raw.githubusercontent.com/nzvekov/zenmoney-backup/v1.0.0/deploy/.env.example
```

Заполните в `.env` значения `API_TOKEN` и `BACKUP_HOST_DIR`. Ограничьте доступ к файлу, в нём лежит секрет:

```bash
chmod 600 .env
```

Описание остальных переменных есть в [README](../README.md#настройки). Запустите контейнер:

```bash
docker compose up -d
```

## Запуск через Portainer

1. Создайте папку для копий, как описано выше.
2. Откройте **Stacks → Add stack**, укажите имя `zenmoney-backup` и выберите **Web editor**.
3. Вставьте содержимое [`deploy/docker-compose.yml`](../deploy/docker-compose.yml).
4. В разделе **Environment variables** добавьте `API_TOKEN`, `BACKUP_HOST_DIR` и `IMAGE_TAG`, например `1.0.0`. Остальные переменные нужны, только если значения по умолчанию не подходят.
5. Нажмите **Deploy the stack**.

Логи находятся в **Containers → zenmoney-backup → Logs**. Этот способ не проверялся.

## Проверка

1. `docker compose logs --tail=100` показывает `Scheduled daily backup at 03:00`.
2. При пустой папке копия создаётся сразу после старта: в логах есть `Backup saved`.
3. В папке для копий лежат `zenmoney_<дата>_<время>.json` и `.json.sha256`.
4. Контрольная сумма сходится. `sudo` нужен, потому что папка закрыта для других пользователей:

```bash
sudo sh -c 'cd /srv/backups/zenmoney && sha256sum -c zenmoney_*.sha256'
```

5. После перезагрузки хоста контейнер запускается сам благодаря `restart: unless-stopped`, если служба Docker стартует вместе с системой. Проверить состояние можно так:

```bash
docker compose ps
```

Контейнер, остановленный вручную через `docker stop`, после перезагрузки не запустится: так работает `unless-stopped`.
