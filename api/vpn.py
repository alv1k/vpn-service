def generate_vpn_config(tg_id: int) -> str:
    # временно — заглушка
    return f"""
[Interface]
PrivateKey = GENERATED_PRIVATE_KEY_FOR_{tg_id}
Address = 10.0.0.{tg_id % 250}/32
DNS = 1.1.1.1
"""
