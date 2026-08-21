/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DESIGN_MODE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
