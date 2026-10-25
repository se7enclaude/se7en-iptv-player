import { create } from "zustand";
import { ApiError } from "../api/client";
import { events } from "../api/ws";
import { useApp } from "../store/app";
import { PlayerState, StreamState, stream } from "./api";

export type Playing = NonNullable<PlayerState["item"]>;

interface SyncState {
  percent: number;
  label: string;
  warning?: string;
}

interface StreamStore {
  state: StreamState | null;
  loaded: boolean;
  now: Playing | null;
  theater: boolean;
  covers: number;
  sync: Record<string, SyncState>;
  /** The live page remembers its category and channel between visits. */
  liveCategory: string;
  version: number; // bumped after a catalogue sync, a profile switch… so lists reload
  /** Artwork blurred behind the glass window (the hero of the home screen). */
  ambient: string | null;
  /** Text typed in the top bar search pill. */
  query: string;
  /** A recording is running or scheduled, or a reminder is set (dot on the bell and the dock). */
  recording: boolean;

  load: () => Promise<void>;
  play: (b: Parameters<typeof stream.play>[0]) => Promise<boolean>;
  stop: () => Promise<void>;
  setTheater: (on: boolean) => void;
  cover: (delta: number) => void;
  setLiveCategory: (c: string) => void;
  bump: () => void;
}

function errMessage(e: unknown): { title: string; body?: string } {
  if (e instanceof ApiError) return { title: e.message, body: e.hint };
  return { title: String((e as Error)?.message ?? e) };
}

export function toastError(e: unknown): void {
  const m = errMessage(e);
  useApp.getState().toast({ kind: "error", title: m.title, body: m.body });
}

let lsCat = "__all";
try {
  lsCat = localStorage.getItem("se7eniptv.liveCategory") || "__all";
} catch {
  /* ignore */
}

export const useStream = create<StreamStore>((set, get) => ({
  state: null,
  loaded: false,
  now: null,
  theater: false,
  covers: 0,
  sync: {},
  liveCategory: lsCat,
  version: 0,
  ambient: null,
  query: "",
  recording: false,

  load: async () => {
    const st = await stream.state();
    set({ state: st, loaded: true });
    refreshPending();
    if (!get().ambient && st.account) {
      // the room behind the glass starts with the artwork of a recent title
      stream.items("movie", { category: "__recent", limit: 6 }).then((r) => {
        const it = r.items.find((x) => x.logo);
        if (it && !get().ambient) set({ ambient: it.logo ?? null });
      }).catch(() => undefined);
    }
  },
  play: async (b) => {
    try {
      const r = await stream.play(b);
      set({ now: r.item ?? null });
      if (r.resumed_at && r.resumed_at > 0) {
        const m = Math.floor(r.resumed_at / 60);
        useApp.getState().toast({ kind: "info", title: `Reprise à ${Math.floor(m / 60) ? `${Math.floor(m / 60)} h ` : ""}${m % 60} min`, timeout: 3000 });
      }
      return true;
    } catch (e) {
      toastError(e);
      return false;
    }
  },
  stop: async () => {
    set({ now: null });
    if (get().theater) get().setTheater(false);
    await stream.cmd("stop").catch(() => undefined);
  },
  setTheater: (on) => {
    if (on === get().theater) return;
    set({ theater: on });
    stream.cmd("fullscreen", on).catch(() => undefined);
  },
  cover: (delta) => set({ covers: Math.max(0, get().covers + delta) }),
  setLiveCategory: (c) => {
    try {
      localStorage.setItem("se7eniptv.liveCategory", c);
    } catch {
      /* ignore */
    }
    set({ liveCategory: c });
  },
  bump: () => set({ version: get().version + 1 }),
}));

function refreshPending(): void {
  Promise.all([stream.recordings(), stream.reminders()])
    .then(([r, m]) => useStream.setState({ recording: r.recordings.some((x) => x.status === "recording" || x.status === "scheduled") || m.reminders.length > 0 }))
    .catch(() => undefined);
}

async function nextEpisode(item: Playing): Promise<void> {
  if (!item.series) return;
  const info = await stream.series(item.series);
  const all = info.seasons.flatMap((s) => s.episodes.map((e) => ({ ...e, season: s.season })));
  const i = all.findIndex((e) => e.id === item.id);
  const next = i >= 0 ? all[i + 1] : undefined;
  if (!next) {
    useApp.getState().toast({ kind: "success", title: `Tu as fini ${info.name}` });
    useStream.getState().stop();
    return;
  }
  useApp.getState().toast({ kind: "info", title: `Épisode suivant : S${next.season} E${next.episode ?? "?"}`, body: next.title, timeout: 4000 });
  await useStream.getState().play({ type: "episode", id: next.id, series: item.series, title: `S${next.season} E${next.episode ?? "?"} · ${next.title}` });
}

events.on((ev) => {
  const s = useStream.getState();
  const d = ev.data as any;
  switch (ev.type as string) {
    case "player":
      if (d.event === "fullscreen") useStream.setState({ theater: !!d.on });
      else if (d.event === "started") useStream.setState({ now: d.item ?? null });
      else if (d.event === "stop") useStream.setState({ now: null, theater: false }); // e.g. from the phone remote
      else if (d.event === "zapped") stream.player().then((p) => useStream.setState({ now: p.item ?? null })).catch(() => undefined);
      else if (d.event === "ended" && d.item?.kind === "episode") nextEpisode(d.item).catch(toastError);
      else if (d.event === "ended") s.stop();
      else if (d.event === "error") useApp.getState().toast({ kind: "error", title: d.message });
      else if (d.event === "action" && d.action === "click") window.dispatchEvent(new CustomEvent("se7en:player-click"));
      break;
    case "stream_sync": {
      const sync = { ...s.sync };
      if (d.done) {
        delete sync[d.account];
        useStream.setState({ sync });
        s.load().catch(() => undefined);
        s.bump();
      } else if (d.warning) {
        sync[d.account] = { ...(sync[d.account] ?? { percent: 0, label: "" }), warning: d.warning };
        useStream.setState({ sync });
      } else {
        sync[d.account] = { percent: d.percent ?? 0, label: d.label ?? "" };
        useStream.setState({ sync });
      }
      break;
    }
    case "stream_recordings":
      refreshPending();
      break;
    case "stream_reminder":
      useApp.getState().toast({
        kind: "info",
        title: `Ça commence : ${d.title}`,
        body: d.channel_name,
        timeout: 15000,
        action: { label: "Regarder", run: () => { useApp.getState().setPage("live" as any); s.play({ type: "live", id: d.channel }); } },
      });
      break;
  }
});
