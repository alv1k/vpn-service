# Дайджест VPN/Прокси приложений по платформам (РФ)

> **Дата составления:** 28 августа 2026 г.  
> **Статус:** Актуально для текущих блокировок Роскомнадзора (ТСПУ) и зачисток в RU App Store / Google Play.

---

## 1. iOS / iPadOS

> **Контекст блокировок:** Большинство классических VPN-клиентов (AmneziaVPN, V2Box, Shadowrocket, Streisand, Outline, WireGuard) удалены из российского сегмента App Store по требованиям регулятора.

| Приложение | Протоколы | Статус в RU App Store | Способы установки и альтернативы |
| :--- | :--- | :--- | :--- |
| **Happ Proxy** | VLESS, Reality, XHTTP, Hysteria2 | 🟢 **Доступен** *(маскировка)* | Прямой поиск в App Store / TestFlight |
| **Karing** | VLESS, Hysteria2, Tuic, Sing-box core | 🟢 **Доступен** | Прямой поиск в App Store / TestFlight |
| **Shadowrocket** | VLESS, Hysteria2, Reality, Shadowsocks | 🔴 **Удален из RU Store** ($2.99) | Смена региона Apple ID (US/TR/KZ) или семейный доступ |
| **Streisand** | VLESS, Reality, Hysteria2 | 🔴 **Удален из RU Store** | Смена региона Apple ID / TestFlight / Sideloading (IPA через AltStore/SideStore) |
| **FoXray** | VLESS, Reality, gRPC, Hysteria2 | 🟡 **Периодически блокируется** | Смена региона Apple ID |
| **AmneziaWG / AmneziaVPN** | AmneziaWG (awg0), OpenVPN, Cloak | 🔴 **Удален из RU Store** | Смена региона Apple ID / Установка IPA |

---

## 2. Android (Смартфоны и Планшеты)

> **Контекст блокировок:** В Google Play для РФ заблокированы многие VPN/прокси инструменты, однако платформа Android позволяет устанавливать APK напрямую без ограничений.

| Приложение | Протоколы | Статус в Google Play | Прямое скачивание без Google Play |
| :--- | :--- | :--- | :--- |
| **Happ Proxy** | VLESS, Reality, XHTTP, Hysteria2 | 🟢 Доступен | Официальный сайт happproxy.com / GitHub |
| **v2rayNG** | VLESS, Reality, XHTTP, Hysteria2 | 🟡 Скрыт в RU регионе | **GitHub Releases**, F-Droid, RuStore, APK с сайта сервиса |
| **NekoBox for Android (Matsuri)** | VLESS, Hysteria2, Sing-box core | 🟡 Скрыт в RU | **GitHub Releases**, F-Droid, прямой APK |
| **AmneziaVPN / AmneziaWG** | AmneziaWG (awg0) | 🔴 Удален из GP | **GitHub Releases**, RuStore, официальный сайт amnezia.org |
| **Karing** | Все протоколы Sing-box | 🟢 Доступен | GitHub Releases, APK |

---

## 3. Android TV / Google TV / Яндекс ТВ / Сбер Салют ТВ

> **Особенность:** Требуется поддержка интерфейса пульта ДУ (D-Pad).

| Приложение | Поддержка пульта (D-Pad) | Способ установки на ТВ в РФ |
| :--- | :--- | :--- |
| **v2rayNG (TV mode)** | 🟢 **Идеальная** *(автоматический TV UI)* | Установка APK через флешку, приложение *Send Files to TV* или *Downloader* (по короткому коду) |
| **NekoBox / sing-box TV** | 🟢 Удобно с пульта | Установка APK через флешку / Downloader |
| **Happ Proxy** | 🟡 Частичная *(рекомендуется аэромышь)* | Прямая установка APK |
| **AmneziaVPN TV** | 🟢 Специальный Android TV билд | APK с GitHub Amnezia |

---

## 4. Windows

> **Особенность:** Магазины приложений не требуются, полная свобода установки портативных и инсталляционных версий.

| Приложение | Поддерживаемые протоколы | Где скачать |
| :--- | :--- | :--- |
| **NekoRay / NekoBox for PC** | VLESS Reality, XHTTP Ultra, Hysteria2, Sing-box core | GitHub Releases (Portable zip / Setup) |
| **v2rayN** | VLESS Reality, XHTTP, Hysteria2 | GitHub Releases |
| **Happ Desktop** | VLESS, Hysteria2 | Сайт happproxy.com |
| **AmneziaVPN (Desktop)** | AmneziaWG, Xray | Сайт amnezia.org / GitHub Releases |
| **Hiddify Next** | Все протоколы (современный интерфейс) | GitHub Releases |

---

## 5. macOS

| Приложение | Протоколы | Способ установки в РФ |
| :--- | :--- | :--- |
| **Karing (macOS)** | VLESS, Hysteria2, Reality | Mac App Store (RU доступен) или прямой DMG с GitHub |
| **FoXray (macOS)** | VLESS, Reality, Hysteria2 | Mac App Store (требует не-RU Apple ID) |
| **NekoBox / Hiddify** | Все современные протоколы | Прямой DMG с GitHub (без участия App Store) |
| **AmneziaVPN** | AmneziaWG | Прямой DMG установщик с amnezia.org / GitHub |

---

## 6. Другие TV-платформы (Apple TV, LG webOS, Samsung Tizen)

1. **Apple TV (tvOS 17+)**:
   - **Karing / Sing-box / Shadowrocket**: нативно поддерживают tvOS. При наличии зарубежного Apple ID приложение ставится прямо на ТВ-приставку и проксирует весь трафик домашнего кинотеатра.
2. **Samsung (Tizen OS) и LG (webOS)**:
   - Закрытые проприетарные операционные системы. Прямая установка VPN-клиентов технически невозможна.
   - **Решение:** Подключение через домашний роутер с поддержкой VPN/Xray (Keenetic, OpenWrt, Mikrotik) либо раздача локального прокси-сервера через приложение с телефона/ПК.

---

## Рекомендации для выдачи пользователям сервиса TIIN

1. **iOS (iPhone/iPad):**
   - Основной вариант: **Happ Proxy** или **Karing** (напрямую из RU App Store).
   - Запасной / Продвинутый: **Shadowrocket** (через не-RU Apple ID).
2. **Android:**
   - **Happ Proxy** (Google Play).
   - **v2rayNG** и **AmneziaWG** (давать прямую кнопку «Скачать APK» в личном кабинете).
3. **Android TV:**
   - **v2rayNG (TV mode)** через приложение *Downloader* или APK с флешки.
4. **Windows / macOS:**
   - **Hiddify** / **NekoRay** / **AmneziaVPN** (прямые ссылки на `.exe` / `.dmg`).
