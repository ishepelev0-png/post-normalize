"""
Django management command to run Telegram message normalizer.

Usage:
    python manage.py run_normalizer
"""

import asyncio
import random
import os
import logging
from collections import defaultdict
from typing import Optional, Dict

from django.core.management.base import BaseCommand, CommandError
from pyrogram import Client, filters, types, Idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message

from post_normalizer.models import (
    NormalizerGroup,
    AuthorPostCount,
    PostHash,
    PendingInvite,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class Command(BaseCommand):
    help = "Run Telegram message normalizer for active groups"

    async def handle_message(self, client: Client, message: types.Message):
        """Handle new message."""
        try:
            group = NormalizerGroup.objects.get(chat_id=message.chat.id, is_active=True)
        except NormalizerGroup.DoesNotExist:
            return

        if message.from_user and message.from_user.is_self:
            return

        if not (message.text or message.caption or message.media):
            return

        # Delay
        await asyncio.sleep(
            random.uniform(group.delay_seconds, group.delay_seconds + 120)
        )

        # Check if message still exists
        try:
            reloaded = await client.get_messages(message.chat.id, message.id)
            if not reloaded:
                return
        except Exception:
            return

        # Media file_id for hash
        media_file_id = None
        if message.photo:
            media_file_id = message.photo.file_id
        elif message.video:
            media_file_id = message.video.file_id
        elif message.document:
            media_file_id = message.document.file_id

        text_for_hash = message.text or message.caption or ""
        msg_hash = PostHash.create_hash(text_for_hash, media_file_id)

        if PostHash.is_duplicate(group, msg_hash):
            await message.delete()
            return

        sender_id = message.from_user.id if message.from_user else None
        if not sender_id:
            return

        # Limits
        counter, _ = AuthorPostCount.objects.get_or_create(
            group=group, user_id=sender_id
        )
        counter.reset_if_needed()
        if (
            group.limit_posts_day > 0 and counter.posts_today >= group.limit_posts_day
        ) or (
            group.limit_posts_week > 0
            and counter.posts_this_week >= group.limit_posts_week
        ):
            await message.delete()
            return

        counter.posts_today += 1
        counter.posts_this_week += 1
        counter.save()

        await message.delete()

        # Button
        reply_markup = None
        if group.buttons_count > 0:
            button_text = group.get_button_text(random.randint(0, 99))
            reply_markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton(button_text, url=f"tg://user?id={sender_id}")]]
            )

        # Text with suffix
        repost_text = (message.text or message.caption or "") + group.suffix_text

        # Album handling (collect if media_group_id)
        if message.media_group_id:
            # Simple collection (in production use cache)
            messages = await client.get_media_group(message.chat.id, message.id)
            new_messages = []
            for msg in messages:
                new_msg = await msg.copy(
                    chat_id=message.chat.id,
                    caption=(
                        (msg.caption or "") + group.suffix_text if msg.media else None
                    ),
                    reply_markup=reply_markup,
                )
                new_messages.append(new_msg)
            new_message = new_messages[0]  # first for link
        else:
            new_message = await message.copy(
                chat_id=message.chat.id,
                caption=repost_text if message.media else None,
                text=repost_text if not message.media else None,
                reply_markup=reply_markup,
            )

        # Save hash
        PostHash.objects.create(group=group, message_hash=msg_hash)

        # Forward handling
        if message.forward_from or message.forward_from_chat:
            original_id = (
                message.forward_from.id
                if message.forward_from
                else message.forward_from_chat.id
            )
            author_name = (
                message.forward_from.first_name
                if message.forward_from
                else message.forward_from_chat.title or ""
            )
            author_username = (
                f"@{message.forward_from.username}"
                if message.forward_from and message.forward_from.username
                else ""
            )

            # Post link
            username = (await client.get_chat(message.chat.id)).username
            if username:
                post_link = f"https://t.me/{username}/{new_message.id}"
            else:
                chat_id_str = (
                    str(message.chat.id)[4:]
                    if str(message.chat.id).startswith("-100")
                    else abs(message.chat.id)
                )
                post_link = f"https://t.me/c/{chat_id_str}/{new_message.id}"

            rendered_text = group.invite_text.format(
                author_name=author_name,
                author_username=author_username,
                group_name=message.chat.title or "группа",
                post_link=post_link,
                rules_link="",  # добавь поле в модель позже
            )

            try:
                await client.send_message(original_id, rendered_text)
            except Exception as e:
                logger.warning(f"Invite failed: {e}")

            # Pending invite
            try:
                await client.get_chat_member(message.chat.id, original_id)
            except Exception:
                PendingInvite.objects.get_or_create(group=group, user_id=original_id)

    def handle(self, *args, **options):
        api_id = os.environ.get("TELEGRAM_API_ID")
        api_hash = os.environ.get("TELEGRAM_API_HASH")
        session_name = os.environ.get("TELEGRAM_SESSION_NAME", "normalizer_session")

        if not api_id or not api_hash:
            raise CommandError("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")

        try:
            api_id = int(api_id)
        except ValueError:
            raise CommandError("TELEGRAM_API_ID must be integer")

        client = Client(session_name, api_id=api_id, api_hash=api_hash)

        active_chat_ids = list(
            NormalizerGroup.objects.filter(is_active=True).values_list(
                "chat_id", flat=True
            )
        )
        if not active_chat_ids:
            self.stdout.write(self.style.WARNING("No active groups"))
            return

        chat_filter = filters.chat(active_chat_ids) & filters.group

        @client.on_message(chat_filter)
        async def wrapper(_, message: Message):
            await self.handle_message(client, message)

        asyncio.run(client.start())
        self.stdout.write(self.style.SUCCESS("Normalizer running..."))
        asyncio.run(Idle())
        asyncio.run(client.stop())
