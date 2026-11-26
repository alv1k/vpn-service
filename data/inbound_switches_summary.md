# 📊 Аналитический срез переключений инбаундов и операторов VPN
*Период: 2026-09-18 04:16:27 — 2026-11-17 04:26:18 | Всего событий: 880,320*

## 1. Сводные метрики
- **Всего переключений протоколов/инбаундов (SWITCH)**: `188,990`
- **Всего смен сетей/операторов (ROAMING)**: `521,606`
- **Событий с частым скачком / флаппингом (< 5с)**: `125,111` (66.2%)

### Помесячная динамика
| Месяц | Переключения (SWITCH) | Смены сетей (ROAMING) |
|---|---|---|
| **2026-09** | 101,108 | 433,491 |
| **2026-10** | 84,025 | 87,876 |
| **2026-11** | 3,857 | 239 |

## 2. Анализ задержек переключения (Flapping & User Behavior)
| Категория задержки | Количество | Доля (%) | Интерпретация |
|---|---|---|---|
| `< 5s (Flapping)` | 125,111 | 66.2% |
| `5s - 30s (Rapid)` | 49,550 | 26.2% |
| `30s - 300s (Moderate)` | 13,633 | 7.2% |
| `> 300s (Organic)` | 696 | 0.4% |

## 3. Матрица переходов (Топ направлений смены инбаундов)
| Переход (Откуда ➔ Куда) | Количество | Доля от всех переходов |
|---|---|---|
| `Hysteria2 Turbo (4443) ➔ Reality Vision (7443)` | 66,782 | 35.3% |
| `Reality Vision (7443) ➔ Hysteria2 Turbo (4443)` | 66,699 | 35.3% |
| `Reality Standard (53151) ➔ Hysteria2 Turbo (4443)` | 13,106 | 6.9% |
| `Hysteria2 Turbo (4443) ➔ Reality Standard (53151)` | 13,042 | 6.9% |
| `xHTTP Stream (47447) ➔ Hysteria2 Turbo (4443)` | 9,352 | 4.9% |
| `Hysteria2 Turbo (4443) ➔ xHTTP Stream (47447)` | 9,342 | 4.9% |
| `Reality Vision (7443) ➔ Reality Standard (53151)` | 2,389 | 1.3% |
| `Reality Standard (53151) ➔ Reality Vision (7443)` | 2,298 | 1.2% |
| `Reality Vision (7443) ➔ xHTTP Stream (47447)` | 1,421 | 0.8% |
| `xHTTP Stream (47447) ➔ Reality Vision (7443)` | 1,402 | 0.7% |
| `xHTTP Armor (48745) ➔ Hysteria2 Turbo (4443)` | 870 | 0.5% |
| `Hysteria2 Turbo (4443) ➔ xHTTP Armor (48745)` | 855 | 0.5% |
| `xHTTP Armor (48745) ➔ Reality Vision (7443)` | 371 | 0.2% |
| `Reality Vision (7443) ➔ xHTTP Armor (48745)` | 367 | 0.2% |
| `Reality Standard (53151) ➔ xHTTP Stream (47447)` | 137 | 0.1% |

## 4. Распределение переключений по ключевым операторам РФ
| Оператор / Провайдер | Всего переключений | Топ переход |
|---|---|---|
| **Other / RU ISP** | 34,541 | `Reality Vision (7443) ➔ Hysteria2 Turbo (4443)` (18,422) |
| **Rostelecom (YKT)** | 26,765 | `Reality Vision (7443) ➔ Hysteria2 Turbo (4443)` (12,130) |
| **Beeline** | 22,663 | `Reality Vision (7443) ➔ Hysteria2 Turbo (4443)` (10,029) |
| **Internal VPN / Bridge** | 17,814 | `Hysteria2 Turbo (4443) ➔ Reality Vision (7443)` (17,811) |
| **Unknown** | 14,504 | `Hysteria2 Turbo (4443) ➔ Reality Vision (7443)` (8,822) |
| **Rostelecom** | 14,320 | `Reality Vision (7443) ➔ Hysteria2 Turbo (4443)` (5,978) |
| **MTS** | 11,096 | `Hysteria2 Turbo (4443) ➔ xHTTP Stream (47447)` (4,652) |
| **RU ISP (in-addr)** | 10,909 | `Reality Vision (7443) ➔ Hysteria2 Turbo (4443)` (6,126) |
| **MegaFon** | 9,738 | `Reality Standard (53151) ➔ Hysteria2 Turbo (4443)` (3,241) |
| **RU ISP (sknt)** | 9,223 | `Hysteria2 Turbo (4443) ➔ Reality Vision (7443)` (5,748) |
| **RU ISP (kampaneko)** | 4,618 | `xHTTP Stream (47447) ➔ Hysteria2 Turbo (4443)` (2,468) |
| **RU ISP (spd-mgts)** | 4,319 | `Reality Vision (7443) ➔ Hysteria2 Turbo (4443)` (2,319) |

## 5. Топ-10 пользователей по числу переключений
| Пользователь | Количество переключений |
|---|---|
| `vless_6335998601` | 45,497 |
| `tiin_667624374` | 42,778 |
| `bwd2scof` | 35,841 |
| `tiin_284509567` | 20,144 |
| `tiin_138315162` | 14,106 |
| `tiin_401499373` | 10,106 |
| `tiin_261343964` | 7,216 |
| `tiin_115744584` | 4,020 |
| `vless_667624374` | 2,511 |
| `tiin_158375551` | 2,282 |
