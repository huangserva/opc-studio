"""Explicit offline adapters for platforms without a connected transport."""
def capability(platform, kind, label):
    return {'adapter':platform+'.'+kind,'transport':'manual_export','stage':'not_connected',
            'label':label+' · 导出发布包'}

def prepare(snapshot, options, label):
    return {**capability(snapshot['platform'],snapshot['studio']['type'],label),
            'status':'manual_only','blocking':['此平台尚未接通在线发布，可先导出发布包'],
            'notes':['导出包绑定选中的保存版本。公开发布后可登记平台链接。']}
