# Telegram Post Normalizer Userbot

Django-based Telegram userbot for normalizing (reposting with modifications) messages in Telegram supergroups.

## Features

- Real-time message normalization in configured supergroups
- Configurable delay (180-300 seconds) before reposting
- Duplicate detection (by text + media hash) within last 3 days
- Anonymous reposting from group name
- Rotating inline button text (4 variants)
- Automatic invite messages for forwarded posts
- Post limits per author (day/week)
- Batch processing of old messages
- Full Django admin interface

## Requirements

- Python 3.8+
- Django 4.2+
- Pyrogram 2.0+
- PostgreSQL (recommended) or SQLite

## Installation

1. Clone the repository and install dependencies:
```bash
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and configure:
```bash
cp .env.example .env
# Edit .env with your settings
```

3. Get Telegram API credentials:
   - Go to https://my.telegram.org/apps
   - Create an application
   - Copy `api_id` and `api_hash` to `.env`

4. Run migrations:
```bash
python manage.py migrate
```

5. Create superuser:
```bash
python manage.py createsuperuser
```

6. Start Django admin:
```bash
python manage.py runserver
```

7. Configure groups in Django admin at `http://localhost:8000/admin/`

8. Run the userbot:
```bash
python manage.py run_userbot
```

## Configuration

### Adding a Group

1. Go to Django admin → Post Normalizer → Groups
2. Add new group with:
   - `chat_id`: Telegram chat ID (negative number for groups)
   - `is_active`: Enable/disable normalization
   - `delay_seconds`: Base delay (180-300 seconds random)
   - `suffix_text`: Text to append to each post
   - `buttons_count`: Number of inline buttons (0-2)
   - `button1_text`: First button text (rotates automatically)
   - `limit_posts_day/week`: Limits per author
   - `invite_enabled`: Enable invite messages
   - `invite_text`: Template for invite message

### Getting Chat ID

To get a group's chat_id:
1. Add @userinfobot to the group
2. Send `/start` in the group
3. The bot will show the chat ID (negative number)

## Usage

### Normal Operation

The userbot automatically:
1. Listens for new messages in active groups
2. Waits random delay (180-300 seconds)
3. Checks for duplicates
4. Deletes original message
5. Reposts as anonymous with button
6. Sends invite to original author (if forwarded)

### Batch Processing Old Messages

1. Go to Django admin → Groups
2. Select groups to process
3. Use action "Запустить нормализацию старых постов"
4. Run management command for processing (to be implemented)

## Models

- **NormalizerGroup**: Group configuration
- **AuthorPostCount**: Post counters per author
- **PostHash**: Message hashes for duplicate detection
- **PendingInvite**: Users to invite after 7 days
- **OldPostsNormalization**: Batch processing configuration

## Permissions Required

The userbot account must be admin in target groups with:
- Delete messages
- Post messages as group (anonymous posting)
- Invite users

## License

MIT
