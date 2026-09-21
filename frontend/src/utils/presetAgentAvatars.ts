/**
 * 预设 AI 智能体头像列表
 * 提供不同设计风格的现代化矢量头像（机器人、智慧光芒、极客科技、睿智学者等）
 */
import defaultAgentAvatarUrl from "@/assets/nanzi-agent-avatar.svg";

export interface PresetAgentAvatar {
  id: string;
  name: string;
  url: string;
  isDefault?: boolean;
}

const robotSvg = `data:image/svg+xml;utf8,${encodeURIComponent(`
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0284c7" />
      <stop offset="100%" stop-color="#2563eb" />
    </linearGradient>
    <linearGradient id="face" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#ffffff" />
      <stop offset="100%" stop-color="#e2e8f0" />
    </linearGradient>
  </defs>
  <circle cx="64" cy="64" r="58" fill="url(#bg)" />
  <line x1="64" y1="28" x2="64" y2="38" stroke="#ffffff" stroke-width="4" stroke-linecap="round" />
  <circle cx="64" cy="24" r="5" fill="#38bdf8" />
  <rect x="36" y="38" width="56" height="46" rx="14" fill="url(#face)" />
  <rect x="42" y="46" width="44" height="24" rx="8" fill="#0f172a" />
  <circle cx="53" cy="58" r="4.5" fill="#38bdf8" />
  <circle cx="75" cy="58" r="4.5" fill="#38bdf8" />
  <path d="M56 74 Q64 79 72 74" stroke="#64748b" stroke-width="3" stroke-linecap="round" fill="none" />
  <rect x="30" y="52" width="6" height="16" rx="3" fill="#38bdf8" />
  <rect x="92" y="52" width="6" height="16" rx="3" fill="#38bdf8" />
</svg>
`)}`;

const sparkSvg = `data:image/svg+xml;utf8,${encodeURIComponent(`
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
  <defs>
    <linearGradient id="spark-bg" x1="0%" y1="100%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#7c3aed" />
      <stop offset="50%" stop-color="#c026d3" />
      <stop offset="100%" stop-color="#f59e0b" />
    </linearGradient>
  </defs>
  <circle cx="64" cy="64" r="58" fill="url(#spark-bg)" />
  <path d="M64 26 C64 47 70 54 90 64 C70 74 64 81 64 102 C64 81 58 74 38 64 C58 54 64 47 64 26 Z" fill="#ffffff" />
  <circle cx="88" cy="40" r="3.5" fill="#fef08a" />
  <circle cx="40" cy="88" r="3.5" fill="#fef08a" />
  <circle cx="44" cy="42" r="2" fill="#ffffff" />
  <circle cx="86" cy="84" r="2" fill="#ffffff" />
</svg>
`)}`;

const cyberSvg = `data:image/svg+xml;utf8,${encodeURIComponent(`
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
  <defs>
    <linearGradient id="cyber-bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0f172a" />
      <stop offset="100%" stop-color="#1e293b" />
    </linearGradient>
    <linearGradient id="cyber-glow" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#10b981" />
      <stop offset="100%" stop-color="#06b6d4" />
    </linearGradient>
  </defs>
  <circle cx="64" cy="64" r="58" fill="url(#cyber-bg)" stroke="#10b981" stroke-width="2" />
  <circle cx="64" cy="64" r="34" fill="none" stroke="#334155" stroke-width="2" stroke-dasharray="6 4" />
  <polygon points="64,36 88,50 88,78 64,92 40,78 40,50" fill="none" stroke="url(#cyber-glow)" stroke-width="3.5" stroke-linejoin="round" />
  <circle cx="64" cy="64" r="10" fill="url(#cyber-glow)" />
  <circle cx="64" cy="36" r="3.5" fill="#34d399" />
  <circle cx="88" cy="50" r="3.5" fill="#38bdf8" />
  <circle cx="88" cy="78" r="3.5" fill="#38bdf8" />
  <circle cx="64" cy="92" r="3.5" fill="#34d399" />
  <circle cx="40" cy="78" r="3.5" fill="#34d399" />
  <circle cx="40" cy="50" r="3.5" fill="#34d399" />
</svg>
`)}`;

const scholarSvg = `data:image/svg+xml;utf8,${encodeURIComponent(`
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
  <defs>
    <linearGradient id="scholar-bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#1e1b4b" />
      <stop offset="100%" stop-color="#312e81" />
    </linearGradient>
  </defs>
  <circle cx="64" cy="64" r="58" fill="url(#scholar-bg)" />
  <path d="M64 54 C54 44 40 44 32 46 L32 86 C40 84 54 84 64 92 C74 84 88 84 96 86 L96 46 C88 44 74 44 64 54 Z" fill="#ffffff" fill-opacity="0.9" />
  <line x1="64" y1="54" x2="64" y2="92" stroke="#4f46e5" stroke-width="2.5" />
  <path d="M64 28 L67 36 L75 39 L67 42 L64 50 L61 42 L53 39 L61 36 Z" fill="#fbbf24" />
</svg>
`)}`;

export const PRESET_AGENT_AVATARS: PresetAgentAvatar[] = [
  {
    id: "default",
    name: "官方默认",
    url: defaultAgentAvatarUrl,
    isDefault: true,
  },
  {
    id: "robot",
    name: "智能小机",
    url: robotSvg,
  },
  {
    id: "spark",
    name: "智慧星火",
    url: sparkSvg,
  },
  {
    id: "cyber",
    name: "赛博科技",
    url: cyberSvg,
  },
  {
    id: "scholar",
    name: "博识专家",
    url: scholarSvg,
  },
];
