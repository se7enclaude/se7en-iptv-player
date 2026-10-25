import { api } from "../api/client";

export type Kind = "live" | "movie" | "series";

export interface Account {
  id: string;
  kind: "xtream" | "m3u";
  name: string;
  server?: string;
  username?: string;
  m3u_url?: string;
  epg_url?: string;
  output?: string;
  user_agent?: string;
  synced?: number | null;
  epg_synced?: number | null;
  counts?: Record<string, number>;
  has_password?: boolean;
  status?: { expires?: number | null; max?: number | null; active?: number | null; problem?: string | null };
}

export interface Profile {
  id: string;
  name: string;
  kids: number | boolean;
  color?: string;
  locked?: boolean;
}

export interface StreamSettings {
  output: string;
  hwdec: string;
  buffer_s: number;
  buffer_mb: number;
  audio_lang: string;
  sub_lang: string;
  record_folder: string;
  record_default?: string;
  margin_before: number;
  margin_after: number;
  refresh_hours: number;
  refresh_on_start: boolean;
  hide_adult: boolean;
  parental_pin: boolean;
  volume: number;
  user_agent: string;
}

export interface StreamState {
  accounts: Account[];
  account: string | null;
  profiles: Profile[];
  profile: string;
  adult: boolean;
  settings: StreamSettings;
  mpv: boolean;
}

export interface Programme {
  channel?: string;
  start: number;
  stop: number;
  title: string;
  descr?: string;
  category?: string;
}

export interface Category {
  id: string;
  name: string;
  n?: number;
  adult?: number;
}

export interface Item {
  id: string;
  name: string;
  logo?: string;
  category?: string;
  fav?: boolean;
  // live
  num?: number;
  epg_id?: string;
  archive?: number;
  archive_days?: number;
  now?: Programme | null;
  next?: Programme | null;
  programmes?: Programme[];
  // vod / series
  rating?: number | null;
  year?: number | null;
  added?: number;
  plot?: string;
  progress?: number;
}

export interface Page<T> {
  total: number;
  items: T[];
}

export interface Episode {
  id: string;
  episode: number | null;
  title: string;
  ext: string;
  plot?: string;
  duration?: string;
  image?: string;
  progress: number;
  watched: boolean;
}

export interface SeriesInfo extends Item {
  cast?: string;
  genre?: string;
  backdrop?: string;
  seasons: { season: string; episodes: Episode[] }[];
}

export interface VodInfo extends Item {
  cast?: string;
  director?: string;
  genre?: string;
  duration?: string;
  release?: string;
  backdrop?: string;
  trailer?: string;
  poster?: string;
  resume: number;
}

export interface HistoryRow {
  type: "movie" | "episode";
  item: string;
  title: string;
  logo?: string;
  position: number;
  duration: number;
  progress: number;
  extra: { series?: string };
}

export interface Home {
  continue: HistoryRow[];
  recent: { id: string; name: string; logo?: string }[];
  favorites: Item[];
  movies: Item[];
  series: Item[];
}

export interface Recording {
  id: string;
  account: string;
  channel: string;
  channel_name: string;
  title: string;
  start: number;
  stop: number;
  source: "live" | "replay";
  status: "scheduled" | "recording" | "done" | "failed" | "missed" | "canceled" | "interrupted";
  file?: string | null;
  size?: number;
  error?: string | null;
}

export interface Track {
  id: number;
  type: "audio" | "sub" | "video";
  lang?: string;
  title?: string;
  codec?: string;
  selected?: boolean;
  external?: boolean;
}

export interface PlayerState {
  playing: boolean;
  item?: { kind: string; id: string; title?: string; subtitle?: string; logo?: string; number?: number; now?: Programme; next?: Programme; series?: string };
  paused?: boolean;
  buffering?: boolean;
  position?: number;
  duration?: number;
  volume?: number;
  muted?: boolean;
  tracks?: Track[];
  connections?: number;
  speed?: number;
  width?: number;
  height?: number;
  fps?: number;
  video_codec?: string;
  audio_codec?: string;
  hwdec?: string;
  video_bitrate?: number;
  audio_bitrate?: number;
  dropped?: number;
  cache_s?: number;
  aspect?: string;
}

export interface TestResult {
  kind: "xtream" | "m3u";
  server?: string;
  status?: string;
  expires?: number | null;
  trial?: boolean;
  active?: number | null;
  max?: number | null;
  formats?: string[];
  timezone?: string;
  counts: Record<string, number | null>;
  problem?: string | null;
}

export interface AccountForm {
  kind: "xtream" | "m3u";
  name: string;
  server: string;
  username: string;
  password: string;
  m3u_url: string;
  epg_url: string;
  output: string;
  user_agent: string;
}

const S = "/api/stream";
const enc = encodeURIComponent;

