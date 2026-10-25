import clsx from "clsx";
import { Activity, AudioLines, Camera, Captions, CircleDot, Gauge, Maximize, Pause, Play, RectangleHorizontal, Square, Volume2, VolumeX } from "lucide-react";
import { ReactNode, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { Button } from "../components/ui";
import { useApp } from "../store/app";
import { PlayerState, stream } from "./api";
import { clock } from "./components";
import { toastError, useStream } from "./store";

/** Polls the player once a second while something plays. */
export function usePlayerState(): PlayerState | null {
  const now = useStream((s) => s.now);
  const [st, setSt] = useState<PlayerState | null>(null);
  useEffect(() => {
    if (!now) {
      setSt(null);
      return;
    }
    let alive = true;
    const tick = () => stream.player().then((p) => alive && setSt(p)).catch(() => undefined);
    tick();
    const t = window.setInterval(tick, 1000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [now]);
  return st;
}

function Menu({ icon, tip, children, disabled }: { icon: ReactNode; tip: string; children: (close: () => void) => ReactNode; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    window.addEventListener("mousedown", off);
    return () => window.removeEventListener("mousedown", off);
  }, [open]);
  return (
    <div className="st-menu" ref={ref}>
      <Button iconOnly size="sm" icon={icon} tip={tip} active={open} disabled={disabled} onClick={() => setOpen(!open)} />
      {open && <div className="st-menu-pop glass rise">{children(() => setOpen(false))}</div>}
    </div>
  );
}

function MenuItem({ on, children, onClick }: { on?: boolean; children: ReactNode; onClick: () => void }) {
  return (
    <button className={clsx("st-menu-item", on && "on")} onClick={onClick}>
      <span className="st-menu-dot" />
      {children}
    </button>
  );
}

const LANG: Record<string, string> = { fr: "Français", fre: "Français", fra: "Français", en: "Anglais", eng: "Anglais", ar: "Arabe", ara: "Arabe", es: "Espagnol", spa: "Espagnol", de: "Allemand", ger: "Allemand", deu: "Allemand", it: "Italien", ita: "Italien", pt: "Portugais", por: "Portugais", tr: "Turc", tur: "Turc" };

const cmd = (c: string, v?: unknown) => stream.cmd(c, v).catch(toastError);

export function PlayerControls({ st, live, extra }: { st: PlayerState | null; live?: boolean; extra?: ReactNode }) {
  const toast = useApp((s) => s.toast);
  const [stats, setStats] = useState(false);
  const playing = !!st?.playing;
  const audio = st?.tracks?.filter((t) => t.type === "audio") ?? [];
  const subs = st?.tracks?.filter((t) => t.type === "sub") ?? [];
  const label = (t: { id: number; lang?: string; title?: string; codec?: string }) =>
    [LANG[t.lang ?? ""] ?? t.lang?.toUpperCase(), t.title, t.codec && `(${t.codec})`].filter(Boolean).join(" · ") || `Piste ${t.id}`;
  const seekable = !live && (st?.duration ?? 0) > 0;

  return (
    <div className="st-controls">
      <div className="row st-controls-row">
        <Button iconOnly icon={st?.paused ? <Play size={17} /> : <Pause size={17} />} tip="Lecture / pause (Espace)" disabled={!playing} onClick={() => cmd("pause")} />
        {seekable && (
          <div className="st-seek">
            <span className="mono">{clock(st?.position)}</span>
            <input type="range" min={0} max={Math.max(1, st?.duration ?? 1)} step={1} value={st?.position ?? 0} onChange={(e) => cmd("seek_to", Number(e.target.value))} />
            <span className="mono">{clock(st?.duration)}</span>
          </div>
        )}
        {!seekable && <span className="spacer" />}
        <div className="st-vol">
          <Button iconOnly size="sm" icon={st?.muted ? <VolumeX size={16} /> : <Volume2 size={16} />} tip="Couper le son (M)" disabled={!playing} onClick={() => cmd("mute")} />
          <input type="range" min={0} max={130} value={st?.volume ?? 100} disabled={!playing} onChange={(e) => cmd("volume", Number(e.target.value))} />
        </div>
        <Menu icon={<AudioLines size={16} />} tip="Langue" disabled={audio.length < 2}>
          {(close) => audio.map((t) => <MenuItem key={t.id} on={t.selected} onClick={() => { cmd("aid", t.id); close(); }}>{label(t)}</MenuItem>)}
        </Menu>
        <Menu icon={<Captions size={16} />} tip="Sous-titres" disabled={!playing}>
          {(close) => (
            <>
              <MenuItem on={!subs.some((s) => s.selected)} onClick={() => { cmd("sid", "no"); close(); }}>Aucun</MenuItem>
              {subs.map((t) => <MenuItem key={t.id} on={t.selected} onClick={() => { cmd("sid", t.id); close(); }}>{label(t)}</MenuItem>)}
              <MenuItem onClick={async () => {
                close();
                const r = await api.post<{ paths: string[] }>("/api/system/pick-files", { kind: "subs", multiple: false }).catch(() => null);
                if (r?.paths?.[0]) cmd("subtitle_file", r.paths[0]);
              }}>Ajouter un fichier…</MenuItem>
            </>
          )}
        </Menu>
        {!live && (
          <Menu icon={<Gauge size={16} />} tip="Vitesse" disabled={!playing}>
            {(close) => [0.5, 0.75, 1, 1.25, 1.5, 2].map((v) => <MenuItem key={v} on={Math.abs((st?.speed ?? 1) - v) < 0.01} onClick={() => { cmd("speed", v); close(); }}>{v === 1 ? "Normale" : `× ${v}`}</MenuItem>)}
          </Menu>
        )}
        <Menu icon={<RectangleHorizontal size={16} />} tip="Format d'image" disabled={!playing}>
          {(close) => [
            ["-1", "Automatique"],
            ["16:9", "16:9"],
            ["4:3", "4:3"],
            ["2.35:1", "Cinéma 2,35"],
          ].map(([v, l]) => <MenuItem key={v} on={(st?.aspect ?? "-1") === v} onClick={() => { cmd("video-aspect-override", v); cmd("panscan", 0); close(); }}>{l}</MenuItem>)
            .concat(<MenuItem key="zoom" onClick={() => { cmd("panscan", 1); close(); }}>Zoom (remplir)</MenuItem>)}
        </Menu>
        <Button iconOnly size="sm" icon={<Camera size={16} />} tip="Capture d'écran" disabled={!playing}
          onClick={async () => { const r = await stream.cmd("screenshot").catch(toastError); if (r && r.folder) toast({ kind: "success", title: "Capture enregistrée", body: r.folder }); }} />
        <Button iconOnly size="sm" icon={<Activity size={16} />} tip="Statistiques" active={stats} disabled={!playing} onClick={() => setStats(!stats)} />
        {extra}
        <Button iconOnly size="sm" icon={<Square size={14} />} tip="Arrêter" disabled={!playing} onClick={() => useStream.getState().stop()} />
        <Button iconOnly size="sm" icon={<Maximize size={16} />} tip="Plein écran (F, double-clic)" disabled={!playing} onClick={() => useStream.getState().setTheater(true)} />
      </div>
      {stats && st?.playing && (
        <div className="st-stats mono rise">
          <span>Image <b>{st.width && st.height ? `${st.width}×${st.height}` : "—"}{st.fps ? ` · ${st.fps.toFixed(2)} i/s` : ""}</b></span>
          <span>Vidéo <b>{st.video_codec?.split(" ")[0] ?? "—"}{st.hwdec && st.hwdec !== "no" ? ` · matériel (${st.hwdec})` : " · logiciel"}</b></span>
          <span>Son <b>{st.audio_codec ?? "—"}</b></span>
          <span>Débit <b>{st.video_bitrate ? `${(st.video_bitrate / 1e6).toFixed(1)} Mb/s` : "—"}</b></span>
          <span>Tampon <b>{st.cache_s != null ? `${st.cache_s.toFixed(1)} s` : "—"}</b></span>
          <span>Images perdues <b>{st.dropped ?? 0}</b></span>
          <span>Connexions <b>{st.connections ?? 0}</b></span>
        </div>
      )}
    </div>
  );
}

export function RecordNow({ channel, title }: { channel: string; title?: string }) {
  const toast = useApp((s) => s.toast);
  return (
    <Button iconOnly size="sm" icon={<CircleDot size={16} />} tip="Enregistrer cette chaîne"
      onClick={async () => {
        try {
          const r = await stream.record({ channel, title, minutes: 240 });
          toast({ kind: "success", title: "Enregistrement lancé", body: `${r.title} · arrête-le dans Enregistrements` });
        } catch (e) {
          toastError(e);
        }
      }} />
  );
}
