/* Context changes use Pi's append-only projection, never a provider payload filter. */
import {createHash} from 'node:crypto';

export function manageImageContext(manager, catalog = []) {
  const refs = new Map(catalog.map(ref => [ref.sha256, ref]));
  const projection = manager.buildSessionProjection();
  let retired = 0;
  for (let i = 0; i < projection.entries.length; i++) {
    const entry = projection.entries[i];
    const message = entry.messages[0];
    if (!['user','toolResult'].includes(message?.role) || !Array.isArray(message.content) ||
        !message.content.some(p => p.type === 'image')) continue;
    let answered = false;
    for (const next of projection.entries.slice(i + 1)) {
      for (const m of next.messages) {
        if (m.role === 'user') break;
        if (m.role === 'assistant' && m.stopReason === 'stop' &&
            m.content.some(p => p.type === 'text' && p.text.trim())) answered = true;
      }
      if (next.messages.some(m => m.role === 'user') || answered) break;
    }
    if (!answered) continue; // Failed or unfinished image requests remain available.
    const content = message.content.map(part => {
      if (part.type !== 'image') return part;
      const hash = createHash('sha256').update(Buffer.from(part.data,'base64')).digest('hex');
      const ref = refs.get(hash);
      // Never discard an image whose local original cannot be located.
      if (!ref) return part;
      retired++;
      return {type:'text',text:`[已分析的历史图片，原图保存在本机。attachment_id=${ref.id}，名称=${JSON.stringify(ref.name)}。分析结论保留在后续回复中；重新核对视觉细节时调用 read_chat_image。]`};
    });
    if (content.some((p,index) => p !== message.content[index]))
      manager.appendContextEdit(entry.sourceEntry.id, {content});
  }
  // Old provider-side truncation made stored usage describe a different transcript.
  // A native edit invalidates that usage once; Pi then estimates its full projection.
  if (!manager.getEntries().some(e => e.type === 'custom' && e.customType === 'studio-context-v2')) {
    const first = projection.entries.find(e => e.sourceEntry.type === 'message' && e.messages[0]?.role === 'user');
    if (first && retired === 0) manager.appendContextEdit(first.sourceEntry.id,{content:first.messages[0].content});
    manager.appendCustomEntry('studio-context-v2',{retired,management:'pi-native'});
  }
  return retired;
}

export function monitoredStream(create, options, seconds, makeStream, progress, failure) {
  const output = makeStream();
  const controller = new AbortController();
  let timer;
  const reset = () => { clearTimeout(timer); timer = setTimeout(() => controller.abort(), seconds * 1000); };
  reset();
  (async () => {
    try {
      const source = create({...options, timeoutMs: seconds * 1000,
        signal: AbortSignal.any([options?.signal || new AbortController().signal, controller.signal])});
      for await (const event of source) {
        reset(); progress?.(event); output.push(event);
      }
    } finally { clearTimeout(timer); }
  })().catch(error => { clearTimeout(timer); if (failure) output.push(failure(error)); else output.end(); });
  return output;
}
