"""
Models for post_normalizer app.

This module contains all database models for managing Telegram message normalization.
"""
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from datetime import timedelta
import hashlib
import json
from typing import Optional


class NormalizerGroup(models.Model):
    """
    Configuration for a Telegram supergroup where message normalization is active.
    """
    # Basic identification
    chat_id = models.BigIntegerField(
        unique=True,
        verbose_name='Chat ID',
        help_text='Telegram chat ID (negative for groups)'
    )
    
    # Display and organization fields
    order = models.IntegerField(
        default=0,
        verbose_name='Порядок',
        help_text='Порядок сортировки'
    )
    country = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Страна'
    )
    category = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Категория'
    )
    owner = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Владелец'
    )
    
    TYPE_CHOICES = [
        ('own', 'Свой'),
        ('other', 'Чужой'),
    ]
    type = models.CharField(
        max_length=10,
        choices=TYPE_CHOICES,
        default='other',
        verbose_name='Тип'
    )
    
    tags = models.CharField(
        max_length=500,
        blank=True,
        verbose_name='Теги',
        help_text='Через запятую'
    )
    subscribers_count = models.IntegerField(
        default=0,
        verbose_name='Подписчиков',
        validators=[MinValueValidator(0)]
    )
    
    # Activation
    is_active = models.BooleanField(
        default=True,
        verbose_name='Активен'
    )
    
    # Timing configuration
    delay_seconds = models.IntegerField(
        default=180,
        verbose_name='Задержка (секунды)',
        help_text='Случайная задержка от delay_seconds до delay_seconds + 120',
        validators=[MinValueValidator(1)]
    )
    
    # Limits
    limit_posts_day = models.IntegerField(
        default=0,
        verbose_name='Лимит постов в день',
        help_text='0 = без ограничений',
        validators=[MinValueValidator(0)]
    )
    limit_posts_week = models.IntegerField(
        default=0,
        verbose_name='Лимит постов в неделю',
        help_text='0 = без ограничений',
        validators=[MinValueValidator(0)]
    )
    
    # Post customization
    suffix_text = models.TextField(
        blank=True,
        verbose_name='Суффикс текста',
        help_text='Текст, добавляемый в конец каждого поста'
    )
    
    # Button configuration
    BUTTON_COUNT_CHOICES = [
        (0, '0 кнопок'),
        (1, '1 кнопка'),
        (2, '2 кнопки'),
    ]
    buttons_count = models.IntegerField(
        default=1,
        choices=BUTTON_COUNT_CHOICES,
        verbose_name='Количество кнопок'
    )
    button_rotation_texts = models.JSONField(
        default=list,
        verbose_name='Варианты текста кнопки (ротация)',
        help_text='JSON список строк для циклической ротации кнопок, например: ["Обратная связь", "Автор поста", ...]'
    )
    button2_text = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Текст кнопки 2'
    )
    
    # Invite configuration
    invite_enabled = models.BooleanField(
        default=True,
        verbose_name='Включить приглашения'
    )
    invite_text = models.TextField(
        default='Привет, {author_name} (@{author_username})!\nВаш пост успешно опубликован в группе {group_name}.\n\nСсылка на пост: {post_link}\n\nПравила группы: {rules_link}\nЧаще публикуйтесь у нас самостоятельно :)',
        verbose_name='Текст приглашения',
        help_text='Шаблон для личного сообщения. Переменные: {group_name}, {post_link}, {rules_link}'
    )
    invite_bot_username = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Username бота для приглашений',
        help_text='Опционально, если нужен бот для отправки приглашений'
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлен')
    
    class Meta:
        verbose_name = 'Группа нормализатора'
        verbose_name_plural = 'Группы нормализаторов'
        ordering = ['order', 'chat_id']
    
    def __str__(self) -> str:
        return f"Group {self.chat_id} ({'Active' if self.is_active else 'Inactive'})"
    
    def clean(self):
        from django.core.exceptions import ValidationError
        if self.chat_id >= 0:
            raise ValidationError({'chat_id': 'Chat ID for supergroups must be negative.'})
    
    def get_button_text(self, index: int = 0) -> str:
        default_cycle = ['Обратная связь', 'Автор поста', 'Связаться с автором', 'Контакты автора']
        cycle = self.button_rotation_texts or default_cycle
        return cycle[index % len(cycle)]


class AuthorPostCount(models.Model):
    """
    Tracks post counts per author per group for day/week limits.
    """
    group = models.ForeignKey(
        NormalizerGroup,
        on_delete=models.CASCADE,
        related_name='author_counts',
        verbose_name='Группа'
    )
    user_id = models.BigIntegerField(
        verbose_name='User ID',
        db_index=True
    )
    
    # Counters
    posts_today = models.IntegerField(
        default=0,
        verbose_name='Постов сегодня',
        validators=[MinValueValidator(0)]
    )
    posts_this_week = models.IntegerField(
        default=0,
        verbose_name='Постов на этой неделе',
        validators=[MinValueValidator(0)]
    )
    
    # Timestamps for reset
    last_day_reset = models.DateField(
        default=timezone.now,
        verbose_name='Последний сброс дня'
    )
    last_week_reset = models.DateField(
        default=timezone.now,
        verbose_name='Последний сброс недели'
    )
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлен')
    
    class Meta:
        verbose_name = 'Счетчик постов автора'
        verbose_name_plural = 'Счетчики постов авторов'
        unique_together = [['group', 'user_id']]
        indexes = [
            models.Index(fields=['group', 'user_id']),
            models.Index(fields=['last_day_reset', 'last_week_reset']),
        ]
    
    def __str__(self) -> str:
        return f"User {self.user_id} in Group {self.group.chat_id}: {self.posts_today}/day, {self.posts_this_week}/week"
    
    def reset_if_needed(self) -> None:
        """Reset counters if day/week has changed."""
        now = timezone.now()
        today = now.date()
        
        if self.last_day_reset < today:
            self.posts_today = 0
            self.last_day_reset = today
        
        # Week reset (ISO week comparison)
        current_week, current_year = now.isocalendar()[:2]
        last_reset_week, last_reset_year = self.last_week_reset.isocalendar()[:2]
        if current_year > last_reset_year or current_week != last_reset_week:
            self.posts_this_week = 0
            self.last_week_reset = today
        
        self.save(update_fields=['posts_today', 'posts_this_week', 'last_day_reset', 'last_week_reset'])


