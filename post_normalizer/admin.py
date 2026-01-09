"""
Django admin configuration for post_normalizer app.
"""
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils import timezone
from django.db.models import Count, Q
from .models import (
    NormalizerGroup,
    AuthorPostCount,
    PostHash,
    PendingInvite,
    OldPostsNormalization
)


class OldPostsNormalizationInline(admin.TabularInline):
    """Inline admin for OldPostsNormalization."""
    model = OldPostsNormalization
    extra = 0
    max_num = 1
    can_delete = False
    
    fields = [
        'batch_size',
        'total_messages',
        'processed_messages',
        'progress_display',
        'last_run_at',
        'is_running',
        'action_button',
    ]
    
    readonly_fields = [
        'progress_display',
        'last_run_at',
        'is_running',
        'action_button',
    ]
    
    def progress_display(self, obj: OldPostsNormalization) -> str:
        """Display progress bar."""
        if not obj.pk:
            return '-'
        
        progress = obj.progress_percent
        color = '#00aa00' if progress == 100 else '#0066cc'
        
        return format_html(
            '<div style="width: 200px; background: #f0f0f0; border-radius: 3px; overflow: hidden;">'
            '<div style="width: {}%; background: {}; height: 20px; text-align: center; line-height: 20px; color: white; font-size: 0.85em;">'
            '{:.1f}%</div></div>',
            progress,
            color,
            progress
        )
    progress_display.short_description = 'Прогресс'
    
    def action_button(self, obj: OldPostsNormalization) -> str:
        """Display action button."""
        if not obj.pk:
            return '-'
        
        if obj.is_running:
            return format_html(
                '<span style="color: #00aa00;">Выполняется...</span>'
            )
        
        return format_html(
            '<a href="#" onclick="alert(\'Используйте management команду для запуска\'); return false;" '
            'style="background: #0066cc; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px;">'
            'Запустить</a>'
        )
    action_button.short_description = 'Действие'


@admin.register(NormalizerGroup)
class NormalizerGroupAdmin(admin.ModelAdmin):
    """Admin interface for NormalizerGroup model."""
    
    inlines = [OldPostsNormalizationInline]
    """Admin interface for NormalizerGroup model."""
    
    list_display = [
        'chat_id',
        'order',
        'country',
        'category',
        'owner',
        'type',
        'subscribers_count',
        'is_active',
        'custom_signature',
        'custom_buttons',
        'custom_posts_count',
        'custom_actions',
    ]
    
    list_filter = [
        'is_active',
        'type',
        'country',
        'category',
        'invite_enabled',
    ]
    
    search_fields = [
        'chat_id',
        'country',
        'category',
        'owner',
        'tags',
    ]
    
    list_editable = [
        'order',
        'is_active',
    ]
    
    fieldsets = (
        ('Основная информация', {
            'fields': (
                'chat_id',
                'order',
                'is_active',
            )
        }),
        ('Организация', {
            'fields': (
                'country',
                'category',
                'owner',
                'type',
                'tags',
                'subscribers_count',
            )
        }),
        ('Настройки задержки и лимитов', {
            'fields': (
                'delay_seconds',
                'limit_posts_day',
                'limit_posts_week',
            )
        }),
        ('Настройки поста', {
            'fields': (
                'suffix_text',
                'buttons_count',
                'button1_text',
                'button2_text',
            )
        }),
        ('Настройки приглашений', {
            'fields': (
                'invite_enabled',
                'invite_text',
                'invite_bot_username',
            )
        }),
        ('Метаданные', {
            'fields': (
                'created_at',
                'updated_at',
            ),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ['created_at', 'updated_at']
    
    def custom_signature(self, obj: NormalizerGroup) -> str:
        """Display suffix text preview."""
        if obj.suffix_text:
            preview = obj.suffix_text[:50] + '...' if len(obj.suffix_text) > 50 else obj.suffix_text
            return format_html('<span title="{}">{}</span>', obj.suffix_text, preview)
        return '-'
    custom_signature.short_description = 'Подпись'
    
    def custom_buttons(self, obj: NormalizerGroup) -> str:
        """Display button configuration."""
        buttons = []
        if obj.buttons_count >= 1:
            buttons.append(obj.button1_text or 'Кнопка 1')
        if obj.buttons_count >= 2:
            buttons.append(obj.button2_text or 'Кнопка 2')
        
        if buttons:
            return format_html(
                '<span style="color: #0066cc;">{}</span>',
                ' + '.join(buttons)
            )
        return format_html('<span style="color: #999;">Нет кнопок</span>')
    custom_buttons.short_description = 'Кнопки'
    
    def custom_posts_count(self, obj: NormalizerGroup) -> str:
        """Display post statistics."""
        today = timezone.now().date()
        week_ago = today - timezone.timedelta(days=7)
        
        # Count posts from PostHash in last 7 days
        posts_count = PostHash.objects.filter(
            group=obj,
            created_at__gte=timezone.now() - timezone.timedelta(days=7)
        ).count()
        
        return format_html(
            '<strong>{}</strong> <span style="color: #666; font-size: 0.9em;">(7 дней)</span>',
            posts_count
        )
    custom_posts_count.short_description = 'Постов'
    
    def custom_actions(self, obj: NormalizerGroup) -> str:
        """Display action buttons."""
        old_posts = OldPostsNormalization.objects.filter(group=obj).first()
        if old_posts:
            progress = old_posts.progress_percent
            status = 'running' if old_posts.is_running else 'idle'
            last_run = old_posts.last_run_at.strftime('%d.%m.%Y %H:%M') if old_posts.last_run_at else 'Никогда'
            
            return format_html(
                '<div style="font-size: 0.85em;">'
                '<div>Статус: <span style="color: {};">{}</span></div>'
                '<div>Прогресс: <strong>{:.1f}%</strong></div>'
                '<div>Запуск: {}</div>'
                '</div>',
                '#00aa00' if status == 'running' else '#666',
                'Выполняется' if status == 'running' else 'Остановлен',
                progress,
                last_run
            )
        return format_html('<span style="color: #999;">Не настроено</span>')
    custom_actions.short_description = 'Действия'
    
    actions = ['start_old_posts_normalization']
    
    def start_old_posts_normalization(self, request, queryset):
        """Action to start old posts normalization for selected groups."""
        from django.contrib import messages
        
        count = 0
        for group in queryset:
            old_posts, created = OldPostsNormalization.objects.get_or_create(
                group=group,
                defaults={
                    'batch_size': 100,
                    'total_messages': 0,
                    'processed_messages': 0,
                }
            )
            if not old_posts.is_running:
                # This will be handled by the management command
                old_posts.is_running = True
                old_posts.last_run_at = timezone.now()
                old_posts.save()
                count += 1
        
        self.message_user(
            request,
            f'Запущена нормализация старых постов для {count} групп. '
            'Используйте management команду для обработки.'
        )
    start_old_posts_normalization.short_description = 'Запустить нормализацию старых постов'


@admin.register(AuthorPostCount)
class AuthorPostCountAdmin(admin.ModelAdmin):
    """Admin interface for AuthorPostCount model."""
    
    list_display = [
        'group',
        'user_id',
        'posts_today',
        'posts_this_week',
        'last_day_reset',
        'last_week_reset',
    ]
    
    list_filter = [
        'group',
        'last_day_reset',
        'last_week_reset',
    ]
    
    search_fields = [
        'user_id',
        'group__chat_id',
    ]
    
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Основная информация', {
            'fields': (
                'group',
                'user_id',
            )
        }),
        ('Счетчики', {
            'fields': (
                'posts_today',
                'posts_this_week',
                'last_day_reset',
                'last_week_reset',
            )
        }),
        ('Метаданные', {
            'fields': (
                'created_at',
                'updated_at',
            ),
            'classes': ('collapse',)
        }),
    )


