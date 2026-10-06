from .. import manual

def capability(kind):
    return manual.capability('xiaohongshu',kind,'小红书')

def prepare(snapshot,options):
    return manual.prepare(snapshot,options,'小红书')