class PostHash(models.Model):
    """
    Stores message hashes for duplicate detection.
    Hashes are kept for 3 days, then cleaned up.
    """
    group = models.ForeignKey(
        NormalizerGroup,
        on_delete=models.CASCADE,
        related_name='post_hashes',
        verbose_name='Группа'
    )
    message_hash = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name='Хеш сообщения',
        help_text='SHA256 hash of text + media'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Создан',
        db_index=True
    )
    
    # TODO: Добавить celery beat задачу или management command для удаления записей старше 4 дней
    class Meta:
        verbose_name = 'Хеш поста'
        verbose_name_plural = 'Хеши постов'
        unique_together = [['group', 'message_hash']]
        indexes = [
            models.Index(fields=['group', 'message_hash', 'created_at']),
        ]
    
    def __str__(self) -> str:
        return f"Hash {self.message_hash[:16]}... in Group {self.group.chat_id}"
    
    @staticmethod
    def create_hash(text: str, media_file_id: Optional[str] = None) -> str:
        """
        Create SHA256 hash from message text and media.
        
        Args:
            text: Message text/caption
            media_file_id: Telegram file_id if media exists
            
        Returns:
            SHA256 hash string
        """
        content = f"{text or ''}|{media_file_id or ''}"
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    @staticmethod
    def is_duplicate(group: NormalizerGroup, message_hash: str) -> bool:
        """
        Check if message hash exists in last 3 days for this group.
        
        Args:
            group: NormalizerGroup instance
            message_hash: SHA256 hash to check
            
        Returns:
            True if duplicate found
        """
        three_days_ago = timezone.now() - timedelta(days=3)
        return PostHash.objects.filter(
            group=group,
            message_hash=message_hash,
            created_at__gte=three_days_ago
        ).exists()


class PendingInvite(models.Model):
    """
    Tracks users who need to be invited to groups after 7 days.
    """
    group = models.ForeignKey(
        NormalizerGroup,
        on_delete=models.CASCADE,
        related_name='pending_invites',
        verbose_name='Группа'
    )
    user_id = models.BigIntegerField(
        verbose_name='User ID',
        db_index=True
    )
    added_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Добавлен',
        db_index=True
    )
    
    STATUS_CHOICES = [
        ('pending', 'Ожидает'),
        ('invited', 'Приглашен'),
        ('joined', 'Присоединился'),
        ('skipped', 'Пропущен'),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Статус'
    )
    
    invited_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Приглашен'
    )
    
    class Meta:
        verbose_name = 'Ожидающее приглашение'
        verbose_name_plural = 'Ожидающие приглашения'
        unique_together = [['group', 'user_id']]
        indexes = [
            models.Index(fields=['group', 'user_id', 'status']),
            models.Index(fields=['added_at', 'status']),
        ]
    
    def __str__(self) -> str:
        return f"User {self.user_id} → Group {self.group.chat_id} ({self.status})"


class OldPostsNormalization(models.Model):
    """
    Manages batch normalization of old messages.
    """
    group = models.ForeignKey(
        NormalizerGroup,
        on_delete=models.CASCADE,
        related_name='old_posts_batches',
        verbose_name='Группа'
    )
    
    batch_size = models.IntegerField(
        default=100,
        verbose_name='Размер батча',
        validators=[MinValueValidator(1), MaxValueValidator(1000)]
    )
    
    total_messages = models.IntegerField(
        default=0,
        verbose_name='Всего сообщений',
        validators=[MinValueValidator(0)]
    )
    processed_messages = models.IntegerField(
        default=0,
        verbose_name='Обработано сообщений',
        validators=[MinValueValidator(0)]
    )
    
    last_run_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Последний запуск'
    )
    
    is_running = models.BooleanField(
        default=False,
        verbose_name='Выполняется'
    )
    
    error_message = models.TextField(blank=True, verbose_name='Сообщение об ошибке')
    task_id = models.CharField(max_length=255, blank=True, verbose_name='ID задачи Celery')
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлен')
    
    class Meta:
        verbose_name = 'Нормализация старых постов'
        verbose_name_plural = 'Нормализации старых постов'
        ordering = ['-last_run_at']
    
    def __str__(self) -> str:
        return f"Old posts for Group {self.group.chat_id}: {self.processed_messages}/{self.total_messages}"
    
    @property
    def progress_percent(self) -> float:
        """Calculate progress percentage."""
        if self.total_messages == 0:
            return 0.0
        return min(100.0, (self.processed_messages / self.total_messages) * 100.0)
