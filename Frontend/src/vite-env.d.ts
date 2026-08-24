/// <reference types="vite/client" />

declare module 'jszip' {
  export default class JSZip {
    file(name: string, data: Blob | File | string | ArrayBuffer): this;
    generateAsync(options: { type: 'blob' | 'arraybuffer' | 'uint8array' | 'base64' | 'string' }): Promise<Blob>;
  }
}

interface HTMLInputElement {
  webkitdirectory: boolean;
  directory: boolean;
}
