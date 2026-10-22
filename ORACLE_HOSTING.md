# PumpScanBot на бесплатной Oracle Cloud VM

Инструкция составлена по фактическому интерфейсу Oracle Cloud 2026 года. Она
создает общую Ubuntu-машину, на которой можно держать PumpScanBot, отдельного
бота для шефа и другие небольшие сервисы.

После установки PumpScanBot и ngrok запускаются автоматически, переживают
выход из SSH и перезапуск VM.

## Итоговая конфигурация

```text
VM name:        evgeniy-server-01
Region:         Germany Central (Frankfurt)
Image:          Canonical Ubuntu 24.04
Shape:          VM.Standard.A1.Flex, Always Free-eligible
CPU / RAM:      2 OCPU / 12 GB
Boot volume:    стандартный, примерно 46.6 GB
Network:        public IPv4, наружу открыт только SSH
```

Проекты внутри VM разделяются:

```text
/home/ubuntu/
├── pumpscanbot/
├── boss-bot/
└── other-project/
```

У каждого проекта должны быть собственные `.env`, виртуальное окружение, база
и systemd-служба.

## Бесплатные ресурсы и защита от счета

Главное правило: не переводить учетную запись с `Free Tier` на
`Pay As You Go` и не нажимать `Upgrade account`.

Для этой VM используются:

```text
Ampere A1:     2 OCPU и 12 GB RAM
Boot volume:  около 46.6 GB из общей бесплатной квоты 200 GB
```

Надпись `Always Free-eligible` возле A1.Flex означает, что тип shape может
использоваться бесплатно. Но интерфейс разрешает выставить десятки OCPU за
пробные `$300`. Поэтому вручную оставляем не больше `2 OCPU / 12 GB`.

Oracle может не показать отдельную строку `$0` на Review. Проверяем сочетание:

- план аккаунта `Free Tier`;
- shape `VM.Standard.A1.Flex` с пометкой `Always Free-eligible`;
- не больше `2 OCPU / 12 GB`;
- стандартный boot volume;
- нет дополнительных платных ресурсов.

В `Billing & Cost Management` создай Budget на `$1` с уведомлениями 50% и
100%. Budget только предупреждает и не блокирует расходы. Главная защита - не
выполнять Upgrade.

## Где смотреть лимиты и расходы

В верхнем поиске Oracle Console введи:

```text
Limits, Quotas and Usage
```

На этой странице:

1. выбери регион `Germany Central (Frankfurt)`;
2. для CPU/RAM выбери сервис `Compute` и найди ресурсы Ampere A1;
3. для дисков выбери `Block Volume` или `Block Storage`;
4. поставь compartment `ELitvinov_KGD (root)`.

Фактические расходы проверяются здесь:

```text
Billing & Cost Management -> Cost Analysis
Billing & Cost Management -> Subscription information
```

Тип плана должен оставаться `Free Tier`.

## Что делать с оставшимся диском

Стандартный boot volume занимает около `46.6 GB`. Остальная часть бесплатной
квоты 200 GB никуда не пропадает и не требует действий.

Ее можно позже использовать для:

- отдельного Block Volume под базы и резервные копии;
- расширения системного диска;
- boot volume второй VM.

Для нескольких Python-ботов 46.6 GB достаточно. Не выделяй все 200 GB заранее:
вторая VM потребует минимум около 47 GB собственного boot volume.

После подключения свободное место проверяется командами:

```bash
nproc
free -h
df -h /
lsblk
```

## 1. Создай сеть через VCN Wizard

Сеть удобнее создать до VM. Обычная кнопка `Create VCN` создает только сеть и
требует ручной настройки шлюзов. Используй именно мастер.

1. Открой `Networking` -> `Virtual Cloud Networks`.
2. Нажми `Start VCN Wizard`.
3. Выбери левую карточку `Create VCN with Internet Connectivity`.
4. Заполни:

```text
VCN name:             personal-services-vcn
Compartment:          ELitvinov_KGD (root)
VCN IPv4 CIDR:        10.0.0.0/16
Public subnet CIDR:   10.0.0.0/24
Private subnet CIDR:  10.0.1.0/24
IPv6:                 Off
DNS hostnames:        On
```

5. Нажми `Review and create`, затем `Create`.
6. Дождись статуса VCN `Available` и успешного завершения операций.

Мастер создаст VCN, публичную и приватную подсети, Internet Gateway, NAT
Gateway, Service Gateway и таблицы маршрутов.

## 2. Создай VM

Открой `Compute` -> `Instances` -> `Create instance`.

### Basic information и Placement

```text
Name:                  evgeniy-server-01
Compartment:           ELitvinov_KGD (root)
Capacity type:         On-demand capacity
Fault domain:          Let Oracle choose
```

Ampere A1 можно создавать в любом Availability Domain. Если один AD отвечает
`Out of capacity`, попробуй остальные:

```text
AD-1 -> AD-2 -> AD-3
```

Сеть переделывать при смене AD не нужно. Если свободных A1 нет нигде, повтори
позже или временно попробуй `1 OCPU / 6 GB`. Не нажимай Upgrade.

