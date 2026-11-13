"""
Legacy MTProto TL Objects Backwards-Compatibility Patch for Telethon.
Restores support for deprecated constructor IDs still emitted by Telegram MTProto / Bot API:
- 0x48a30254: Legacy ReplyInlineMarkup
- 0xe62bc960: Legacy KeyboardButtonCallback
- 0x31774388: Legacy User (Layer 198-220)
"""
import struct
from typing import Optional, List
from telethon.tl import alltlobjects, types
from telethon.tl.tlobject import TLObject


class LegacyKeyboardButtonCallback(TLObject):
    CONSTRUCTOR_ID = 0xe62bc960
    SUBCLASS_OF_ID = 0xbad74a3

    @classmethod
    def from_reader(cls, reader):
        flags = reader.read_int()
        _requires_password = bool(flags & 1)
        _style = reader.tgread_object() if (flags & 1024) else None
        _text = reader.tgread_string()
        _data = reader.tgread_bytes()
        return types.KeyboardInlineButton(
            text=_text,
            type=types.InlineButtonTypeCallback(data=_data, requires_password=_requires_password),
            style=_style
        )


class LegacyReplyInlineMarkup(TLObject):
    CONSTRUCTOR_ID = 0x48a30254
    SUBCLASS_OF_ID = 0xe2e10ef2

    @classmethod
    def from_reader(cls, reader):
        reader.read_int()  # Vector magic 0x1cb5c415
        rows = []
        for _ in range(reader.read_int()):
            row = reader.tgread_object()
            if hasattr(row, 'buttons'):
                rows.append(types.KeyboardInlineButtonRow(buttons=row.buttons))
            else:
                rows.append(row)
        return types.ReplyInlineMarkup(rows=rows)


class LegacyUser(TLObject):
    CONSTRUCTOR_ID = 0x31774388
    SUBCLASS_OF_ID = 0x2da17977

    @classmethod
    def from_reader(cls, reader):
        flags = reader.read_int()

        _is_self = bool(flags & 1024)
        _contact = bool(flags & 2048)
        _mutual_contact = bool(flags & 4096)
        _deleted = bool(flags & 8192)
        _bot = bool(flags & 16384)
        _bot_chat_history = bool(flags & 32768)
        _bot_nochats = bool(flags & 65536)
        _verified = bool(flags & 131072)
        _restricted = bool(flags & 262144)
        _min = bool(flags & 1048576)
        _bot_inline_geo = bool(flags & 2097152)
        _support = bool(flags & 8388608)
        _scam = bool(flags & 16777216)
        _apply_min_photo = bool(flags & 33554432)
        _fake = bool(flags & 67108864)
        _bot_attach_menu = bool(flags & 134217728)
        _premium = bool(flags & 268435456)
        _attach_menu_enabled = bool(flags & 536870912)
        flags2 = reader.read_int()

        _bot_can_edit = bool(flags2 & 2)
        _close_friend = bool(flags2 & 4)
        _stories_hidden = bool(flags2 & 8)
        _stories_unavailable = bool(flags2 & 16)
        _contact_require_premium = bool(flags2 & 1024)
        _bot_business = bool(flags2 & 2048)
        _bot_has_main_app = bool(flags2 & 8192)
        _bot_forum_view = bool(flags2 & 65536)
        _bot_forum_can_manage_topics = bool(flags2 & 131072)
        _bot_can_manage_bots = bool(flags2 & 262144)
        _bot_guestchat = bool(flags2 & 524288)
        _bot_guard = bool(flags2 & 1048576)
        _id = reader.read_long()
        _access_hash = reader.read_long() if (flags & 1) else None
        _first_name = reader.tgread_string() if (flags & 2) else None
        _last_name = reader.tgread_string() if (flags & 4) else None
        _username = reader.tgread_string() if (flags & 8) else None
        _phone = reader.tgread_string() if (flags & 16) else None
        _photo = reader.tgread_object() if (flags & 32) else None
        _status = reader.tgread_object() if (flags & 64) else None
        _bot_info_version = reader.read_int() if (flags & 16384) else None

        if flags & 262144:
            reader.read_int()
            _restriction_reason = [reader.tgread_object() for _ in range(reader.read_int())]
        else:
            _restriction_reason = None

        _bot_inline_placeholder = reader.tgread_string() if (flags & 524288) else None
        _lang_code = reader.tgread_string() if (flags & 4194304) else None
        _emoji_status = reader.tgread_object() if (flags & 1073741824) else None

        if flags2 & 1:
            reader.read_int()
            _usernames = [reader.tgread_object() for _ in range(reader.read_int())]
        else:
            _usernames = None

        _stories_max_id = reader.tgread_object() if (flags2 & 32) else None
        _color = reader.tgread_object() if (flags2 & 256) else None
        _profile_color = reader.tgread_object() if (flags2 & 512) else None
        _bot_active_users = reader.read_int() if (flags2 & 4096) else None
        _bot_verification_icon = reader.read_long() if (flags2 & 16384) else None
        _send_paid_messages_stars = reader.read_long() if (flags2 & 32768) else None

        return types.User(
            id=_id,
            is_self=_is_self,
            contact=_contact,
            mutual_contact=_mutual_contact,
            deleted=_deleted,
            bot=_bot,
            bot_chat_history=_bot_chat_history,
            bot_nochats=_bot_nochats,
            verified=_verified,
            restricted=_restricted,
            min=_min,
            bot_inline_geo=_bot_inline_geo,
            support=_support,
            scam=_scam,
            apply_min_photo=_apply_min_photo,
            fake=_fake,
            bot_attach_menu=_bot_attach_menu,
            premium=_premium,
            attach_menu_enabled=_attach_menu_enabled,
            bot_can_edit=_bot_can_edit,
            close_friend=_close_friend,
            stories_hidden=_stories_hidden,
            stories_unavailable=_stories_unavailable,
            contact_require_premium=_contact_require_premium,
            bot_business=_bot_business,
            bot_has_main_app=_bot_has_main_app,
            bot_forum_view=_bot_forum_view,
            bot_forum_can_manage_topics=_bot_forum_can_manage_topics,
            bot_can_manage_bots=_bot_can_manage_bots,
            bot_guestchat=_bot_guestchat,
            bot_guard=_bot_guard,
            access_hash=_access_hash,
            first_name=_first_name,
            last_name=_last_name,
            username=_username,
            phone=_phone,
            photo=_photo,
            status=_status,
            bot_info_version=_bot_info_version,
            restriction_reason=_restriction_reason,
            bot_inline_placeholder=_bot_inline_placeholder,
            lang_code=_lang_code,
            emoji_status=_emoji_status,
            usernames=_usernames,
            stories_max_id=_stories_max_id,
            color=_color,
            profile_color=_profile_color,
            bot_active_users=_bot_active_users,
            bot_verification_icon=_bot_verification_icon,
            send_paid_messages_stars=_send_paid_messages_stars
        )


def apply_patch():
    """Register legacy TL constructors in Telethon."""
    alltlobjects.tlobjects[LegacyKeyboardButtonCallback.CONSTRUCTOR_ID] = LegacyKeyboardButtonCallback
    alltlobjects.tlobjects[LegacyReplyInlineMarkup.CONSTRUCTOR_ID] = LegacyReplyInlineMarkup
    alltlobjects.tlobjects[LegacyUser.CONSTRUCTOR_ID] = LegacyUser


# Auto-apply on import
apply_patch()
