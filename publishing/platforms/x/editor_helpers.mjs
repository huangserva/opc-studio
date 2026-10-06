/** DOM-only helpers. Call through the active Browser skill, after inspecting the UI.
 * These functions do not attach to a browser, read credentials, call X APIs or publish.
 */
export function readEditorDom() {
  const editors = [...document.querySelectorAll('[contenteditable="true"][role="textbox"]')]
    .filter(e => e.getClientRects().length);
  if (editors.length !== 1) throw new Error('Expected one visible article editor; inspect the UI');
  const editor = editors[0];
  const nodes = [...editor.querySelectorAll('[data-block="true"]')]
    .filter(e => !e.parentElement?.closest('[data-block="true"]'));
  if (!nodes.length) throw new Error('Editor structure changed; inspect the UI');
  const blocks = [];
  for (const node of nodes) {
    const images = [...node.querySelectorAll('img')];
    if (images.length) {
      for (const img of images) blocks.push({
        kind: 'image', media_id: null, identity_evidence: null,
        src: img.currentSrc || img.src, alt: img.alt,
        loaded: img.complete && img.naturalWidth > 0,
        width: img.naturalWidth, height: img.naturalHeight,
      });
    } else {
      const spans = [...node.querySelectorAll('[data-text="true"]')];
      if (!spans.length && node.innerText.trim()) {
        blocks.push({kind: 'unsupported', text: node.innerText});
      } else {
        blocks.push({kind: 'text', text: spans.map(e => e.textContent).join('')});
      }
    }
  }
  return {url: location.href, blocks};
}

// Use ONLY via a documented DOM selection/edit capability (e.g. supported CDP).
// Do not invoke this mutation through read-only playwright.evaluate.
export function selectMarkerDom(marker) {
  const editors = [...document.querySelectorAll('[contenteditable="true"][role="textbox"]')]
    .filter(e => e.getClientRects().length);
  if (editors.length !== 1) throw new Error('Expected one visible article editor');
  const editor = editors[0];
  const spans = [...editor.querySelectorAll('[data-text="true"]')].filter(e => e.textContent === marker);
  if (spans.length !== 1) throw new Error('Marker missing, split or duplicated; inspect before writing');
  editor.focus();
  const range = document.createRange();
  range.selectNodeContents(spans[0]);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  if (selection.toString() !== marker) throw new Error('Marker selection failed');
  return {selected: marker};
}
