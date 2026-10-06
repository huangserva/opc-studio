"""Local publishing preparation entry point. Does not publish remotely."""
import argparse
import json
import sys
from pathlib import Path

def main():
    if len(sys.argv)>1 and sys.argv[1]=='article':
        from .core.article_package import main as article_main
        sys.argv.pop(1)
        return article_main()
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('plan')
    q.add_argument('--workspace',default='workspace')
    q.add_argument('--slug',required=True)
    q.add_argument('--version',required=True)
    q.add_argument('--platform',required=True)
    q.add_argument('--account');q.add_argument('--channel-id')
    q.add_argument('--media-urls',help='JSON file mapping local asset names to public HTTPS URLs')
    q.add_argument('--buffer-input-out',help='Write Buffer draft JSON if all requirements are met')
    sub.add_parser('capabilities')
    q=sub.add_parser('buffer-check')
    q.add_argument('--input',required=True)
    q.add_argument('--executable',default=None)
    args=p.parse_args()
    try:
        if args.command=='capabilities':
            from .registry import capabilities
            result=capabilities()
        elif args.command=='buffer-check':
            from .transports.buffer_cli import validate
            result=validate(json.loads(Path(args.input).read_text()),[args.executable] if args.executable else None)
        else:
            from server import Store
            store=Store(args.workspace)
            options={'account':args.account,'channel_id':args.channel_id,
                     'media_urls':json.loads(Path(args.media_urls).read_text()) if args.media_urls else {}}
            result=store.publication_plan(args.slug,{'platform':args.platform,'version':args.version,'options':options})
            if args.buffer_input_out:
                if not result.get('buffer_input'):
                    raise ValueError('发布条件尚未满足，未生成 Buffer 请求：'+'；'.join(result.get('blocking',[])))
                output=Path(args.buffer_input_out)
                # Never overwrite a previously reviewed payload by accident.
                with output.open('x',encoding='utf-8') as f:
                    json.dump(result['buffer_input'],f,ensure_ascii=False,indent=2)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (ValueError,OSError,KeyError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr);return 1

if __name__=='__main__':sys.exit(main())
