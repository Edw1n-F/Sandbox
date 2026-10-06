/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Scene to load. The scene is the entry anchor (CLAUDE.md). */
  readonly VITE_SCENE_ID?: string
  /** Leave blank: the demo runs on OpenStreetMap tiles with no token (ADR-033). */
  readonly VITE_CESIUM_ION_TOKEN?: string
  /** Overrides the API base. Blank = same origin, proxied by Vite. */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
