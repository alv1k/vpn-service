try:
        logger.info(f"💰 Processing successful payment: {payment_id}")
        
        tg_id = payment_data["tg_id"]
        tariff_key = payment_data["tariff"]
        
        # ===== 1. Активация подписки =====
        activate_subscription(payment_id)
        logger.info(f"✅ Subscription activated for payment: {payment_id}")
        
        # ===== 2. Получение/создание пользователя =====
        user_id = get_or_create_user(tg_id)
        logger.info(f"👤 User ID: {user_id} (Telegram: {tg_id})")
        
        # ===== 3. Определение даты окончания подписки =====
        subscription_until = get_subscription_until(tg_id)
        logger.info(f"📅 Subscription until: {subscription_until.strftime('%d.%m.%Y')}")
        
        # ===== 4. Создание VPN клиента =====
        # timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        # client_name = f"user_{tg_id}_{timestamp}"
        client_name = f"tg_{tg_id}_{payment_id[:8]}"
        
        logger.info(f"🔑 Creating VPN client: {client_name}")
        
        # Асинхронное создание клиента через API
        client_data = await wg_client.create_client(name=client_name)
        
        if not client_data:
            logger.error(f"❌ Failed to create WireGuard client for payment {payment_id}")
            return False
        
        # Извлечение данных клиента
        client_id = client_data.get('id')
        client_ip = client_data.get('address')
        client_public_key = client_data.get('publicKey')
        
        logger.info(f"✅ Client created - ID: {client_id}, IP: {client_ip}")
        
        # ===== 5. Получение конфигурации =====
        logger.info(f"📄 Retrieving config for client: {client_id}")
        
        client_config = await wg_client.get_client_config(client_id)
        
        if not client_config:
            logger.error(f"❌ Failed to get config for client {client_id}")
            # Попытаться удалить созданного клиента
            await wg_client.delete_client(client_id)
            return False
        
        logger.info(f"✅ Config retrieved successfully")
        
        # ===== 6. Сохранение в БД =====
        create_vpn_key(
            user_id=user_id,
            payment_id=payment_id,
            client_ip=client_ip,
            client_public_key=client_public_key,
            config=client_config,
            expires_at=subscription_until
        )
        
        logger.info(f"💾 VPN key saved - User: {user_id}, IP: {client_ip}")
        
        # ===== 7. Получение информации о тарифе =====
        tariff_info = TARIFFS.get(tariff_key, {})
        tariff_name = tariff_info.get("name", tariff_key)
        
        # ===== 8. Отправка конфига в Telegram =====
        try:
            # Создание файла для отправки
            file = BufferedInputFile(
                client_config.encode('utf-8'),
                filename=f"vpn_{tg_id}_{timestamp}.conf"
            )
            
            # Формирование сообщения
            caption = (
                f"✅ Ваш VPN конфиг готов!\n\n"
                f"🔑 Тариф: {tariff_name}\n"
                f"🌐 IP адрес: {client_ip}\n"
                f"📅 Активен до: {subscription_until.strftime('%d.%m.%Y')}\n\n"
                f"📱 Инструкция:\n"
                f"1. Установите приложение AmneziaVPN\n"
                f"2. Импортируйте этот файл\n"
                f"3. Нажмите 'Подключить'\n\n"
                f"💬 Поддержка: @your_support"
            )
            
            # Отправка файла
            await bot.send_document(
                chat_id=tg_id,
                document=file,
                caption=caption
            )
            
            logger.info(f"📤 Config sent to Telegram user: {tg_id}")
            
        except Exception as e:
            logger.error(f"❌ Failed to send config to Telegram: {e}", exc_info=True)
            # Не возвращаем False, т.к. конфиг создан и сохранен в БД
            # Пользователь сможет получить его через бота позже
        
        logger.info(f"🎉 Payment {payment_id} processed successfully!")
        return True
        
    except Exception as e:
        logger.error(
            f"❌ Critical error processing payment {payment_id}: {e}", 
            exc_info=True
        )
        return False
