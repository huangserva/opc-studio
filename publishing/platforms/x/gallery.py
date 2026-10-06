"""X image post conversion shares Buffer serialization with ordinary posts."""
from .post import prepare as prepare_post

def capability(kind):
    return {'adapter':'x.gallery','transport':'buffer_cli','stage':'payload_ready_only',
            'label':'X 图片帖 · Buffer CLI'}

def prepare(snapshot, options):
    return {**prepare_post(snapshot,options),**capability('gallery')}
