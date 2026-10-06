/* The upstream published shrinkwrap pins a vulnerable bundled brace-expansion.
 * Use our exact, audited direct dependency after every npm ci. No upstream code
 * is changed; minimatch's ^5.0.2 range accepts the patched 5.0.12 release.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const root=path.dirname(fileURLToPath(import.meta.url));
const patched=path.join(root,'node_modules/brace-expansion/package.json');
if(JSON.parse(fs.readFileSync(patched,'utf8')).version!=='5.0.12')
  throw new Error('缺少固定的 brace-expansion 5.0.12 依赖，请重新安装助手环境');
const nested=path.join(root,'node_modules/@earendil-works/pi-coding-agent/node_modules/brace-expansion');
if(fs.existsSync(nested)){
  if(fs.lstatSync(nested).isSymbolicLink())throw new Error('依赖路径异常');
  const meta=JSON.parse(fs.readFileSync(path.join(nested,'package.json'),'utf8'));
  if(meta.name!=='brace-expansion')throw new Error('依赖名称异常');
  if(meta.version!=='5.0.12')fs.rmSync(nested,{recursive:true,force:true});
}
const require=createRequire(path.join(root,'node_modules/@earendil-works/pi-coding-agent/node_modules/minimatch/package.json'));
const actual=JSON.parse(fs.readFileSync(require.resolve('brace-expansion/package.json'),'utf8'));
if(actual.version!=='5.0.12')throw new Error('依赖修补未生效');
