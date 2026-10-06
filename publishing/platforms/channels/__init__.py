from .. import manual

def capability(kind):
    return manual.capability('channels',kind,'视频号')

def prepare(snapshot,options):
    return manual.prepare(snapshot,options,'视频号')
