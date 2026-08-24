/**
 * Shared document-export utilities used by RCA Summary, RCA Detailed, and any
 * future page that needs PDF / DOC / ODT download.
 */

// ── Core helpers ─────────────────────────────────────────────────────────

/** Trigger a browser download for an in-memory Blob. */
export function downloadBlob(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

const DOC_STYLES = `
  body{font-family:Calibri,Arial,sans-serif;margin:40px;color:#1e293b;line-height:1.6}
  h1{color:#0f172a} h2{color:#1e293b;margin-top:28px;border-bottom:1px solid #e2e8f0;padding-bottom:6px}
  h3{color:#334155;margin-top:20px}
  table{border-collapse:collapse;width:100%;margin:16px 0}
  th,td{border:1px solid #cbd5e1;padding:8px 12px;text-align:left}
  th{background:#f1f5f9;font-weight:600}
  code{background:#f1f5f9;padding:2px 6px;font-family:Consolas,'Courier New',monospace;font-size:.9em}
  pre{background:#f8fafc;border:1px solid #e2e8f0;padding:16px;font-family:Consolas,'Courier New',monospace;font-size:.85em;white-space:pre-wrap;word-wrap:break-word}
  strong{color:#be123c} li{margin:4px 0} p{margin:8px 0}
  blockquote{border-left:4px solid #fbbf24;background:#fffbeb;padding:8px 16px;margin:12px 0}
  hr{border:none;border-top:1px solid #e2e8f0;margin:24px 0}
  .no-export{display:none!important}
`.trim();

/** Wrap body HTML in a Word-compatible HTML document. */
export function buildDocHtml(bodyHtml: string, title = 'RCA Report'): string {
  return `<!DOCTYPE html>
<html xmlns:o="urn:schemas-microsoft-com:office:office"
      xmlns:w="urn:schemas-microsoft-com:office:word"
      xmlns="http://www.w3.org/TR/REC-html40">
<head>
<meta charset="utf-8"/>
<title>${title}</title>
<style>${DOC_STYLES}</style>
<!--[if gte mso 9]><xml><w:WordDocument><w:View>Print</w:View><w:Zoom>100</w:Zoom></w:WordDocument></xml><![endif]-->
</head>
<body>${bodyHtml}</body>
</html>`;
}

/** Capture the innerHTML of a DOM element by id.  Returns '' if missing. */
export function captureElementHtml(elementId: string): string {
  return document.getElementById(elementId)?.innerHTML ?? '';
}

/** Capture the innerText of a DOM element by id.  Returns '' if missing. */
export function captureElementText(elementId: string): string {
  return document.getElementById(elementId)?.innerText ?? '';
}

/**
 * Build a full styled HTML page from an element — includes all page
 * stylesheets so the printed/exported page matches the screen.
 */
export function buildStyledPageHtml(elementId: string, title = 'RCA Report'): string {
  const content = captureElementHtml(elementId);
  const styles = Array.from(document.querySelectorAll('style, link[rel="stylesheet"]'))
    .map(node => node.outerHTML)
    .join('\n');
  return `<html>
<head>
  <meta charset="utf-8" />
  <title>${title}</title>
  ${styles}
  <style>
    body { font-family: system-ui, sans-serif; margin: 24px; color: #1e293b; }
    .no-export { display: none !important; }
    .prose { max-width: none !important; }
  </style>
</head>
<body>${content}</body>
</html>`;
}

// ── High-level export actions ────────────────────────────────────────────

/** Export a DOM element as a .doc file (Word-compatible HTML). */
export function exportAsDoc(elementId: string, filename: string, title?: string): void {
  const html = captureElementHtml(elementId);
  if (!html) return;
  const doc = buildDocHtml(html, title);
  downloadBlob(filename, new Blob(['\ufeff', doc], { type: 'application/msword;charset=utf-8' }));
}

/** Export a DOM element via the browser print dialog (PDF). */
export function exportAsPdf(elementId: string, title?: string): void {
  const html = buildStyledPageHtml(elementId, title);
  const w = window.open('', '_blank');
  if (!w) return;
  w.document.write(html);
  w.document.close();
  w.focus();
  w.print();
}

// ── ODT builder ──────────────────────────────────────────────────────────

function escapeXml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(data: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < data.length; i++) c = CRC_TABLE[(c ^ data[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

const u16 = (n: number) => new Uint8Array([n & 0xff, (n >>> 8) & 0xff]);
const u32 = (n: number) => new Uint8Array([n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, (n >>> 24) & 0xff]);
const te = new TextEncoder();

/** Build a minimal .odt (OpenDocument Text) Blob from plain text. */
export function buildOdtBlob(plainText: string): Blob {
  const body = plainText
    .split('\n')
    .map(l => l.trimEnd())
    .map(l => `<text:p>${escapeXml(l.length ? l : ' ')}</text:p>`)
    .join('');

  const mimetype = 'application/vnd.oasis.opendocument.text';
  const contentXml = `<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 office:version="1.2">
  <office:body><office:text>${body}</office:text></office:body>
</office:document-content>`;

  const manifestXml = `<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
  <manifest:file-entry manifest:media-type="application/vnd.oasis.opendocument.text" manifest:full-path="/"/>
  <manifest:file-entry manifest:media-type="text/xml" manifest:full-path="content.xml"/>
</manifest:manifest>`;

  const files = [
    { name: 'mimetype', data: te.encode(mimetype) },
    { name: 'content.xml', data: te.encode(contentXml) },
    { name: 'META-INF/manifest.xml', data: te.encode(manifestXml) },
  ];

  const chunks: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;

  for (const f of files) {
    const name = te.encode(f.name);
    const crc = crc32(f.data);
    const size = f.data.length;

    const local = new Uint8Array([
      ...u32(0x04034b50), ...u16(20), ...u16(0), ...u16(0), ...u16(0), ...u16(0),
      ...u32(crc), ...u32(size), ...u32(size), ...u16(name.length), ...u16(0),
    ]);
    chunks.push(local, name, f.data);

    const cent = new Uint8Array([
      ...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0), ...u16(0), ...u16(0), ...u16(0),
      ...u32(crc), ...u32(size), ...u32(size), ...u16(name.length), ...u16(0), ...u16(0),
      ...u16(0), ...u16(0), ...u32(0), ...u32(offset),
    ]);
    central.push(cent, name);

    offset += local.length + name.length + size;
  }

  const centralSize = central.reduce((s, c) => s + c.length, 0);
  const end = new Uint8Array([
    ...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(files.length), ...u16(files.length),
    ...u32(centralSize), ...u32(offset), ...u16(0),
  ]);

  const toPart = (u: Uint8Array): ArrayBuffer =>
    u.buffer.slice(u.byteOffset, u.byteOffset + u.byteLength) as ArrayBuffer;
  return new Blob([...chunks, ...central, end].map(toPart), { type: mimetype });
}

/** Export a DOM element as an .odt file. */
export function exportAsOdt(elementId: string, filename: string): void {
  const text = captureElementText(elementId);
  if (!text) return;
  downloadBlob(filename, buildOdtBlob(text));
}
