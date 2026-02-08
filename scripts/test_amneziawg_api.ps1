# PowerShell скрипт для проверки API AmneziaWG

# Настройки (замените на ваши значения)
$ApiUrl = "http://localhost:51821"  # URL AmneziaWG Web UI
$Password = "vtnfvjhajp03"           # Пароль от Web UI

# Имя клиента для теста
$ClientName = "test_client_$((Get-Date).ToFileTime())"

Write-Host "=== Проверка API AmneziaWG ===" -ForegroundColor Green
Write-Host "API URL: $ApiUrl"
Write-Host "Клиент: $ClientName"
Write-Host ""

# 1. Авторизация
Write-Host "1. Авторизация в API..." -ForegroundColor Yellow
try {
    $AuthResponse = Invoke-RestMethod -Uri "$ApiUrl/api/session" -Method Post -ContentType "application/json" -Body (@{password=$Password} | ConvertTo-Json) -SessionVariable Session
    Write-Host "✓ Авторизация успешна" -ForegroundColor Green
} catch {
    Write-Host "✗ Ошибка авторизации: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host ""

# 2. Создание клиента
Write-Host "2. Создание клиента '$ClientName'..." -ForegroundColor Yellow
try {
    $CreateResponse = Invoke-RestMethod -Uri "$ApiUrl/api/wireguard/client" -Method Post -WebSession $Session -ContentType "application/json" -Body (@{name=$ClientName} | ConvertTo-Json)
    Write-Host "✓ Клиент создан успешно" -ForegroundColor Green
    $ClientId = $CreateResponse.id
    Write-Host "ID клиента: $ClientId" -ForegroundColor Cyan
} catch {
    Write-Host "✗ Ошибка создания клиента: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host ""

# 3. Получение списка клиентов
Write-Host "3. Получение списка клиентов..." -ForegroundColor Yellow
try {
    $ClientsList = Invoke-RestMethod -Uri "$ApiUrl/api/wireguard/client" -Method Get -WebSession $Session
    Write-Host "✓ Список клиентов получен" -ForegroundColor Green
    Write-Host "Количество клиентов: $($ClientsList.Count)"
    $ClientsList | Format-Table -Property name, id, address, enabled -AutoSize
} catch {
    Write-Host "✗ Ошибка получения списка клиентов: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""

# 4. Получение конфигурации клиента
Write-Host "4. Получение конфигурации клиента..." -ForegroundColor Yellow
try {
    $ConfigResponse = Invoke-RestMethod -Uri "$ApiUrl/api/wireguard/client/$ClientId/configuration" -Method Get -WebSession $Session -ContentType "text/plain"
    Write-Host "✓ Конфигурация получена" -ForegroundColor Green
    Write-Host "Пример содержимого (первые 10 строк):" -ForegroundColor Cyan
    $ConfigResponse.Split("`n")[0..9] | ForEach-Object { Write-Host $_ }
} catch {
    Write-Host "✗ Ошибка получения конфигурации: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host ""

# 5. Удаление клиента
Write-Host "5. Удаление клиента '$ClientName'..." -ForegroundColor Yellow
try {
    Invoke-RestMethod -Uri "$ApiUrl/api/wireguard/client/$ClientId" -Method Delete -WebSession $Session
    Write-Host "✓ Клиент удален успешно" -ForegroundColor Green
} catch {
    Write-Host "✗ Ошибка удаления клиента: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host ""

# 6. Получение списка клиентов после удаления
Write-Host "6. Получение списка клиентов после удаления..." -ForegroundColor Yellow
try {
    $ClientsListAfter = Invoke-RestMethod -Uri "$ApiUrl/api/wireguard/client" -Method Get -WebSession $Session
    Write-Host "✓ Список клиентов после удаления получен" -ForegroundColor Green
    Write-Host "Количество клиентов: $($ClientsListAfter.Count)"
    $ClientsListAfter | Format-Table -Property name, id, address, enabled -AutoSize
} catch {
    Write-Host "✗ Ошибка получения списка клиентов: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== Тестирование завершено ===" -ForegroundColor Green