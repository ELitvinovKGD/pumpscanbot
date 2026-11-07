# PumpScanBot: Git, сервер и базы данных

Это основная практическая инструкция для уже созданной Oracle VM.

Здесь описан полный рабочий цикл:

```text
правка на Windows -> тест -> commit -> push -> pull на сервере -> restart
```

Инструкция по созданию самой VM, сети и SSH-ключа находится в
[ORACLE_HOSTING.md](ORACLE_HOSTING.md).

## 1. Где что находится

Главное правило этой схемы:

```text
Windows: только код, Git и тесты
Oracle: .env, SQLite, ngrok, systemd и работающий бот
```

На сервере используем такую структуру:

```text
/opt/bots/pumpscanbot/                    код из GitHub
/opt/bots/pumpscanbot/.env               production-секреты, не в Git
/var/lib/pumpscanbot/production.sqlite3  production-база, не в Git
/var/lib/pumpscanbot/backups/             резервные копии перед обновлениями
```

На Windows находится только рабочая копия кода:

```text
C:\Users\Evgeniy\Desktop\Py\Проекты\pumpscanbot\
```

На компьютере не создаём рабочий `.env`, не запускаем ngrok и не храним копию
production-базы. Локальная `.venv` нужна только для тестов кода. Обычный
`git push` и `git pull` не переносят секреты и данные: `.env`, `*.sqlite3`,
`*.db`, `.venv` и логи исключены через `.gitignore`.

Для будущего тестового экземпляра используй отдельный файл:

```text
/var/lib/pumpscanbot/test.sqlite3
```

Одного отдельного файла БД недостаточно для одновременного запуска двух ботов.
Тестовому экземпляру также нужны отдельные `TELEGRAM_BOT_TOKEN`, webhook,
`HELIUS_WEBHOOK_ID`, порт и systemd-служба. Один Telegram-токен нельзя
одновременно использовать в production и test.

## 2. Один раз подготовь Git-доступ сервера

Эти команды нужны только на новом сервере. На текущем сервере deploy key уже
создан и подключён к GitHub.

На сервере:

```bash
sudo mkdir -p /opt/bots
sudo chown ubuntu:ubuntu /opt/bots

GIT_SSH_COMMAND='ssh -i /home/ubuntu/.ssh/github_pumpscanbot -o IdentitiesOnly=yes' \
  git clone git@github.com:ELitvinovKGD/pumpscanbot.git /opt/bots/pumpscanbot

git -C /opt/bots/pumpscanbot config core.sshCommand \
  'ssh -i /home/ubuntu/.ssh/github_pumpscanbot -o IdentitiesOnly=yes'
```

Проверка:

```bash
git -C /opt/bots/pumpscanbot remote -v
git -C /opt/bots/pumpscanbot status
```

Серверу достаточно read-only deploy key: сервер только скачивает код. Коммиты
и push выполняются с Windows.

## 3. Первый запуск: всё создаётся на сервере

После клонирования выполни на сервере:

```bash
cd /opt/bots/pumpscanbot
chmod +x deploy/oracle/*.sh
bash deploy/oracle/bootstrap.sh
```

Первый запуск bootstrap:

1. создаст `/var/lib/pumpscanbot` и каталог резервных копий;
2. создаст серверный `/opt/bots/pumpscanbot/.env`;
3. пропишет в нём production-путь базы;
4. остановится, чтобы ты заполнил секреты.

Открой созданный файл прямо на сервере:

```bash
nano /opt/bots/pumpscanbot/.env
```

Минимально заполни:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_USER_IDS=...
HELIUS_API_KEY=...
HELIUS_PROJECT_ID=...
HELIUS_WEBHOOK_ID=...
HELIUS_AUTH_HEADER=...
DATABASE_PATH=/var/lib/pumpscanbot/production.sqlite3
HOST=127.0.0.1
PORT=8080
```

Сохрани через `Ctrl+O`, `Enter`, выйди через `Ctrl+X`. Затем снова запусти:

```bash
cd /opt/bots/pumpscanbot
bash deploy/oracle/bootstrap.sh
```

Второй запуск установит зависимости, при необходимости попросит ngrok
authtoken, создаст systemd-службы и запустит бот. Production SQLite будет
создан приложением на сервере при первом старте.

Не отправляй `.env`, Telegram token, Helius key, ngrok authtoken и SSH private
key в чат и не добавляй их в Git.

## 4. Перенос уже существующей серверной базы

Этот раздел нужен только для уже работающего сервера, где база пока лежит
внутри `/opt/bots/pumpscanbot/data`.

Сначала создай каталоги:

```bash
sudo install -d -m 700 -o ubuntu -g ubuntu /var/lib/pumpscanbot
sudo install -d -m 700 -o ubuntu -g ubuntu /var/lib/pumpscanbot/backups
```

Если данные сейчас находятся в
`/opt/bots/pumpscanbot/data/pumpscanbot.sqlite3`, перенеси их только при
остановленном боте:

```bash
sudo systemctl stop pumpscanbot