@admin.register(PostHash)
class PostHashAdmin(admin.ModelAdmin):
    """Admin interface for PostHash model."""
    
    list_display = [
        'group',
        'message_hash_short',
        'created_at',
    ]
    
    list_filter = [
        'group',
        'created_at',
    ]
    
    search_fields = [
        'message_hash',
        'group__chat_id',
    ]
    
    readonly_fields = ['created_at']
    
    def message_hash_short(self, obj: PostHash) -> str:
        """Display shortened hash."""
        return f"{obj.message_hash[:16]}...{obj.message_hash[-8:]}"
    message_hash_short.short_description = 'Хеш'
    
    def get_queryset(self, request):
        """Filter out old hashes (older than 3 days) by default."""
        qs = super().get_queryset(request)
        three_days_ago = timezone.now() - timezone.timedelta(days=3)
        return qs.filter(created_at__gte=three_days_ago)


@admin.register(PendingInvite)
class PendingInviteAdmin(admin.ModelAdmin):
    """Admin interface for PendingInvite model."""
    
    list_display = [
        'group',
        'user_id',
        'status',
        'added_at',
        'invited_at',
        'days_since_added',
    ]
    
    list_filter = [
        'status',
        'group',
        'added_at',
    ]
    
    search_fields = [
        'user_id',
        'group__chat_id',
    ]
    
    readonly_fields = ['added_at']
    
    def days_since_added(self, obj: PendingInvite) -> int:
        """Calculate days since added."""
        delta = timezone.now() - obj.added_at
        return delta.days
    days_since_added.short_description = 'Дней с добавления'
    
    fieldsets = (
        ('Основная информация', {
            'fields': (
                'group',
                'user_id',
                'status',
            )
        }),
        ('Временные метки', {
            'fields': (
                'added_at',
                'invited_at',
            )
        }),
    )


@admin.register(OldPostsNormalization)
class OldPostsNormalizationAdmin(admin.ModelAdmin):
    """Admin interface for OldPostsNormalization model."""
    
    list_display = [
        'group',
        'batch_size',
        'total_messages',
        'processed_messages',
        'progress_display',
        'is_running',
        'last_run_at',
    ]
    
    list_filter = [
        'is_running',
        'group',
        'last_run_at',
    ]
    
    search_fields = [
        'group__chat_id',
    ]
    
    readonly_fields = [
        'created_at',
        'updated_at',
        'progress_display',
    ]
    
    def progress_display(self, obj: OldPostsNormalization) -> str:
        """Display progress percentage."""
        progress = obj.progress_percent
        return format_html(
            '<strong>{:.1f}%</strong>',
            progress
        )
    progress_display.short_description = 'Прогресс'
    
    fieldsets = (
        ('Основная информация', {
            'fields': (
                'group',
                'batch_size',
            )
        }),
        ('Прогресс', {
            'fields': (
                'total_messages',
                'processed_messages',
                'progress_display',
            )
        }),
        ('Статус', {
            'fields': (
                'is_running',
                'last_run_at',
            )
        }),
        ('Метаданные', {
            'fields': (
                'created_at',
                'updated_at',
            ),
            'classes': ('collapse',)
        }),
    )
