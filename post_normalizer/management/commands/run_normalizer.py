"""
Django management command to run Telegram message normalizer.

Usage:
    python manage.py run_normalizer
"""
import asyncio
import random
import logging
import os
from typing import Optional

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from pyrogram import Client
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram import filters

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
    
    def handle(self, *args, **options):
        """Main entry point."""
        # Load API credentials from environment
        try:
            api_id = int(os.environ.get('TELEGRAM_API_ID'))
            api_hash = os.environ.get('TELEGRAM_API_HASH')
            session_name = os.environ.get('TELEGRAM_SESSION_NAME', 'normalizer_session')
        except (ValueError, TypeError):
            raise CommandError(
                'TELEGRAM_API_ID must be a valid integer. '
                'Set TELEGRAM_API_ID and TELEGRAM_API_HASH environment variables.'
            )
        except KeyError:
            raise CommandError(
                'Set TELEGRAM_API_ID and TELEGRAM_API_HASH environment variables.'
            )
        
        if not api_hash:
            raise CommandError('TELEGRAM_API_HASH environment variable is not set.')
        
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
            api_id=api_id,
            api_hash=api_hash,
            workdir='.'
        )
        
        # Get active groups
        active_chat_ids = list(
            NormalizerGroup.objects.filter(is_active=True).values_list('chat_id', flat=True)
        )
        
        if not active_chat_ids:
            self.stdout.write(self.style.WARNING('No active groups found. Exiting.'))
            return
        
        # Create filter for active chats
        active_chats_filter = filters.chat(active_chat_ids) & filters.group
        
        # Register message handler
        self.client.on_message(active_chats_filter)(self.on_message)
        
        # Start client
        await self.client.start()
        self.stdout.write(self.style.SUCCESS('Normalizer running...'))
        
        # Keep running
        await self.client.idle()
    
    async def on_message(self, client: Client, message: Message):
        """Handle new messages in configured groups."""
        try:
            # Get group configuration
            try:
                group = await asyncio.to_thread(
                    NormalizerGroup.objects.get,
                    chat_id=message.chat.id
                )
            except NormalizerGroup.DoesNotExist:
                return
            
            # Skip if message is from the bot itself
            if message.from_user and message.from_user.is_self:
                return
            
            # Skip service messages without content
            if not message.text and not message.caption and not message.media:
                return
            
            # Random delay
            delay = random.uniform(group.delay_seconds, group.delay_seconds + 120)
            await asyncio.sleep(delay)
            
            # Reload message to check if it still exists
            try:
                reloaded = await client.get_messages(message.chat.id, message.id)
                if reloaded is None or reloaded.empty:
                    return
            except Exception:
                logger.info(f"Message {message.id} no longer exists, skipping")
                return
            
            # Compute media_file_id
            media_file_id = None
            if message.photo:
                media_file_id = message.photo.file_id
            elif message.video:
                media_file_id = message.video.file_id
            elif message.document:
                media_file_id = message.document.file_id
            
            # Compute hash
            text_for_hash = message.text or message.caption or ''
            message_hash = PostHash.create_hash(text_for_hash, media_file_id)
            
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
            
            # Get sender ID (handle channel posts)
            if message.from_user:
                sender_id = message.from_user.id
            elif message.sender_chat:
                sender_id = message.sender_chat.id
            else:
                logger.warning(f"Message {message.id} has no sender, skipping")
                return
            
            # Get or create AuthorPostCount
            author_count, created = await asyncio.to_thread(
                AuthorPostCount.objects.get_or_create,
                group=group,
                user_id=sender_id,
                defaults={
                    'posts_today': 0,
                    'posts_this_week': 0,
                }
            )
            
            # Reset counters if needed
            await asyncio.to_thread(author_count.reset_if_needed)
            
            # Check limits
            if group.limit_posts_day > 0 and author_count.posts_today >= group.limit_posts_day:
                logger.info(f"Day limit reached for user {sender_id}")
                try:
                    await message.delete()
                    # Optional: send private warning
                    if message.from_user:
                        try:
                            await client.send_message(
                                sender_id,
                                f"Достигнут дневной лимит постов ({group.limit_posts_day})"
                            )
                        except Exception:
                            pass
                except Exception as e:
                    logger.error(f"Error deleting message with limit: {e}")
                return
            
            if group.limit_posts_week > 0 and author_count.posts_this_week >= group.limit_posts_week:
                logger.info(f"Week limit reached for user {sender_id}")
                try:
                    await message.delete()
                    # Optional: send private warning
                    if message.from_user:
                        try:
                            await client.send_message(
                                sender_id,
                                f"Достигнут недельный лимит постов ({group.limit_posts_week})"
                            )
                        except Exception:
                            pass
                except Exception as e:
                    logger.error(f"Error deleting message with limit: {e}")
                return
            
            # Increment counters
            author_count.posts_today += 1
            author_count.posts_this_week += 1
            await asyncio.to_thread(author_count.save)
            
            # Delete original message
            try:
                await message.delete()
            except Exception as e:
                logger.error(f"Error deleting original message: {e}")
                return
            
            # Prepare repost text
            repost_text = (message.text or message.caption or '') + group.suffix_text
            if not repost_text.strip():
                repost_text = group.suffix_text
            
            # Prepare entities
            entities = message.entities or message.caption_entities or []
            
            # Create inline keyboard
            reply_markup = None
            if group.buttons_count > 0 and message.from_user:
                button_text = group.get_button_text(random.randint(0, 100))
                reply_markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        text=button_text,
                        url=f"tg://user?id={sender_id}"
                    )
                ]])
            
            # Handle media group (albums)
            if message.media_group_id:
                # For albums, we need to collect all messages in the group
                # This is a simplified version - in production you might want to cache and process together
                logger.info(f"Media group detected: {message.media_group_id}")
                # For now, just repost the single message
                pass
            
            # Repost message
            try:
                new_message = await client.send_message(
                    chat_id=message.chat.id,
                    text=repost_text,
                    entities=entities,
                    disable_web_page_preview=True,
                    reply_markup=reply_markup,
                    photo=message.photo.file_id if message.photo else None,
                    video=message.video.file_id if message.video else None,
                    document=message.document.file_id if message.document else None,
                )
                
                logger.info(f"Reposted message {message.id} as {new_message.id}")
                
                # Save hash
                await asyncio.to_thread(
                    PostHash.objects.create,
                    group=group,
                    message_hash=message_hash
                )
                
                # Handle forwards
                original_author_id = None
                if message.forward_from:
                    original_author_id = message.forward_from.id
                elif message.forward_from_chat:
                    original_author_id = message.forward_from_chat.id
                
                if original_author_id and group.invite_enabled:
                    # Render invite text
                    author_name = message.forward_from.first_name if message.forward_from else ''
                    author_username = message.forward_from.username if message.forward_from else ''
                    
                    # Get message link
                    chat_id_str = str(message.chat.id)
                    if chat_id_str.startswith('-100'):
                        chat_id_str = chat_id_str[4:]
                    post_link = f"https://t.me/c/{chat_id_str}/{new_message.id}"
                    rules_link = 'https://example.com/rules'  # Placeholder, make configurable
                    
                    rendered_text = group.invite_text.format(
                        author_name=author_name,
                        author_username=author_username,
                        group_name=message.chat.title or 'группа',
                        post_link=post_link,
                        rules_link=rules_link
                    )
                    
                    # Send invite message
                    try:
                        await client.send_message(original_author_id, rendered_text)
                        logger.info(f"Sent invite message to user {original_author_id}")
                    except Exception as e:
                        logger.warning(f"Could not send invite to {original_author_id}: {e}")
                    
                    # Check if user is member and add to PendingInvite if not
                    try:
                        chat_member = await client.get_chat_member(message.chat.id, original_author_id)
                        if chat_member.status not in ['member', 'administrator', 'creator']:
                            await asyncio.to_thread(
                                PendingInvite.objects.get_or_create,
                                group=group,
                                user_id=original_author_id,
                                defaults={'status': 'pending'}
                            )
                    except Exception:
                        # User is not a member, add to pending invites
                        await asyncio.to_thread(
                            PendingInvite.objects.get_or_create,
                            group=group,
                            user_id=original_author_id,
                            defaults={'status': 'pending'}
                        )
            
            except Exception as e:
                logger.exception(f"Error reposting message: {e}")
        
        except Exception as e:
            logger.exception(f"Error in message handler: {e}")
