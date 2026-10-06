"""Optional local display preferences; the repository carries no owner identity."""
import os


def user_name():
    value=os.environ.get('ARTICLE_STUDIO_USER_NAME','').strip()
    if not value or len(value)>40 or any(ord(c)<32 or ord(c)==127 for c in value):
        return '用户'
    return value
