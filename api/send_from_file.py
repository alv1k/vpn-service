#!/usr/bin/env python3
"""
Отправка письма с HTML из файла
"""

import sys
import os
from pathlib import Path

# Добавляем путь к проекту
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from api.notifications import _send_html_email, _branded_html

def send_email_from_files(to_email, subject, html_file, text_file=None, use_branding=True):
    """
    Отправляет письмо, загружая HTML из файла
    
    Args:
        to_email: Email получателя
        subject: Тема письма
        html_file: Путь к файлу с HTML-содержимым
        text_file: Путь к файлу с текстовой версией (опционально)
        use_branding: Использовать брендированный шаблон
    """
    
    # Читаем HTML из файла
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            html_body = f.read()
        print(f"✅ HTML загружен из {html_file} ({len(html_body)} символов)")
    except Exception as e:
        print(f"❌ Ошибка чтения HTML файла: {e}")
        return False
    
    # Читаем текстовую версию или создаем базовую
    if text_file and os.path.exists(text_file):
        try:
            with open(text_file, 'r', encoding='utf-8') as f:
                text_content = f.read()
            print(f"✅ Текст загружен из {text_file}")
        except Exception as e:
            print(f"⚠️ Ошибка чтения текстового файла: {e}")
            text_content = "Для просмотра этого письма используйте почтовый клиент с поддержкой HTML"
    else:
        text_content = "Для просмотра этого письма используйте почтовый клиент с поддержкой HTML"
        print("ℹ️ Текстовая версия не указана, используется стандартная")
    
    # Оборачиваем в брендированный шаблон при необходимости
    if use_branding:
        html_content = _branded_html(html_body)
        print("✅ Применено брендирование TIIN")
    else:
        html_content = html_body
        print("ℹ️ Брендирование не применялось")
    
    # Отправляем письмо
    print(f"\n📧 Отправка письма на {to_email}")
    print(f"📝 Тема: {subject}")
    
    success = _send_html_email(to_email, subject, text_content, html_content)
    
    if success:
        print(f"\n✅ Письмо успешно отправлено на {to_email}")
    else:
        print(f"\n❌ Ошибка при отправке письма на {to_email}")
    
    return success

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Отправка email с HTML из файла')
    parser.add_argument('email', help='Email получателя')
    parser.add_argument('subject', help='Тема письма')
    parser.add_argument('--html', '-H', default='email_template.html', help='HTML файл с содержимым')
    parser.add_argument('--text', '-T', help='Текстовый файл (plain text)')
    parser.add_argument('--no-branding', action='store_true', help='Не использовать брендирование TIIN')
    
    args = parser.parse_args()
    
    send_email_from_files(
        to_email=args.email,
        subject=args.subject,
        html_file=args.html,
        text_file=args.text,
        use_branding=not args.no_branding
    )
