"""
List and verification helper for disposable / temporary email domains.
Prevents bot registration and trial abuse.
"""
import logging

logger = logging.getLogger(__name__)

# Set of known disposable email domains
DISPOSABLE_DOMAINS = {
    # Recent attacker domains
    "teto.lol",

    # Popular temporary mail services
    "10minutemail.com",
    "10minutemail.net",
    "10minmail.com",
    "1secmail.com",
    "1secmail.net",
    "1secmail.org",
    "burnermail.io",
    "crazymailing.com",
    "dispostable.com",
    "dropmail.me",
    "emailondeck.com",
    "fakemailgenerator.com",
    "generator.email",
    "getairmail.com",
    "getnada.com",
    "grr.la",
    "guerrillamail.biz",
    "guerrillamail.com",
    "guerrillamail.de",
    "guerrillamail.net",
    "guerrillamail.org",
    "guerrillamailblock.com",
    "inboxbear.com",
    "maildrop.cc",
    "mailinator.com",
    "mailinator2.com",
    "minutemail.com",
    "minuteinbox.com",
    "mohmal.com",
    "pokemail.net",
    "sharklasers.com",
    "spam4.me",
    "temp-mail.org",
    "tempinbox.com",
    "tempmail.com",
    "tempmail.net",
    "tempmailo.com",
    "throwawaymail.com",
    "trashmail.com",
    "trashmail.me",
    "trashmail.net",
    "yopmail.com",
    "yopmail.fr",
    "yopmail.net",
    "mytemp.email",
    "nada.ltd",
    "nada.email",
    "fakeinbox.com",
    "guerrillamail.info",
    "guerrillamail.biz",
    "spambog.com",
    "spambog.de",
    "spambog.ru",
    "mailcatch.com",
    "harakirimail.com",
    "armyspy.com",
    "cuvox.de",
    "dayrep.com",
    "fleckens.hu",
    "gustr.com",
    "jourrapide.com",
    "rhyta.com",
    "superrito.com",
    "teleworm.us",
    "tinac.net",
    "crazymailing.com",
}


def is_disposable_email(email: str | None) -> bool:
    """Returns True if the email belongs to a known disposable/temporary email provider."""
    if not email or "@" not in email:
        return False

    domain = email.split("@")[-1].lower().strip()

    if domain in DISPOSABLE_DOMAINS:
        return True

    # Check for subdomains (e.g. sub.teto.lol)
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        sub = ".".join(parts[i:])
        if sub in DISPOSABLE_DOMAINS:
            return True

    return False
