"""
Management command to run the Telegram message normalizer.

Usage:
    python manage.py run_normalizer
"""
import asyncio
import random
import logging
import os
from typing import Optional

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings

from pyrogram import Client
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ChatType

from post_normalizer.models import (
    NormalizerGroup,
    AuthorPostCount,
    PostHash,
    PendingInvite,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Run Telegram message normalizer."""
    
    help = 'Run Telegram message normalizer for active groups'
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client: Optional[Client] = None
        self.button_rotation_index: dict[int, int] = {}
    
    def handle(self, *args, **options):
        """Main entry point."""
        self.stdout.write(self.style.SUCCESS('Starting Telegram normalizer...'))
        
        # Get API credentials from environment or settings
        api_id = os.environ.get('TELEGRAM_API_ID') or settings.TELEGRAM_API_ID
        api_hash = os.environ.get('TELEGRAM_API_HASH') or settings.TELEGRAM_API_HASH
        session_name = os.environ.get('TELEGRAM_SESSION_NAME') or settings.TELEGRAM_SESSION_NAME or 'userbot'
        
        if not api_id or not api_hash:
            self.stdout.write(
                self.style.ERROR(
                    'ERROR: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in environment or settings'
                )
            )
            return
        
        # Run async event loop
        try:
            asyncio.run(self.run_normalizer(api_id, api_hash, session_name))
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('\nNormalizer stopped by user'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {e}'))
            logger.exception('Normalizer error')
    
    async def run_normalizer(self, api_id: int, api_hash: str, session_name: str):
        """Initialize and run Pyrogram client."""
        self.client = Client(
            name=session_name,
            api_id=int(api_id),
            api_hash=api_hash,
            workdir='.'
        )
        
        # Register message handler
        self.client.on_message()(self.handle_message)
        
        # Start client
        await self.client.start()
        self.stdout.write(self.style.SUCCESS('Normalizer started successfully!'))
        
        # Keep running
        await self.client.idle()
    
    async def handle_message(self, client: Client, message: Message):
        """
        Handle new messages in configured groups.
        
        Flow:
        1. Check if message is from an active configured group
        2. Wait random delay
        3. Check if message still exists
        4. Check for duplicates
        5. Delete original and repost
        6. Handle forwards and invites
        """
        # Only process messages from supergroups
        if not message.chat or message.chat.type != ChatType.SUPERGROUP:
            return
        
        chat_id = message.chat.id
        
        # Get group configuration
        try:
            group = await asyncio.to_thread(
                NormalizerGroup.objects.get,
                chat_id=chat_id,
                is_active=True
            )
        except NormalizerGroup.DoesNotExist:
            return
        
        # Skip if message is from the bot itself
        if message.from_user and message.from_user.is_self:
            return
        
        # Skip service messages without content
        if not message.text and not message.caption and not message.media:
            return
        
        # Wait random delay
        delay = random.uniform(group.delay_seconds, group.delay_seconds + 120)
        await asyncio.sleep(delay)
        
        # Check if message still exists
        try:
            await client.get_messages(chat_id, message.id)
        except Exception:
            logger.info(f"Message {message.id} no longer exists, skipping")
            return
        
        # Get message content for hash
        text_content = message.text or message.caption or ''
        media_file_id = None
        
        if message.photo:
            media_file_id = message.photo.file_id
        elif message.video:
            media_file_id = message.video.file_id
        elif message.document:
            media_file_id = message.document.file_id
        elif message.audio:
            media_file_id = message.audio.file_id
        elif message.voice:
            media_file_id = message.voice.file_id
        
        # Compute hash
        message_hash = await asyncio.to_thread(
            PostHash.create_hash,
            text_content,
            media_file_id
        )
        
        # Check for duplicates
        is_duplicate = await asyncio.to_thread(
            PostHash.is_duplicate,
            group,
            message_hash
        )
        
        if is_duplicate:
            logger.info(f"Duplicate message {message.id} detected, deleting original")
            try:
                await message.delete()
            except Exception as e:
                logger.error(f"Error deleting duplicate message: {e}")
            return
        
        # Get original sender info
        original_sender_id = message.from_user.id if message.from_user else None
        forward_from_id = message.forward_from.id if message.forward_from else None
        
        # Check post limits
        if original_sender_id:
            author_count, _ = await asyncio.to_thread(
                AuthorPostCount.objects.get_or_create,
                group=group,
                user_id=original_sender_id,
                defaults={
                    'posts_today': 0,
                    'posts_this_week': 0,
                }
            )
            
            await asyncio.to_thread(author_count.reset_if_needed)
            
            # Check day limit
            if group.limit_posts_day > 0 and author_count.posts_today >= group.limit_posts_day:
                logger.info(f"Day limit reached for user {original_sender_id}")
                return
            
            # Check week limit
            if group.limit_posts_week > 0 and author_count.posts_this_week >= group.limit_posts_week:
                logger.info(f"Week limit reached for user {original_sender_id}")
                return
        
        # Delete original message
        try:
            await message.delete()
        except Exception as e:
            logger.error(f"Error deleting original message: {e}")
            return
        
        # Prepare repost text
        repost_text = text_content
        if group.suffix_text:
            repost_text = f"{repost_text}\n\n{group.suffix_text}" if repost_text else group.suffix_text
        
        # Prepare entities (formatting)
        entities = message.entities or message.caption_entities or []
        
        # Create inline keyboard with button
        keyboard = []
        if group.buttons_count >= 1 and original_sender_id:
            # Get rotation index for this group
            rotation_index = self.button_rotation_index.get(chat_id, 0)
            button_text = group.get_button_text(rotation_index)
            
            # Cycle rotation for next time
            self.button_rotation_index[chat_id] = (rotation_index + 1) % 4
            
            button = InlineKeyboardButton(
                text=button_text,
                url=f"tg://user?id={original_sender_id}"
            )
            keyboard.append([button])
        
        if group.buttons_count >= 2 and group.button2_text:
            button2 = InlineKeyboardButton(
                text=group.button2_text,
                url="#"  # Configure URL as needed
            )
            keyboard.append([button2])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        # Repost message
        try:
            if message.media:
                # Repost with media - handle different media types
                if message.photo:
                    sent_message = await client.send_photo(
                        chat_id=chat_id,
                        photo=message.photo.file_id,
                        caption=repost_text if repost_text else None,
                        caption_entities=entities if repost_text else None,
                        reply_markup=reply_markup,
                    )
                elif message.video:
                    sent_message = await client.send_video(
                        chat_id=chat_id,
                        video=message.video.file_id,
                        caption=repost_text if repost_text else None,
                        caption_entities=entities if repost_text else None,
                        reply_markup=reply_markup,
                    )
                elif message.document:
                    sent_message = await client.send_document(
                        chat_id=chat_id,
                        document=message.document.file_id,
                        caption=repost_text if repost_text else None,
                        caption_entities=entities if repost_text else None,
                        reply_markup=reply_markup,
                    )
                elif message.audio:
                    sent_message = await client.send_audio(
                        chat_id=chat_id,
                        audio=message.audio.file_id,
                        caption=repost_text if repost_text else None,
                        caption_entities=entities if repost_text else None,
                        reply_markup=reply_markup,
                    )
                elif message.voice:
                    sent_message = await client.send_voice(
                        chat_id=chat_id,
                        voice=message.voice.file_id,
                        caption=repost_text if repost_text else None,
                        caption_entities=entities if repost_text else None,
                        reply_markup=reply_markup,
                    )
                elif message.video_note:
                    sent_message = await client.send_video_note(
                        chat_id=chat_id,
                        video_note=message.video_note.file_id,
                        reply_markup=reply_markup,
                    )
                elif message.sticker:
                    sent_message = await client.send_sticker(
                        chat_id=chat_id,
                        sticker=message.sticker.file_id,
                        reply_markup=reply_markup,
                    )
                else:
                    # Fallback: copy message
                    sent_message = await message.copy(
                        chat_id=chat_id,
                        caption=repost_text if repost_text else None,
                        caption_entities=entities if repost_text else None,
                        reply_markup=reply_markup,
                    )
            else:
                # Repost text only
                sent_message = await client.send_message(
                    chat_id=chat_id,
                    text=repost_text,
                    entities=entities,
                    reply_markup=reply_markup,
                )
            
            logger.info(f"Reposted message {message.id} as {sent_message.id}")
            
            # Save hash
            await asyncio.to_thread(
                PostHash.objects.create,
                group=group,
                message_hash=message_hash
            )
            
            # Update counters
            if original_sender_id:
                author_count.posts_today += 1
                author_count.posts_this_week += 1
                await asyncio.to_thread(author_count.save)
            
            # Handle forward - send invite
            if forward_from_id and forward_from_id != original_sender_id:
                await self.send_invite_message(
                    group,
                    forward_from_id,
                    sent_message,
                    message.chat
                )
            
            # Track for pending invites
            if original_sender_id:
                await asyncio.to_thread(
                    self.track_author_for_invite,
                    group,
                    original_sender_id
                )
        
        except Exception as e:
            logger.exception(f"Error reposting message: {e}")
    
    async def send_invite_message(
        self,
        group: NormalizerGroup,
        user_id: int,
        new_message: Message,
        chat
    ):
        """Send private message to original author with invite template."""
        if not group.invite_enabled:
            return
        
        try:
            # Format invite text
            # Convert chat_id to string for link (remove -100 prefix for public links)
            chat_id_str = str(chat.id)
            if chat_id_str.startswith('-100'):
                chat_id_str = chat_id_str[4:]
            
            post_link = f"https://t.me/c/{chat_id_str}/{new_message.id}"
            rules_link = f"https://t.me/c/{chat_id_str}/1"  # Adjust rules link as needed
            
            # Get author info if available
            try:
                user = await self.client.get_users(user_id)
                author_name = user.first_name or 'Пользователь'
                author_username = user.username or ''
            except Exception:
                author_name = 'Пользователь'
                author_username = ''
            
            invite_text = group.invite_text.format(
                author_name=author_name,
                author_username=author_username,
                group_name=chat.title or 'группа',
                post_link=post_link,
                rules_link=rules_link
            )
            
            await self.client.send_message(
                chat_id=user_id,
                text=invite_text
            )
            
            logger.info(f"Sent invite message to user {user_id}")
        
        except Exception as e:
            logger.error(f"Error sending invite to user {user_id}: {e}")
    
    def track_author_for_invite(self, group: NormalizerGroup, user_id: int):
        """Track author for potential invite after 7 days."""
        PendingInvite.objects.get_or_create(
            group=group,
            user_id=user_id,
            defaults={
                'status': 'pending',
            }
        )
