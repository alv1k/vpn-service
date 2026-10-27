"""
Telethon Client Wrapper for Alex (QA Sentinel).
Provides high-level actions: clean-slate DB, sending commands, clicking inline buttons, and SLA tracking.
"""
import os
import sys
import time
import asyncio
import logging
from typing import Optional, Tuple, List
from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qa_agent.config as config
from api.db import get_db

logger = logging.getLogger("alex_qa.client")


class AlexClient:
    def __init__(self):
        self.client = TelegramClient(config.SESSION_FILE, config.API_ID, config.API_HASH)
        self.bot_entity = None
        self.me = None
        self.tg_id = None
        self._incoming_messages = []
        self._edited_messages = {}

    async def start(self):
        """Starts Telethon client session and resolves target bot entity."""
        await self.client.start()
        self.me = await self.client.get_me()
        self.tg_id = self.me.id
        self.bot_entity = await self.client.get_input_entity(config.BOT_USERNAME)
        logger.info(f"[ALEX QA] Logged in as: {self.me.first_name} (@{self.me.username}, tg_id: {self.tg_id})")
        logger.info(f"[ALEX QA] Target bot: @{config.BOT_USERNAME}")

        # Register push handlers for instant 0-latency message updates without polling flood
        @self.client.on(events.NewMessage(chats=self.bot_entity))
        async def _on_new_msg(event):
            if not event.message.out:
                self._incoming_messages.append(event.message)

        @self.client.on(events.MessageEdited(chats=self.bot_entity))
        async def _on_msg_edited(event):
            self._edited_messages[event.message.id] = event.message

    async def stop(self):
        """Disconnects client session."""
        if self.client.is_connected():
            await self.client.disconnect()

    def clean_slate_db(self):
        """
        Resets the test user in MySQL database so the bot treats the user as new.
        If the account is ADMIN_TG_ID, it resets trial flags rather than deleting the admin row.
        """
        if not self.tg_id:
            logger.warning("[ALEX QA] Cannot clean DB: tg_id is not yet resolved.")
            return

        from api.db import execute_query

        if self.tg_id == config.ADMIN_TG_ID:
            logger.info(f"[ALEX QA] 🛡️ Account is Admin ({self.tg_id}). Resetting trial flags without deleting admin user.")
            execute_query(
                "UPDATE users SET test_vless_activated = 0, test_awg_activated = 0, in_rescue = 0, bot_blocked = 0 WHERE tg_id = %s",
                (self.tg_id,)
            )
            execute_query("DELETE FROM user_platforms WHERE tg_id = %s", (self.tg_id,))
            return

        row = execute_query("SELECT id FROM users WHERE tg_id = %s", (self.tg_id,), fetch="one")
        if row:
            user_id = row["id"]
            execute_query("DELETE FROM vpn_keys WHERE user_id = %s OR tg_id = %s", (user_id, self.tg_id))
            execute_query("DELETE FROM user_platforms WHERE tg_id = %s", (self.tg_id,))
            execute_query("DELETE FROM message_log WHERE tg_id = %s", (self.tg_id,))
            execute_query("DELETE FROM payments WHERE tg_id = %s", (self.tg_id,))
            execute_query("DELETE FROM users WHERE id = %s", (user_id,))
            logger.info(f"[ALEX QA] 🧹 Clean slate complete for tg_id={self.tg_id} (user_id={user_id})")
        else:
            execute_query("DELETE FROM vpn_keys WHERE tg_id = %s", (self.tg_id,))
            execute_query("DELETE FROM message_log WHERE tg_id = %s", (self.tg_id,))
            execute_query("DELETE FROM user_platforms WHERE tg_id = %s", (self.tg_id,))
            logger.info(f"[ALEX QA] 🧹 User tg_id={self.tg_id} not found in DB, clean slate ensured.")

    async def send_command(self, command: str) -> Optional[Message]:
        """Sends a text command (e.g. /start) to the bot and returns the sent message."""
        logger.info(f"[ALEX QA] 📤 Sending command: {command}")
        try:
            return await self.client.send_message(self.bot_entity, command)
        except Exception as e:
            logger.warning(f"[ALEX QA] ⚠️ send_command '{command}' notice: {e}")
            return None

    async def wait_for_response(self, min_id: int = 0, timeout: float = config.DEFAULT_WAIT_TIMEOUT) -> Tuple[Optional[Message], float]:
        """
        Waits for a new message from the bot with ID > min_id.
        Returns (message, latency_seconds).
        """
        start_time = time.monotonic()
        deadline = start_time + timeout

        while time.monotonic() < deadline:
            # First check push events (0ms latency, zero Telegram API calls)
            for msg in reversed(self._incoming_messages):
                if msg.id > min_id:
                    latency = round(time.monotonic() - start_time, 3)
                    logger.info(f"[ALEX QA] 📥 Received response via push event in {latency}s (msg_id={msg.id})")
                    return msg, latency

            # Periodically (every 2.0s) do a single fallback check
            elapsed = time.monotonic() - start_time
            if elapsed > 1.5 and int(elapsed * 10) % 20 == 0:
                try:
                    messages = await self.client.get_messages(self.bot_entity, limit=3)
                    for msg in messages:
                        if not msg.out and msg.id > min_id:
                            latency = round(time.monotonic() - start_time, 3)
                            logger.info(f"[ALEX QA] 📥 Received response in {latency}s (msg_id={msg.id})")
                            return msg, latency
                except Exception:
                    pass

            await asyncio.sleep(0.1)

        latency = round(time.monotonic() - start_time, 3)
        logger.error(f"[ALEX QA] ⏱️ Timeout ({timeout}s) waiting for bot response after msg_id={min_id}")
        return None, latency

    async def send_text(self, text: str) -> Optional[Message]:
        """Sends arbitrary text to the bot."""
        logger.info(f"[ALEX QA] ✍️ Sending text: {text}")
        try:
            return await self.client.send_message(self.bot_entity, text)
        except Exception as e:
            logger.warning(f"[ALEX QA] ⚠️ send_text notice: {e}")
            return None

    async def click_inline_button(
        self,
        message: Optional[Message],
        button_text_match: Optional[str] = None,
        callback_data: Optional[str] = None,
        index: Optional[int] = None,
        timeout: float = 15.0
    ) -> Tuple[Optional[Message], float]:
        """
        Clicks an inline button on a message by text substring, callback_data or flat index.
        Waits for edited message or new message. Returns (resulting_message, latency_seconds).
        """
        if not message or not getattr(message, "buttons", None):
            logger.warning(f"[ALEX QA] Message {getattr(message, 'id', None)} has no inline buttons!")
            return None, 0.0

        target_btn = None
        flat_buttons = [btn for row in message.buttons for btn in row]

        if index is not None and 0 <= index < len(flat_buttons):
            target_btn = flat_buttons[index]
        elif callback_data:
            for btn in flat_buttons:
                if hasattr(btn, 'data') and btn.data and btn.data.decode('utf-8', errors='ignore') == callback_data:
                    target_btn = btn
                    break
        elif button_text_match:
            for btn in flat_buttons:
                if button_text_match.lower() in btn.text.lower():
                    target_btn = btn
                    break

        if not target_btn:
            available = [f"{b.text} (data={b.data.decode('utf-8', errors='ignore') if hasattr(b, 'data') and b.data else ''})" for b in flat_buttons]
            raise ValueError(f"Button (text='{button_text_match}', data='{callback_data}') not found! Available: {available}")

        logger.info(f"[ALEX QA] 👆 Clicking inline button: '{target_btn.text}'")
        start_time = time.monotonic()
        msg_id_before = message.id
        orig_edit_date = message.edit_date
        orig_text = message.text
        orig_buttons = [[b.text for b in row] for row in message.buttons] if message.buttons else []

        # Click the button
        await target_btn.click()

        # Wait for either edited message, new message or updated buttons via push events
        deadline = start_time + timeout
        while time.monotonic() < deadline:
            # 1. Check if an edited event arrived for this message
            if msg_id_before in self._edited_messages:
                m = self._edited_messages[msg_id_before]
                curr_btns = [[b.text for b in row] for row in m.buttons] if m.buttons else []
                if m.edit_date != orig_edit_date or m.text != orig_text or curr_btns != orig_buttons:
                    latency = round(time.monotonic() - start_time, 3)
                    return m, latency

            # 2. Check if a new message arrived
            for msg in reversed(self._incoming_messages):
                if msg.id > msg_id_before:
                    latency = round(time.monotonic() - start_time, 3)
                    return msg, latency

            # 3. Fallback poll only after 1.5s
            elapsed = time.monotonic() - start_time
            if elapsed > 1.5 and int(elapsed * 10) % 20 == 0:
                try:
                    latest_messages = await self.client.get_messages(self.bot_entity, limit=3)
                    if latest_messages:
                        if latest_messages[0].id > msg_id_before and not latest_messages[0].out:
                            latency = round(time.monotonic() - start_time, 3)
                            return latest_messages[0], latency
                        for m in latest_messages:
                            if m.id == msg_id_before:
                                curr_btns = [[b.text for b in row] for row in m.buttons] if m.buttons else []
                                if m.edit_date != orig_edit_date or m.text != orig_text or curr_btns != orig_buttons:
                                    latency = round(time.monotonic() - start_time, 3)
                                    return m, latency
                except Exception:
                    pass

            await asyncio.sleep(0.1)

        latency = round(time.monotonic() - start_time, 3)
        return None, latency

    async def get_latest_message(self) -> Optional[Message]:
        """Fetches the latest message in the chat with the bot."""
        try:
            msgs = await self.client.get_messages(self.bot_entity, limit=1)
            return msgs[0] if msgs else None
        except Exception as e:
            logger.warning(f"[ALEX QA] Telethon get_messages notice: {e}")
            return None
