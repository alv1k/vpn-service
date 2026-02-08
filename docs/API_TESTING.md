# Тестирование API AmneziaWG

Данные скрипты позволяют проверить работу API AmneziaWG для создания и управления VPN-клиентами.

## Скрипты

1. **test_amneziawg_api.ps1** - PowerShell скрипт для Windows
2. **amneziawg_curl_commands.sh** - Bash скрипт с curl командами
3. **test_amneziawg_api.bat** - Batch скрипт для Windows (упрощенная версия)

## Использование

### PowerShell (Windows)
```powershell
# Убедитесь, что AmneziaWG Web UI запущен
# Запустите скрипт
.\test_amneziawg_api.ps1
```

### Bash (Linux/Mac)
```bash
# Сделайте скрипт исполняемым
chmod +x amneziawg_curl_commands.sh

# Запустите скрипт
./amneziawg_curl_commands.sh
```

### Batch (Windows)
```batch
# Запустите скрипт
test_amneziawg_api.bat
```

## Что проверяет скрипт

1. **Авторизация** - проверяет возможность входа в API с использованием пароля
2. **Создание клиента** - создает нового VPN-клиента с уникальным именем
3. **Получение списка клиентов** - получает список всех клиентов до и после создания нового
4. **Получение конфигурации** - получает конфигурационный файл для созданного клиента
5. **Удаление клиента** - удаляет тестового клиента после тестирования

## Настройка

Перед запуском скриптов убедитесь, что:

1. AmneziaWG Web UI запущен и доступен по адресу `http://localhost:51821` (или другому указанному адресу)
2. Пароль от Web UI указан правильно (по умолчанию `vtnfvjhajp03`)
3. Порт 51821 открыт и доступен

## Основные curl команды

Если вы хотите выполнить команды вручную, вот основные:

### Авторизация
```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{"password":"YOUR_PASSWORD"}' \
  -c cookies.txt \
  http://localhost:51821/api/session
```

### Создание клиента
```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{"name":"client_name"}' \
  http://localhost:51821/api/wireguard/client
```

### Получение конфигурации клиента
```bash
curl -X GET \
  -b cookies.txt \
  http://localhost:51821/api/wireguard/client/CLIENT_ID/configuration
```

### Удаление клиента
```bash
curl -X DELETE \
  -b cookies.txt \
  http://localhost:51821/api/wireguard/client/CLIENT_ID
```

## Возможные ошибки

- `401 Unauthorized` - неверный пароль
- `500 Internal Server Error` - сервер не может обработать запрос (возможно, достигнут лимит клиентов)
- `404 Not Found` - неверный URL или эндпоинт