export const stream = {
  state: () => api.get<StreamState>(`${S}/state`),
  home: () => api.get<Home>(`${S}/home`),
  testAccount: (f: Partial<AccountForm>) => api.post<TestResult>(`${S}/accounts/test`, f),
  addAccount: (f: Partial<AccountForm>) => api.post<{ account: Account; summary: TestResult }>(`${S}/accounts`, f),
  editAccount: (id: string, f: Partial<AccountForm>) => api.patch<Account>(`${S}/accounts/${id}`, f),
  deleteAccount: (id: string) => api.del(`${S}/accounts/${id}`),
  selectAccount: (id: string) => api.post(`${S}/accounts/${id}/select`),
  syncAccount: (id: string, what = "all") => api.post(`${S}/accounts/${id}/sync?what=${what}`),
  categories: (k: Kind) => api.get<{ categories: Category[] }>(`${S}/categories/${k}`),
  items: (k: Kind, p: { category?: string; q?: string; offset?: number; limit?: number; sort?: string }) =>
    api.get<Page<Item>>(
      `${S}/items/${k}?category=${enc(p.category ?? "__all")}&q=${enc(p.q ?? "")}&offset=${p.offset ?? 0}&limit=${p.limit ?? 200}&sort=${p.sort ?? "default"}`,
    ),
  guide: (p: { category: string; start: number; hours: number; offset: number; limit: number }) =>
    api.get<{ start: number; end: number; total: number; channels: Item[] }>(
      `${S}/guide?category=${enc(p.category)}&start=${p.start}&hours=${p.hours}&offset=${p.offset}&limit=${p.limit}`,
    ),
  channelEpg: (id: string) => api.get<{ programmes: Programme[] }>(`${S}/channel/${enc(id)}/epg`),
  search: (q: string) => api.get<{ live: Item[]; movie: Item[]; series: Item[]; programmes: (Programme & { channel_id: string; channel_name: string; logo?: string })[] }>(`${S}/search?q=${enc(q)}`),
  vod: (id: string) => api.get<VodInfo>(`${S}/vod/${enc(id)}`),
  series: (id: string, refresh = false) => api.get<SeriesInfo>(`${S}/series/${enc(id)}?refresh=${refresh}`),
  favorite: (type: string, item: string, on: boolean) => api.post(`${S}/favorite`, { type, item, on }),
  hide: (type: string, item: string, on: boolean) => api.post(`${S}/hidden`, { type, item, on }),
  hidden: () => api.get<{ items: { type: string; item: string; name: string }[] }>(`${S}/hidden`),
  watched: (type: string, item: string, on: boolean) => api.post(`${S}/watched`, { type, item, on }),
  play: (b: { type: string; id: string; start?: number; stop?: number; title?: string; series?: string; resume?: boolean }) =>
    api.post<{ item: PlayerState["item"]; resumed_at?: number }>(`${S}/play`, b),
  rect: (r: { x: number; y: number; w: number; h: number; dpr: number; visible: boolean; radius: number }) => api.put(`${S}/player/rect`, r),
  player: () => api.get<PlayerState>(`${S}/player`),
  cmd: (cmd: string, value?: unknown) => api.post<{ ok?: boolean; on?: boolean; folder?: string }>(`${S}/player`, { cmd, value }),
  recordings: () => api.get<{ recordings: Recording[]; folder: string }>(`${S}/recordings`),
  record: (b: { channel: string; title?: string; start?: number; stop?: number; minutes?: number; source?: string }) => api.post<Recording>(`${S}/recordings`, b),
  stopRecording: (id: string) => api.post(`${S}/recordings/${id}/stop`),
  deleteRecording: (id: string, file: boolean) => api.del(`${S}/recordings/${id}?file=${file}`),
  playRecording: (id: string) => api.post(`${S}/recordings/${id}/play`),
  reminders: () => api.get<{ reminders: { id: string; channel: string; channel_name: string; title: string; start: number }[] }>(`${S}/reminders`),
  remind: (channel: string, title: string, start: number) => api.post<{ id: string }>(`${S}/reminders`, { channel, title, start }),
  unremind: (id: string) => api.del(`${S}/reminders/${id}`),
  addProfile: (b: { name: string; kids: boolean; color?: string; pin?: string }) => api.post<Profile>(`${S}/profiles`, b),
  editProfile: (id: string, b: { name?: string; kids?: boolean; color?: string }) => api.patch(`${S}/profiles/${id}`, b),
  deleteProfile: (id: string) => api.del(`${S}/profiles/${id}`),
  selectProfile: (id: string, pin = "") => api.post(`${S}/profiles/${id}/select`, { pin }),
  profilePin: (id: string, pin: string, new_pin: string) => api.post(`${S}/profiles/${id}/pin`, { pin, new_pin }),
  unlock: (pin: string) => api.post(`${S}/parental/unlock`, { pin }),
  lock: () => api.post(`${S}/parental/lock`),
  parentalPin: (pin: string, new_pin: string) => api.post(`${S}/parental/pin`, { pin, new_pin }),
  settings: () => api.get<StreamSettings>(`${S}/settings`),
  saveSettings: (p: Partial<StreamSettings>) => api.patch<StreamSettings>(`${S}/settings`, p),
  exportBackup: (path: string, password: string) => api.post<{ path: string; accounts: number }>(`${S}/backup/export`, { path, password }),
  importBackup: (path: string, password: string) => api.post<{ accounts: number }>(`${S}/backup/import`, { path, password }),
};