Бесплатная AMD-машина находится не в обычном разделе AMD, а в
`Specialty and previous generation` под именем `VM.Standard.E2.1.Micro`. У нее
всего 1 GB RAM, поэтому для нескольких ботов выбираем Ampere.

### Image

Нажми `Change image` и выбери:

```text
Canonical Ubuntu 24.04
Price: Free
```

Не выбирай Marketplace-образы. `Ubuntu 24.04 Minimal` тоже работает, но
обычный образ проще для первого сервера.

### Shape

1. Нажми `Change shape`.
2. Выбери `Virtual machine` -> `Ampere`.
3. Выбери только `VM.Standard.A1.Flex` с `Always Free-eligible`.
4. Нажми маленькую стрелку слева от названия shape.
5. Установи:

```text
Number of OCPUs:        2
Amount of memory (GB):  12
Burstable performance:  Off
```

Не выбирай `VM.Standard.A2.Flex`. Возможность поставить 4/24 или больше
появляется из-за пробных `$300`, но такая конфигурация не останется бесплатной.

### Management и Availability

Оставь:

```text
Instance metadata authorization header: Enabled
Initialization script:                  пусто
Live migration:                         Let Oracle choose
Restart after infrastructure maintenance: Enabled
```

Теги необязательны. Настройки Oracle Cloud Agent можно оставить стандартными.

### Security

На сочетании Ubuntu 24.04 и A1.Flex Oracle может не позволить включить
`Shielded instance`. В таком случае оставь выключенными:

```text
Secure Boot
Measured Boot
Trusted Platform Module
Confidential computing
```

Для запуска ботов они не требуются. Защиту обеспечат SSH-ключ, firewall и
права файлов.

### Networking

Выбери уже созданную сеть:

```text
VNIC name:             personal-services-vnic
Primary network:       Select existing virtual cloud network
Virtual cloud network: personal-services-vcn
Subnet:                public subnet-personal-services-vcn
Private IPv4:          Automatically assigned
Public IPv4:           Yes / Automatically assign
IPv6:                  Off
DNS record:            Yes
```

Если Public IPv4 заблокирован с предупреждением `You must select a public
subnet`, значит выбрана не публичная подсеть либо VCN создавалась без мастера.

Порт 8080 в Oracle открывать не нужно: ngrok обращается к нему изнутри VM.
Для первоначальной установки снаружи нужен только SSH-порт 22.

### SSH keys

1. Выбери `Generate a key pair for me`.
2. Нажми `Download private key`.
3. Нажми `Download public key`.

Private key Oracle повторно не показывает. Перемести файлы в:

```text
C:\Users\Evgeniy\.ssh\oracle-personal-services.key
C:\Users\Evgeniy\.ssh\oracle-personal-services.key.pub
```

Публичный файл `.pub` можно показывать. Содержимое `.key` нельзя отправлять
никому и нельзя добавлять в Git.

### Storage

Оставь:

```text
Custom boot volume size:       Off
Default boot volume:           около 46.6 GB
Use in-transit encryption:     Enabled
Customer-managed key:          Off
Additional Block Volumes:      none
```

### Review и создание

Проверь:

```text
Ubuntu 24.04
VM.Standard.A1.Flex, Always Free-eligible
2 OCPU / 12 GB RAM
On-demand capacity
Public IPv4: Yes
Private IPv4: Automatically assigned
Default boot volume, no extra volumes
```

Отдельная цена `$0` может не отображаться. Нажми `Create`. При ошибке
`Out of capacity for shape VM.Standard.A1.Flex` смени Availability Domain и
повтори создание.

Дождись статуса `Running`. На странице VM найди `Public IPv4 address`.

## 3. Подготовь SSH на Windows

Проверь, что private key лежит в `.ssh`, и ограничь его права:

```powershell
icacls "$HOME\.ssh\oracle-personal-services.key" /inheritance:r
icacls "$HOME\.ssh\oracle-personal-services.key" /grant:r "$env:USERNAME`:R"
```

Подключись, подставив IP со страницы VM:

```powershell
ssh -i "$HOME\.ssh\oracle-personal-services.key" ubuntu@ТВОЙ_IP
```

При первом подключении напиши `yes`. Для Ubuntu пользователь называется
`ubuntu`; имя `opc` используется для Oracle Linux.

## 4. Подготовь проект на Windows

Перед переносом закрой локальный PumpScanBot и ngrok. Два экземпляра одного
Telegram-бота будут конфликтовать, а бесплатный ngrok может не разрешить две
одновременные сессии.

Готовый архив уже находится здесь:

```text
C:\Users\Evgeniy\Desktop\Py\Проекты\pumpscanbot\pumpscanbot-server.zip
```

После будущих изменений архив создается из последнего Git-коммита:

```powershell
cd "C:\Users\Evgeniy\Desktop\Py\Проекты\pumpscanbot"
git status
git archive --format=zip --output=pumpscanbot-server.zip HEAD
```

Архив не содержит `.env`, SQLite-базу, `.venv` и SSH-ключи.

## 5. Отправь файлы на сервер

В PowerShell, находясь в каталоге проекта:

```powershell
$KEY = "$HOME\.ssh\oracle-personal-services.key"
$SERVER = "ubuntu@ТВОЙ_IP"

