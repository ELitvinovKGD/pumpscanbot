# START HERE: запуск PumpScanBot на твоем ПК

## Обычный запуск после настройки

Дважды щелкни по файлу `ЗАПУСТИТЬ БОТА.bat` в папке проекта.

Он сам:

1. запустит или найдет ngrok;
2. получит актуальную HTTPS-ссылку;
3. обновит `PUBLIC_WEBHOOK_URL` в `.env`, если ссылка поменялась;
4. запустит бота;
5. синхронизирует Helius только после смены ссылки.

Окно запуска нужно оставить открытым. Его можно свернуть. Для остановки бота
закрой окно или нажми `Ctrl+C`.

В Telegram кнопка `Проверить связь` проверяет ngrok и состояние синхронизации.
Эта проверка не обращается к Helius API и не расходует Helius credits.

Если Windows не может найти ngrok, положи `ngrok.exe` в папку `tools` внутри
проекта. Полный путь получится `pumpscanbot\tools\ngrok.exe`.

Это короткая инструкция без лишней теории. Делай по порядку.

## 0. Что тебе понадобится

Нужно получить только 3 обязательные вещи:

```text
1. TELEGRAM_BOT_TOKEN
2. HELIUS_API_KEY
3. PUBLIC_WEBHOOK_URL
```

`HELIUS_PROJECT_ID` нужен только для кнопки `Лимит Helius`. Если сразу не нашел, можно временно оставить плейсхолдер.

## 1. Подготовь проект, но еще не запускай бота

На этом шаге бот еще не стартует. Скрипт `setup.ps1` только создает виртуальное окружение `.venv`, ставит библиотеки и, если файла `.env` нет, создает его из шаблона.

Токены можно вставить после этого шага.

Открой PowerShell:

```powershell
cd "C:\Users\Evgeniy\Desktop\Py\Проекты\pumpscanbot"
.\setup.ps1
```

Если PowerShell ругается на запуск `.ps1`, выполни один раз:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Потом снова выполни только установку:

```powershell
.\setup.ps1
```

## 2. Заполни .env

Открой файл:

```text
C:\Users\Evgeniy\Desktop\Py\Проекты\pumpscanbot\.env
```

Там будут строки с `PASTE_...`.

### Telegram token

Ты уже нашел токен у `@BotFather`.

Замени:

```env
TELEGRAM_BOT_TOKEN=PASTE_TELEGRAM_BOT_TOKEN_HERE
```

на:

```env
TELEGRAM_BOT_TOKEN=1234567890:AA....
```

### Helius API key

Открой:

```text
https://dashboard.helius.dev/
```

Зайди в свой проект и найди API key. Вставь его:

```env
HELIUS_API_KEY=твой_helius_api_key
```

### Helius Project ID

Project ID нужен для команды `/limit`, кнопки `Лимит Helius` и авто-предупреждения, если credits почти закончились.

Где искать:

```text
Helius Dashboard -> твой Project -> Settings / Project settings
```

Если не нашел сразу, оставь так:

```env
HELIUS_PROJECT_ID=PASTE_HELIUS_PROJECT_ID_HERE
```

Бот будет работать, просто `/limit` покажет ошибку до заполнения Project ID.

### Авто-предупреждение по Helius credits

По умолчанию бот раз в 6 часов проверяет Helius usage и присылает предупреждение в Telegram, если осталось 10% credits или меньше:

```env
HELIUS_USAGE_WARN_PERCENT=10
HELIUS_USAGE_CHECK_INTERVAL_MINUTES=360
```

Если не хочешь авто-проверку, поставь:

```env
HELIUS_USAGE_CHECK_INTERVAL_MINUTES=0
```

### Helius auth header

Это секретная строка, чтобы чужой человек не мог дергать твой webhook.

Просто впиши любую длинную фразу без кавычек:

```env
HELIUS_AUTH_HEADER=my-secret-pumpscanbot-123456
```

## 3. Сделай публичный URL для локального ПК

Бот запускается на твоем ПК по адресу:

```text
http://127.0.0.1:8080
```

Но Helius не может отправить событие на `127.0.0.1`, потому что это адрес внутри твоего компьютера. Нужен публичный HTTPS URL.

Самый простой путь - ngrok.

В отдельном PowerShell окне:

```powershell
ngrok http 8080
```

Ngrok покажет строку `Forwarding`, примерно такую:

```text
https://abc123.ngrok-free.app -> http://localhost:8080
```

Скопируй только HTTPS часть:

```text
https://abc123.ngrok-free.app
```

и вставь в `.env`:

```env
PUBLIC_WEBHOOK_URL=https://abc123.ngrok-free.app
```

Важно: без `/helius/webhook` на конце. Бот сам добавит этот путь.

## 4. Запусти бота

Этот шаг делай только после того, как в `.env` уже вставлены Telegram token, Helius API key и `PUBLIC_WEBHOOK_URL`.

Открой новое PowerShell окно:

```powershell
cd "C:\Users\Evgeniy\Desktop\Py\Проекты\pumpscanbot"
.\run.ps1
```

Если все хорошо, окно останется открытым и будет писать логи.

## 5. Открой Telegram

Напиши своему боту:

```text
/start
```

После `/start` внизу Telegram появится постоянная клавиатура:

```text
Меню | Кошельки
Добавить кошелек | Глобальный минимум
Лимит Helius | Статус
Sync Helius | Тест уведомления
```

Если главное меню уехало вверх по чату, просто нажми нижнюю кнопку `Меню`.

Кнопка `Тест уведомления` имитирует покупку и проверяет только доставку в Telegram. Helius credits она не тратит.

Потом:

```text
/whoami
```

Если хочешь закрыть управление ботом только для себя, скопируй свой `user id` из ответа и вставь в `.env`:

```env
TELEGRAM_ADMIN_USER_IDS=123456789
```

После изменения `.env` перезапусти `.\run.ps1`.

## 6. Добавь кошелек

Нажми кнопку:

```text
Добавить кошелек
```

Отправь:

```text
АДРЕС_КОШЕЛЬКА Красный
```

Пример:

```text
7xKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx Красный
```

После добавления бот попробует создать или обновить Helius webhook.

## 7. Проверь, что webhook живой

Открой в браузере:

```text
https://abc123.ngrok-free.app/health
```

Должно быть:

```json
{"status":"ok"}
```

Если ngrok-ссылка поменялась:

```text
1. поменяй PUBLIC_WEBHOOK_URL в .env
2. перезапусти .\run.ps1
3. в Telegram отправь /sync
```

## Частые проблемы

### Бот не стартует

Проверь, что в `.env` заменен:

```env
TELEGRAM_BOT_TOKEN=...
```

### Helius sync не удался

Проверь:

```env
HELIUS_API_KEY=...
PUBLIC_WEBHOOK_URL=https://...
HELIUS_AUTH_HEADER=...
```

И проверь, что ngrok все еще запущен.

### /limit не работает

Это не ломает бота. Значит не заполнен или неверно заполнен:

```env
HELIUS_PROJECT_ID=...
```

### Уведомления не приходят

Проверь:

```text
1. ngrok запущен
2. /health открывается по публичному URL
3. кошелек включен в меню
4. минимум SOL не слишком высокий
5. Helius webhook успешно синхронизировался после добавления кошелька
```
