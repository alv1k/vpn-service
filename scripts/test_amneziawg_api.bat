@echo off
setlocal enabledelayedexpansion

REM Скрипт для проверки API AmneziaWG
REM Проверяет создание клиента через API

echo === Тестирование API AmneziaWG ===

REM Загрузка переменных из .env файла
for /f "tokens=*" %%i in ('type "..\docker-config\.env" 2^>nul ^| findstr "="') do set %%i

REM Настройки по умолчанию
if "%AMNEZIA_WG_API_URL%"=="" set AMNEZIA_WG_API_URL=http://localhost:51821
if "%WG_UI_PASSWORD%"=="" set WG_UI_PASSWORD=vtnfvjhajp03

echo API URL: %AMNEZIA_WG_API_URL%
echo Password: ***%WG_UI_PASSWORD:~-3%

REM Временные файлы
set TEMP_DIR=%TEMP%
set COOKIES_FILE=%TEMP_DIR%\amneziawg_cookies.txt
set RESPONSE_FILE=%TEMP_DIR%\amneziawg_response.txt
set SESSION_FILE=%TEMP_DIR%\session.txt

echo.
echo 1. Авторизация в API...

REM Авторизация
curl -s -X POST ^
  -H "Content-Type: application/json" ^
  -d "{\"password\":\"%WG_UI_PASSWORD%\"}" ^
  -c "%COOKIES_FILE%" ^
  "%AMNEZIA_WG_API_URL%/api/session" ^
  -w "HTTP Status: %%{http_code}" ^
  -o "%RESPONSE_FILE%"