cp /opt/bots/pumpscanbot/data/pumpscanbot.sqlite3 \
  /var/lib/pumpscanbot/production.sqlite3

chmod 600 /var/lib/pumpscanbot/production.sqlite3
nano /opt/bots/pumpscanbot/.env
sudo systemctl start pumpscanbot
```

В `nano` измени только `DATABASE_PATH`, сохрани через `Ctrl+O`, `Enter`, затем
выйди через `Ctrl+X`.

Проверь, что новая база используется:

```bash
grep '^DATABASE_PATH=' /opt/bots/pumpscanbot/.env
ls -lh /var/lib/pumpscanbot/production.sqlite3
sudo systemctl status pumpscanbot --no-pager
```

Старый файл удаляй только после проверки кошельков и настроек в Telegram.

После нового запуска проверь:

```bash
./deploy/oracle/status.sh
curl -s http://127.0.0.1:8080/health
sudo journalctl -u pumpscanbot -n 100 --no-pager
```

Ожидаемый ответ health-check:

```json
{"status":"ok"}
```

## 5. Обычная разработка на Windows без запуска бота

Открой PowerShell:

```powershell
cd "C:\Users\Evgeniy\Desktop\Py\Проекты\pumpscanbot"
git status
```

Один раз создай окружение только для кода и тестов:

```powershell
.\setup.ps1 -CodeOnly
```

Эта команда не создаёт `.env` и не запускает бот. Затем запускай тесты:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Посмотри изменения, создай коммит и отправь его в GitHub:

```powershell
git diff
git add README.md ORACLE_HOSTING.md SERVER_RUNBOOK.md setup.ps1 requirements-dev.txt
git add deploy/oracle/install.sh
git add --chmod=+x deploy/oracle/bootstrap.sh deploy/oracle/update.sh
git commit -m "Add server deployment runbook"
git push origin main
```

Для следующих изменений перечисляй в `git add` именно изменённые файлы либо,
после проверки `git status`, используй `git add .`.

Команды `git push` недостаточно для обновления сервера. Push только обновляет
GitHub; после него серверу нужно отдельно выполнить pull и restart.

## 6. Обновление production после push

Подключись к серверу из PowerShell:

```powershell
ssh -i "$HOME\.ssh\oracle-personal-services.key" ubuntu@92.5.150.183
```

На сервере:

```bash
cd /opt/bots/pumpscanbot
bash deploy/oracle/update.sh
```

### Первый переход со старой установки

На текущем сервере `update.sh` ещё отсутствует до первого pull. После первого
push этой инструкции один раз выполни:

```bash
cd /opt/bots/pumpscanbot
git pull --ff-only origin main
```

Затем перенеси существующую базу по разделу 4 и запусти:

```bash
bash deploy/oracle/bootstrap.sh
```

После этого все следующие обновления выполняются обычной командой
`bash deploy/oracle/update.sh`.

Скрипт обновления:

1. проверяет, что в отслеживаемых Git-файлах нет ручных изменений;
2. останавливает `pumpscanbot`, чтобы получить целую копию SQLite;
3. копирует production-базу и `.env` в каталог `backups`;
4. выполняет `git pull --ff-only origin main`;
5. обновляет Python-зависимости;
6. запускает сервис и показывает его статус.

Если обновлялись сами systemd-шаблоны или установщик, один раз выполни:

```bash
cd /opt/bots/pumpscanbot
./deploy/oracle/install.sh
```

## 7. Быстрые команды сервера

```bash
# Состояние
sudo systemctl status pumpscanbot --no-pager
sudo systemctl status pumpscanbot-ngrok --no-pager

# Логи в реальном времени
sudo journalctl -u pumpscanbot -f

# Последние 100 строк
sudo journalctl -u pumpscanbot -n 100 --no-pager

# Перезапуск после ручного изменения .env
sudo systemctl restart pumpscanbot

# Проверка текущего коммита
git -C /opt/bots/pumpscanbot log -1 --oneline

# Проверка базы и резервных копий
ls -lh /var/lib/pumpscanbot/production.sqlite3
ls -lht /var/lib/pumpscanbot/backups
```

## 8. Резервные копии остаются на сервере

`update.sh` автоматически сохраняет базу и `.env` перед каждым обновлением:

```bash
ls -lht /var/lib/pumpscanbot/backups
```

Компьютер остаётся рабочим местом только для кода. При необходимости внешний
backup лучше складывать в отдельное защищённое хранилище, не внутрь Git-проекта.

## 9. Короткая памятка

Локально:

```powershell
git status
.\.venv\Scripts\python.exe -m pytest -q
git add .
git commit -m "Кратко описать изменение"
git push origin main
```

На сервере:

```bash
cd /opt/bots/pumpscanbot
bash deploy/oracle/update.sh
sudo journalctl -u pumpscanbot -n 50 --no-pager
```

Production-база остаётся в `/var/lib/pumpscanbot`, поэтому обновление кода через
Git её не заменяет.
