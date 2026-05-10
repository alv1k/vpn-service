#!/usr/bin/env python3
"""
Интерактивный скрипт для отправки сообщений пользователям.
"""

import sys
import logging
from pathlib import Path

# Добавляем корневую директорию проекта в PATH (vpn-service)
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Импортируем из api.notifications (внимание: notifications, не notification_service)
from api.notifications import _send_html_email, _branded_html

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def get_input(prompt: str, required: bool = True) -> str:
    """Получает ввод от пользователя с проверкой."""
    while True:
        value = input(prompt).strip()
        if value or not required:
            return value
        print("❌ Это поле обязательно!")


def main():
    print("\n" + "="*50)
    print("📧 Отправка сообщения пользователю")
    print("="*50 + "\n")
    
    # Получаем email получателя
    to = get_input("Email получателя: ")
    
    # Тема письма
    subject = get_input("Тема письма: ")
    
    # Выбор типа сообщения
    print("\nТип сообщения:")
    print("  1. Обычный текст")
    print("  2. HTML-форматирование")
    print("  3. Чтение из файла")
    
    choice = get_input("\nВаш выбор (1-3): ", required=True)
    
    message = ""
    is_html = False
    
    if choice == "1":
        print("\nВведите текст сообщения (для завершения введите пустую строку):")
        lines = []
        while True:
            line = input()
            if line == "" and len(lines) > 0:
                break
            lines.append(line)
        message = "\n".join(lines)
        is_html = False
        
    elif choice == "2":
        print("\nВведите HTML-код (для завершения введите пустую строку):")
        lines = []
        while True:
            line = input()
            if line == "" and len(lines) > 0:
                break
            lines.append(line)
        message = "\n".join(lines)
        is_html = True
        
    elif choice == "3":
        filepath = get_input("Путь к файлу: ")
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                message = f.read()
            print(f"✅ Прочитано {len(message)} символов из {filepath}")
        except Exception as e:
            print(f"❌ Ошибка чтения файла: {e}")
            sys.exit(1)
        
        # Автоопределение HTML по расширению или содержимому
        is_html = filepath.endswith(('.html', '.htm')) or ('<html' in message.lower())
    
    # Использовать брендирование?
    branding_choice = input("\nИспользовать брендированный шаблон TIIN? (y/n, по умолчанию y): ").lower()
    use_branding = branding_choice != 'n'
    
    # Предпросмотр
    print("\n" + "="*50)
    print("📋 Предпросмотр письма:")
    print("="*50)
    print(f"Кому: {to}")
    print(f"Тема: {subject}")
    print(f"Формат: {'HTML' if is_html else 'Текст'}")
    print(f"Брендирование: {'Да' if use_branding else 'Нет'}")
    print("\nСодержание:")
    print("-"*50)
    print(message[:500] + ("..." if len(message) > 500 else ""))
    print("-"*50)
    
    confirm = input("\nОтправить письмо? (y/n): ").lower()
    if confirm != 'y':
        print("❌ Отправка отменена")
        sys.exit(0)
    
    # Формируем письмо
    if is_html:
        if use_branding:
            html_content = _branded_html(message)
        else:
            html_content = message
        text_content = "Для просмотра этого письма используйте почтовый клиент с поддержкой HTML"
    else:
        text_content = message
        if use_branding:
            # Экранируем спецсимволы для HTML
            escaped_message = (message
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('\n', '<br/>'))
            body = f"""\
    <div style="background: #161616; border: 1px solid #262626; border-radius: 12px; padding: 1.5rem;">
      <p style="color: #e5e5e5; font-size: 1rem; line-height: 1.6; margin: 0; white-space: pre-wrap;">
        {escaped_message}
      </p>
    </div>"""
            html_content = _branded_html(body)
        else:
            html_content = f"""\
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px;">
  <div style="max-width: 600px; margin: 0 auto;">
    <pre style="white-space: pre-wrap; font-family: inherit;">{message}</pre>
  </div>
</body>
</html>"""
    
    # Отправляем
    success = _send_html_email(to, subject, text_content, html_content)
    
    if success:
        print(f"\n✅ Письмо успешно отправлено на {to}")
    else:
        print(f"\n❌ Ошибка при отправке письма на {to}")
        print("Проверьте:")
        print("  - SMTP настройки в config.py")
        print("  - Подключение к интернету")
        print("  - Правильность email адреса")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Операция прервана пользователем")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)