REM Проверка статуса авторизации
for /f %%i in ('curl -s -w "%%{http_code}" -o nul -X POST ^
  -H "Content-Type: application/json" ^
  -d "{\\"password\\":\\"%WG_UI_PASSWORD%\\"}" ^
  -c "%COOKIES_FILE%" ^
  "%AMNEZIA_WG_API_URL%/api/session"') do set AUTH_STATUS=%%i

if "!AUTH_STATUS!"=="200" (
    echo ✓ Авторизация успешна
) else (
    echo ✗ Ошибка авторизации (HTTP !AUTH_STATUS!)
    type "%RESPONSE_FILE%"
    goto cleanup
)

echo.
echo 2. Получение списка клиентов до создания нового...

REM Получение списка клиентов
curl -s -X GET ^
  -b "%COOKIES_FILE%" ^
  "%AMNEZIA_WG_API_URL%/api/wireguard/client" ^
  -o "%RESPONSE_FILE%"

REM Подсчет количества клиентов
for /f %%i in ('findstr /R "\"id\"" "%RESPONSE_FILE%" ^| find /c /v ""') do set CLIENT_COUNT_BEFORE=%%i

echo ✓ Список клиентов получен
echo Количество клиентов до создания нового: !CLIENT_COUNT_BEFORE!

echo.
echo 3. Создание нового клиента...

REM Генерация уникального имени для тестового клиента
set CLIENT_NAME=test_client_%date:~-4%%date:~4,2%%date:~7,2%_%time:~0,2%%time:~3,2%%time:~6,2%

REM Заменяем пробелы на 0 в имени клиента
set CLIENT_NAME=!CLIENT_NAME: =0!

REM Создание клиента
curl -s -X POST ^
  -H "Content-Type: application/json" ^
  -b "%COOKIES_FILE%" ^
  -d "{\"name\":\"!CLIENT_NAME!\"}" ^
  "%AMNEZIA_WG_API_URL%/api/wireguard/client" ^
  -w "HTTP Status: %%{http_code}" ^
  -o "%RESPONSE_FILE%"

REM Проверка статуса создания
for /f %%i in ('curl -s -w "%%{http_code}" -o nul -X POST ^
  -H "Content-Type: application/json" ^
  -b "%COOKIES_FILE%" ^
  -d "{\\"name\\":\\"!CLIENT_NAME!\\"}" ^
  "%AMNEZIA_WG_API_URL%/api/wireguard/client"') do set CREATE_STATUS=%%i

if "!CREATE_STATUS!"=="200" (
    echo ✓ Клиент '!CLIENT_NAME!' успешно создан
    
    REM Извлечение ID клиента из ответа
    for /f "tokens=4 delims=:," %%a in ('findstr "id" "%RESPONSE_FILE%"') do (
        set CLIENT_ID=%%a
        set CLIENT_ID=!CLIENT_ID:"=!
        set CLIENT_ID=!CLIENT_ID: =!
        goto :found_id
    )
    :found_id
    echo ID клиента: !CLIENT_ID!
) else (
    echo ✗ Ошибка создания клиента (HTTP !CREATE_STATUS!)
    type "%RESPONSE_FILE%"
    goto cleanup
)

echo.
echo 4. Получение конфигурации клиента...

REM Получение конфигурации клиента
curl -s -X GET ^
  -b "%COOKIES_FILE%" ^
  "%AMNEZIA_WG_API_URL%/api/wireguard/client/!CLIENT_ID!/configuration" ^
  -w "HTTP Status: %%{http_code}" ^
  -o "%RESPONSE_FILE%"

REM Проверка статуса получения конфигурации
for /f %%i in ('curl -s -w "%%{http_code}" -o nul -X GET ^
  -b "%COOKIES_FILE%" ^
  "%AMNEZIA_WG_API_URL%/api/wireguard/client/!CLIENT_ID!/configuration"') do set CONFIG_STATUS=%%i

if "!CONFIG_STATUS!"=="200" (
    echo ✓ Конфигурация клиента получена
    echo Пример содержимого конфига (первые 10 строк):
    for /f "skip=0 tokens=*" %%a in ('type "%RESPONSE_FILE%" ^| findstr /n "^" ^| findstr "^1:|^2:|^3:|^4:|^5:|^6:|^7:|^8:|^9:|^10:"') do echo %%a
) else (
    echo ✗ Ошибка получения конфигурации (HTTP !CONFIG_STATUS!)
    type "%RESPONSE_FILE%"
    goto cleanup
)

echo.
echo 5. Получение списка клиентов после создания...

REM Получение списка клиентов после создания
curl -s -X GET ^
  -b "%COOKIES_FILE%" ^
  "%AMNEZIA_WG_API_URL%/api/wireguard/client" ^
  -o "%RESPONSE_FILE%"

REM Подсчет количества клиентов после создания
for /f %%i in ('findstr /R "\"id\"" "%RESPONSE_FILE%" ^| find /c /v ""') do set CLIENT_COUNT_AFTER=%%i

echo ✓ Список клиентов после создания нового получен
echo Количество клиентов после создания нового: !CLIENT_COUNT_AFTER!

echo.
echo 6. Удаление тестового клиента...

REM Удаление тестового клиента
curl -s -X DELETE ^
  -b "%COOKIES_FILE%" ^
  "%AMNEZIA_WG_API_URL%/api/wireguard/client/!CLIENT_ID!" ^
  -w "HTTP Status: %%{http_code}" ^
  -o "%RESPONSE_FILE%"

REM Проверка статуса удаления
for /f %%i in ('curl -s -w "%%{http_code}" -o nul -X DELETE ^
  -b "%COOKIES_FILE%" ^
  "%AMNEZIA_WG_API_URL%/api/wireguard/client/!CLIENT_ID!"') do set DELETE_STATUS=%%i

if "!DELETE_STATUS!"=="204" (
    echo ✓ Клиент '!CLIENT_NAME!' успешно удален
) else (
    echo ✗ Ошибка удаления клиента (HTTP !DELETE_STATUS!)
    type "%RESPONSE_FILE%"
    goto cleanup
)

echo.
echo === Тестирование завершено успешно ===
echo - Авторизация: ✓
echo - Создание клиента: ✓
echo - Получение конфигурации: ✓
echo - Удаление клиента: ✓
echo.
echo Клиент был успешно создан и удален через API AmneziaWG

:cleanup
REM Удаление временных файлов
if exist "%COOKIES_FILE%" del "%COOKIES_FILE%"
if exist "%RESPONSE_FILE%" del "%RESPONSE_FILE%"
if exist "%SESSION_FILE%" del "%SESSION_FILE%"

pause