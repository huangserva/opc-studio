"""X video posts; platform restrictions remain separate from other platforms."""
from .post import prepare as prepare_post

def capability(kind):
    return {'adapter':'x.video','transport':'buffer_cli','stage':'payload_ready_only',
            'label':'X 视频帖 · Buffer CLI'}

def prepare(snapshot, options):
    return {**prepare_post(snapshot,options),**capability('video')}
