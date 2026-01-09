"""
Django admin configuration for post_normalizer app.
"""
from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone
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
        'progress_percent_display',
        'is_running',
        'last_run_at',
    ]
    
    readonly_fields = [
        'progress_percent_display',
        'last_run_at',
        'is_running',
    ]
    
    def progress_percent_display(self, obj: OldPostsNormalization) -> str:
        """Display progress percentage."""
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
    progress_percent_display.short_description = 'Прогресс'


@admin.register(NormalizerGroup)
class NormalizerGroupAdmin(admin.ModelAdmin):
    """Admin interface for NormalizerGroup model."""
    
    inlines = [OldPostsNormalizationInline]
    
    list_display = [
        'chat_id',
        'order',
        'country',
        'category',
        'type',
        'is_active',
        'buttons_count',
        'invite_enabled',
        'subscribers_count',
        'owner',
    ]
    
    list_filter = [
        'is_active',
        'type',
        'country',
        'category',
        'invite_enabled',
        'buttons_count',
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
                'button_rotation_texts',
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
    
    actions = ['start_old_posts_normalization']
    
    def start_old_posts_normalization(self, request, queryset):
        """Действие для запуска нормализации старых постов."""
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
        """Отображение сокращенного хеша."""
        return f"{obj.message_hash[:16]}...{obj.message_hash[-8:]}"
    message_hash_short.short_description = 'Хеш'
    
    def get_queryset(self, request):
        """Фильтрация старых хешей (старше 3 дней) по умолчанию."""
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
        """Количество дней с момента добавления."""
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
        """Отображение процента выполнения."""
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
                'error_message',
                'task_id',
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