ssh -i $KEY $SERVER "mkdir -p ~/pumpscanbot/data"
scp -i $KEY .\pumpscanbot-server.zip "${SERVER}:~/"
scp -i $KEY .\.env "${SERVER}:~/pumpscanbot/.env"
scp -i $KEY .\data\pumpscanbot.sqlite3 "${SERVER}:~/pumpscanbot/data/pumpscanbot.sqlite3"
```

Если база еще не существует, последнюю команду можно пропустить. Перенос базы
сохраняет кошельки, подписи, лимиты, Telegram-чат и историю обработанных
транзакций.

## 6. Распакуй проект

Подключись по SSH и выполни:

```bash
sudo apt-get update
sudo apt-get install -y unzip
unzip -o ~/pumpscanbot-server.zip -d ~/pumpscanbot
cd ~/pumpscanbot
```

Распаковка не затрагивает `.env` и SQLite-базу, потому что их нет в архиве.

## 7. Получи ngrok authtoken

Открой на своем ПК:

```text
https://dashboard.ngrok.com/get-started/your-authtoken
```

Скопируй значение после `ngrok config add-authtoken`. Это отдельный секрет, не
Telegram token и не Helius API key.

## 8. Установи и запусти PumpScanBot

На сервере:

```bash
cd ~/pumpscanbot
chmod +x deploy/oracle/install.sh deploy/oracle/run_ngrok.sh deploy/oracle/status.sh
./deploy/oracle/install.sh
```

Скрипт:

1. проверит `.env`;
2. ограничит доступ к `.env` и базе;
3. установит Python и ngrok;
4. создаст `.venv` и установит зависимости;
5. создаст две systemd-службы;
6. включит автозапуск;
7. запустит ngrok и бота.

Установщик скрыто попросит ngrok authtoken. При вставке символы не отображаются
- это нормально. Нажми Enter.

Серверный launcher сам получает HTTPS-адрес ngrok, обновляет
`PUBLIC_WEBHOOK_URL` и синхронизирует Helius только при изменении адреса.

## 9. Проверь работу

```bash
cd ~/pumpscanbot
./deploy/oracle/status.sh
sudo journalctl -u pumpscanbot -f
```

Узнать публичный адрес:

```bash
curl -s http://127.0.0.1:4040/api/tunnels
```

Проверить endpoint:

```bash
curl https://ТВОЙ_NGROK_АДРЕС/health
```

Ожидаемый ответ:

```json
{"status":"ok"}
```

В Telegram нажми `Проверить связь`. Используй `Sync Helius` только если
автоматическая синхронизация не прошла: ручной Sync расходует Helius credits.

## Управление после установки

```bash
# Перезапустить PumpScanBot после изменения .env
sudo systemctl restart pumpscanbot

# Перезапустить ngrok, затем бота
sudo systemctl restart pumpscanbot-ngrok
sudo systemctl restart pumpscanbot

# Остановить обе службы
sudo systemctl stop pumpscanbot pumpscanbot-ngrok

# Запустить обе службы
sudo systemctl start pumpscanbot-ngrok pumpscanbot

# Последние 100 строк лога
sudo journalctl -u pumpscanbot -n 100 --no-pager
```

## Обновление кода

На Windows:

```powershell
cd "C:\Users\Evgeniy\Desktop\Py\Проекты\pumpscanbot"
git archive --format=zip --output=pumpscanbot-server.zip HEAD
scp -i "$HOME\.ssh\oracle-personal-services.key" .\pumpscanbot-server.zip ubuntu@ТВОЙ_IP:~/
```

На сервере:

```bash
cd ~/pumpscanbot
unzip -o ~/pumpscanbot-server.zip -d ~/pumpscanbot
./deploy/oracle/install.sh
```

`.env` и база при обновлении сохраняются.

## Резервная копия базы

Скачать актуальную базу на Windows:

```powershell
scp -i "$HOME\.ssh\oracle-personal-services.key" ubuntu@ТВОЙ_IP:~/pumpscanbot/data/pumpscanbot.sqlite3 .\data\pumpscanbot.sqlite3.server-backup
```

## Если что-то не работает

```bash
sudo systemctl status pumpscanbot --no-pager
sudo systemctl status pumpscanbot-ngrok --no-pager
sudo journalctl -u pumpscanbot -n 100 --no-pager
sudo journalctl -u pumpscanbot-ngrok -n 100 --no-pager
```

Частые причины:

- локальный бот или ngrok на ПК все еще запущен;
- неверный ngrok authtoken;
- `.env` не отправлен на сервер;
- в `.env` остались значения `PASTE_...`;
- одновременно запущены два экземпляра одного Telegram-бота;
- бесплатный A1 был освобожден Oracle как длительно неиспользуемый ресурс.

Никому не отправляй `.env`, Oracle private key и ngrok authtoken.
