import re

PLATFORM_RULES = [
    (re.compile(r'v2rayNG[ /\v]([\d.]+)', re.I),         'android', 'v2rayNG'),
    (re.compile(r'NekoBox[ /\v]([\d.]+)', re.I),          'android', 'NekoBox'),
    (re.compile(r'Matsuri[ /\v]([\d.]+)', re.I),          'android', 'Matsuri'),
    (re.compile(r'AnXray[ /\v]([\d.]+)', re.I),           'android', 'AnXray'),
    (re.compile(r'SagerNet[ /\v]([\d.]+)', re.I),         'android', 'SagerNet'),
    (re.compile(r'v2rayNG[-/ ]?[\d.]', re.I),             'android', 'v2rayNG'),
    (re.compile(r'Shadowrocket[ /\v]([\d.]+)', re.I),     'ios',     'Shadowrocket'),
    (re.compile(r'Stash[ /\v]([\d.]+)', re.I),            'ios',     'Stash'),
    (re.compile(r'Happ[ /\v]([\d.]+)', re.I),             None,      'Happ'),
    (re.compile(r'V2Box[ /\v]([\d.]+)', re.I),            'ios',     'V2Box'),
    (re.compile(r'Streisand[ /\v]([\d.]+)', re.I),        'ios',     'Streisand'),
    (re.compile(r'FairVPN[ /\v]([\d.]+)', re.I),          'ios',     'FairVPN'),
    (re.compile(r'FoXray[ /\v]([\d.]+)', re.I),           'ios',     'FoXray'),
    (re.compile(r'v2rayN[ /\v]([\d.]+)', re.I),           'windows', 'v2rayN'),
    (re.compile(r'Clash\.?Meta[ /\v]?([\d.]+)', re.I),    None,      'ClashMeta'),
    (re.compile(r'ClashMeta[ /\v]([\d.]+)', re.I),        None,      'ClashMeta'),
    (re.compile(r'Hiddify[ ]?Next[ /\v]([\d.]+)', re.I),  None,      'HiddifyNext'),
    (re.compile(r'Hiddify[ /\v]([\d.]+)', re.I),          None,      'Hiddify'),
    (re.compile(r'Sing-box[ /\v]([\d.]+)', re.I),         None,      'Sing-box'),
    (re.compile(r'Karing[ /\v]([\d.]+)', re.I),           None,      'Karing'),
    (re.compile(r'Loon[ /\v]([\d.]+)', re.I),             'ios',     'Loon'),
    (re.compile(r'Surge[ /\v]([\d.]+)', re.I),            'ios',     'Surge'),
    (re.compile(r'quantumult[ /\v]?([\d.]+)', re.I),      'ios',     'Quantumult'),
    (re.compile(r'qV2ray[ /\v]([\d.]+)', re.I),           'linux',   'qV2ray'),
]


OS_PATTERNS = [
    (re.compile(r'\bAndroid\b', re.I),              'android'),
    (re.compile(r'\bDarwin\b', re.I),               'macos'),
    (re.compile(r'\bWindows\b', re.I),              'windows'),
    (re.compile(r'\biPhone\b', re.I),               'ios'),
    (re.compile(r'\biPad\b', re.I),                 'ios'),
    (re.compile(r'\bLinux\b', re.I),                'linux'),
    (re.compile(r'\biOS\b', re.I),                  'ios'),
    (re.compile(r'\bMac OS X\b', re.I),             'macos'),
    (re.compile(r'\bMacintosh\b', re.I),            'macos'),
    (re.compile(r'\bmacOS\b', re.I),                'macos'),
    (re.compile(r'\bCFNetwork\b', re.I),            'ios'),
]


def detect_platform(user_agent: str | None) -> tuple[str, str | None, str | None]:
    if not user_agent:
        return 'unknown', None, None

    ua = user_agent.strip()

    for pattern, default_platform, app_name in PLATFORM_RULES:
        m = pattern.search(ua)
        if m:
            version = m.group(1) if m.lastindex and m.lastindex >= 1 else None
            platform = default_platform
            if platform is None:
                platform = _resolve_platform_from_ua(ua)
            return platform, app_name, version

    platform = _resolve_platform_from_ua(ua)
    return platform, None, None


def _resolve_platform_from_ua(ua: str) -> str:
    for pattern, platform in OS_PATTERNS:
        if pattern.search(ua):
            return platform
    return 'unknown'
