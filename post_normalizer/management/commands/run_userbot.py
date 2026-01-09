"""
Management command to run the Telegram userbot.

Usage:
    python manage.py run_userbot
"""
import asyncio
import random
import logging
from typing import Optional, Dict, Any
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings

from pyrogram import Client
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ChatType, MessageEntityType

from post_normalizer.models import (
    NormalizerGroup,
    AuthorPostCount,
    PostHash,
    PendingInvite,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Run Telegram userbot for message normalization."""
    
    help = 'Run Telegram userbot for message normalization'
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client: Optional[Client] = None
        self.pending_messages: Dict[int, Dict[str, Any]] = {}
        # Rotation index for button text (cycles through 0-3)
        self.button_rotation_index: Dict[int, int] = {}
    
    def handle(self, *args, **options):
        """Main entry point."""
        self.stdout.write(self.style.SUCCESS('Starting Telegram userbot...'))
        
        # Validate settings
        if not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
            self.stdout.write(
                self.style.ERROR(
                    'ERROR: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in settings or .env file'
                )
            )
            return
        
        # Run async event loop
        try:
            asyncio.run(self.run_userbot())
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('\nUserbot stopped by user'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {e}'))
            logger.exception('Userbot error')
    
    async def run_userbot(self):
        """Initialize and run Pyrogram client."""
        self.client = Client(
            name=settings.TELEGRAM_SESSION_NAME,
            api_id=settings.TELEGRAM_API_ID,
            api_hash=settings.TELEGRAM_API_HASH,
            workdir='.'
        )
        
        # Register handlers
        self.client.on_message()(self.handle_new_message)
        self.client.on_message_deleted()(self.handle_message_deleted)
        
        # Start client
        await self.client.start()
        self.stdout.write(self.style.SUCCESS('Userbot started successfully!'))
        
        # Run background tasks
        asyncio.create_task(self.background_tasks())
        
        # Keep running
        await self.client.idle()
    
    async def handle_new_message(self, client: Client, message: Message):
        """
        Handle new messages in configured groups.
        
        Flow:
        1. Check if message is from a configured group
        2. Schedule delayed processing
        3. After delay: check duplicates, delete original, repost
        """
        # Only process messages from supergroups
        if not message.chat or message.chat.type != ChatType.SUPERGROUP:
            return
        
        chat_id = message.chat.id
        
        # Check if this group is configured and active
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
        
        # Skip service messages
        if not message.text and not message.caption and not message.media:
            return
        
        # Calculate random delay (delay_seconds to delay_seconds + 120)
        delay = random.randint(
            group.delay_seconds,
            group.delay_seconds + 120
        )
        
        # Store message info for delayed processing
        message_key = f"{chat_id}_{message.id}"
        self.pending_messages[message_key] = {
            'group': group,
            'message': message,
            'scheduled_at': timezone.now() + timedelta(seconds=delay),
            'original_sender_id': message.from_user.id if message.from_user else None,
            'forward_from_id': message.forward_from.id if message.forward_from else None,
        }
        
        logger.info(
            f"Scheduled message {message.id} from chat {chat_id} "
            f"for processing in {delay} seconds"
        )
        
        # Schedule async task for delayed processing
        asyncio.create_task(
            self.process_message_after_delay(message_key, delay)
        )
    
    async def process_message_after_delay(self, message_key: str, delay: int):
        """Process message after delay."""
        await asyncio.sleep(delay)
        
        if message_key not in self.pending_messages:
            # Message was deleted during delay
            return
        
        message_data = self.pending_messages.pop(message_key)
        group = message_data['group']
        message = message_data['message']
        
        try:
            await self.normalize_message(group, message, message_data)
        except Exception as e:
            logger.exception(f"Error normalizing message {message.id}: {e}")
    
    async def handle_message_deleted(self, client: Client, messages):
        """Handle deleted messages - remove from pending queue."""
        for msg in messages:
            if isinstance(msg, Message):
                message_key = f"{msg.chat.id}_{msg.id}"
                if message_key in self.pending_messages:
                    del self.pending_messages[message_key]
                    logger.info(f"Message {msg.id} deleted, removed from queue")
    
    async def normalize_message(
        self,
        group: NormalizerGroup,
        message: Message,
        message_data: Dict[str, Any]
    ):
        """
        Main normalization logic:
        1. Check for duplicates
        2. Check limits
        3. Delete original
        4. Repost as anonymous
        5. Send invite if needed
        """
        chat_id = message.chat.id
        original_sender_id = message_data['original_sender_id']
        forward_from_id = message_data['forward_from_id']
        
        # 1. Check if message still exists
        try:
            await message.get()
        except Exception:
            logger.info(f"Message {message.id} no longer exists, skipping")
            return
        
        # 2. Create message hash and check for duplicates
        text_content = message.text or message.caption or ''
        media_file_id = None
        if message.media:
            # Get file_id from media
            if hasattr(message, 'photo') and message.photo:
                media_file_id = message.photo.file_id
            elif hasattr(message, 'video') and message.video:
                media_file_id = message.video.file_id
            elif hasattr(message, 'document') and message.document:
                media_file_id = message.document.file_id
            # Add other media types as needed
        
        message_hash = await asyncio.to_thread(
            PostHash.create_hash,
            text_content,
            media_file_id
        )
        
        # Check duplicate
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
        
        # 3. Check post limits
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
        
        # 4. Delete original message
        try:
            await message.delete()
        except Exception as e:
            logger.error(f"Error deleting original message: {e}")
            return
        
        # 5. Prepare repost content
        repost_text = text_content
        if group.suffix_text:
            repost_text = f"{repost_text}\n\n{group.suffix_text}" if repost_text else group.suffix_text
        
        # Prepare entities (formatting)
        entities = message.entities or message.caption_entities or []
        
        # 6. Create inline keyboard with button
        keyboard = []
        if group.buttons_count >= 1 and original_sender_id:
            # Get rotation index for this group
            rotation_index = self.button_rotation_index.get(chat_id, 0)
            button_text = group.get_button_text_rotation(rotation_index)
            
            # Cycle rotation for next time
            self.button_rotation_index[chat_id] = (rotation_index + 1) % 4
            
            button = InlineKeyboardButton(
                text=button_text,
                url=f"tg://user?id={original_sender_id}"
            )
            keyboard.append([button])
        
        if group.buttons_count >= 2 and group.button2_text:
            # Add second button if configured
            button2 = InlineKeyboardButton(
                text=group.button2_text,
                url="#"  # Configure URL as needed
            )
            keyboard.append([button2])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        # 7. Repost message as anonymous
        try:
            if message.media:
                # Repost with media - handle different media types
                if message.photo:
                    sent_message = await self.client.send_photo(
                        chat_id=chat_id,
                        photo=message.photo.file_id,
                        caption=repost_text if repost_text else None,
                        caption_entities=entities if repost_text else None,
                        reply_markup=reply_markup,
                    )
                elif message.video:
                    sent_message = await self.client.send_video(
                        chat_id=chat_id,
                        video=message.video.file_id,
                        caption=repost_text if repost_text else None,
                        caption_entities=entities if repost_text else None,
                        reply_markup=reply_markup,
                    )
                elif message.document:
                    sent_message = await self.client.send_document(
                        chat_id=chat_id,
                        document=message.document.file_id,
                        caption=repost_text if repost_text else None,
                        caption_entities=entities if repost_text else None,
                        reply_markup=reply_markup,
                    )
                elif message.audio:
                    sent_message = await self.client.send_audio(
                        chat_id=chat_id,
                        audio=message.audio.file_id,
                        caption=repost_text if repost_text else None,
                        caption_entities=entities if repost_text else None,
                        reply_markup=reply_markup,
                    )
                elif message.voice:
                    sent_message = await self.client.send_voice(
                        chat_id=chat_id,
                        voice=message.voice.file_id,
                        caption=repost_text if repost_text else None,
                        caption_entities=entities if repost_text else None,
                        reply_markup=reply_markup,
                    )
                elif message.video_note:
                    sent_message = await self.client.send_video_note(
                        chat_id=chat_id,
                        video_note=message.video_note.file_id,
                        reply_markup=reply_markup,
                    )
                elif message.sticker:
                    sent_message = await self.client.send_sticker(
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
                sent_message = await self.client.send_message(
                    chat_id=chat_id,
                    text=repost_text,
                    entities=entities,
                    reply_markup=reply_markup,
                )
            
            logger.info(f"Reposted message {message.id} as {sent_message.id}")
            
            # 8. Save hash
            await asyncio.to_thread(
                PostHash.objects.create,
                group=group,
                message_hash=message_hash
            )
            
            # 9. Update counters
            if original_sender_id:
                author_count.posts_today += 1
                author_count.posts_this_week += 1
                await asyncio.to_thread(author_count.save)
            
            # 10. Handle forward - send invite
            if forward_from_id and forward_from_id != original_sender_id:
                await self.send_invite_message(
                    group,
                    forward_from_id,
                    sent_message,
                    message.chat
                )
            
            # 11. Track for pending invites (check after 7 days)
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
            post_link = f"https://t.me/c/{str(chat.id)[4:]}/{new_message.id}"
            rules_link = f"https://t.me/c/{str(chat.id)[4:]}/1"  # Adjust rules link as needed
            
            invite_text = group.invite_text.format(
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
        # This will be checked by background task
        PendingInvite.objects.get_or_create(
            group=group,
            user_id=user_id,
            defaults={
                'status': 'pending',
            }
        )
    
    async def background_tasks(self):
        """Run background tasks periodically."""
        while True:
            try:
                await asyncio.sleep(3600)  # Run every hour
                await self.check_pending_invites()
            except Exception as e:
                logger.exception(f"Error in background tasks: {e}")
    
    async def check_pending_invites(self):
        """Check and process pending invites after 7 days."""
        seven_days_ago = timezone.now() - timedelta(days=7)
        
        pending = await asyncio.to_thread(
            list,
            PendingInvite.objects.filter(
                status='pending',
                added_at__lte=seven_days_ago
            ).select_related('group')
        )
        
        for invite in pending:
            try:
                # Check if user is member of the group
                chat_member = await self.client.get_chat_member(
                    chat_id=invite.group.chat_id,
                    user_id=invite.user_id
                )
                
                # User is already a member
                if chat_member.status in ['member', 'administrator', 'creator']:
                    invite.status = 'joined'
                    await asyncio.to_thread(invite.save)
                    continue
            
            except Exception:
                # User is not a member, keep as pending for invite
                pass
