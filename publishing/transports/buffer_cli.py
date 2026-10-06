"""Local validation only; no login, credentials, scheduling or remote mutation."""
import json
import subprocess
import shutil

def validate(payload, command=None):
    if payload.get('saveToDraft') is not True:
        raise ValueError('当前适配器仅接受明确的草稿请求')
    if command is None:
        command=['buffer'] if shutil.which('buffer') else ['npx','--yes','@bufferapp/cli@0.5.2']
    if not command or isinstance(command,str):raise ValueError('CLI 命令必须是参数列表')
    try:
        result=subprocess.run([*command,'posts','create','--input','-',
                               '--dry-run','--output','json','--quiet'],
                              input=json.dumps(payload,ensure_ascii=False),text=True,
                              capture_output=True,timeout=30,check=False)
    except FileNotFoundError:
        raise ValueError('未找到官方 Buffer CLI，请配置 @bufferapp/cli') from None
    except subprocess.TimeoutExpired:
        raise ValueError('Buffer 本地校验超时，未提交在线请求') from None
    if result.returncode:
        raise ValueError('Buffer 本地校验未通过，请检查请求格式与 CLI 版本')
    return {'status':'validated_locally','published':False,'remote_request_sent':False}
