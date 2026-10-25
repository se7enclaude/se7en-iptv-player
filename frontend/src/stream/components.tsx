import clsx from "clsx";
import { Lock, Play, Tv } from "lucide-react";
import { MouseEvent, ReactNode, UIEvent, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button, Modal } from "../components/ui";
import { stream } from "./api";
import { toastError, useStream } from "./store";

// ------------------------------------------------------------------ formats
export const hm = (ts: number) => new Date(ts * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
export const day = (ts: number) => {
  const d = new Date(ts * 1000);
  const today = new Date();
  const diff = Math.round((new Date(d.toDateString()).getTime() - new Date(today.toDateString()).getTime()) / 86400000);
  if (diff === 0) return "Aujourd'hui";
  if (diff === -1) return "Hier";
  if (diff === 1) return "Demain";
  return d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
};
export function clock(s?: number | null): string {
  if (!s || s < 0) s = 0;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}
export function progressOf(p?: { start: number; stop: number } | null, now = Date.now() / 1000): number {
  if (!p || p.stop <= p.start) return 0;
  return Math.min(1, Math.max(0, (now - p.start) / (p.stop - p.start)));
}

/** Seconds clock that re-renders its users every `ms`. */
export function useNow(ms = 30000): number {
  const [n, setN] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = window.setInterval(() => setN(Date.now() / 1000), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return n;
}

// ------------------------------------------------------------------ images
export function Logo({ src, name, size = 40, className }: { src?: string | null; name: string; size?: number; className?: string }) {
  const [bad, setBad] = useState(false);
  useEffect(() => setBad(false), [src]);
  const initials = name.replace(/[^\p{L}\p{N} ]/gu, "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  return (
    <span className={clsx("st-logo", className)} style={{ width: size, height: size }}>
      {src && !bad ? <img src={src} alt="" loading="lazy" draggable={false} referrerPolicy="no-referrer" onError={() => setBad(true)} /> : <span className="st-logo-txt">{initials || <Tv size={size * 0.45} />}</span>}
    </span>
  );
}

export function PlayDisc({ onClick, small, label = "Lecture" }: { onClick?: (e: MouseEvent) => void; small?: boolean; label?: string }) {
  return (
    <span role="button" tabIndex={-1} aria-label={label} className={clsx("st-play-disc", small && "sm")}
      onClick={(e) => { if (onClick) { e.stopPropagation(); onClick(e); } }}>
      <Play size={small ? 12 : 15} />
    </span>
  );
}

/** Portrait card: artwork edge to edge, title on a bottom scrim, round white play disc, red resume line. */
export function Poster({ src, name, progress, badge, onClick, sub, onPlay }: { src?: string | null; name: string; progress?: number; badge?: ReactNode; onClick?: () => void; sub?: ReactNode; onPlay?: () => void }) {
  const [bad, setBad] = useState(false);
  useEffect(() => setBad(false), [src]);
  return (
    <button className="st-poster" onClick={onClick} title={name}>
      <span className="st-poster-img">
        {src && !bad ? <img src={src} alt="" loading="lazy" draggable={false} referrerPolicy="no-referrer" onError={() => setBad(true)} /> : <span className="st-poster-fallback">{name}</span>}
      </span>
      <span className="st-poster-scrim" />
      {badge && <span className="st-poster-badge ip-badge ip-badge--glass">{badge}</span>}
      <span className="st-poster-info">
        <span className="st-poster-text">
          <span className="st-poster-name">{name}</span>
          {sub && <span className="st-poster-sub">{sub}</span>}
        </span>
        <PlayDisc small onClick={onPlay ? () => onPlay() : undefined} />
      </span>
      {!!progress && progress > 0.01 && (
        <span className="st-bar st-poster-bar">
          <i style={{ width: `${Math.min(100, progress * 100)}%` }} />
        </span>
      )}
    </button>
  );
}

export function Bar({ value, className }: { value: number; className?: string }) {
  return (
    <span className={clsx("st-bar", className)}>
      <i style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }} />
    </span>
  );
}

// ------------------------------------------------------------------ video surface
/**
 * Where the video goes. mpv draws in a native window placed exactly over this box (the page cannot draw over the
 * video, so the box only holds a placeholder). In theater mode the box covers the whole window.
 */
export function VideoSurface({ className, radius = 22, children, idle }: { className?: string; radius?: number; children?: ReactNode; idle?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const now = useStream((s) => s.now);
  const covers = useStream((s) => s.covers);
  const theater = useStream((s) => s.theater);
  const visible = !!now && covers === 0;
  const last = useRef("");

  const send = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const rect = { x: r.left, y: r.top, w: r.width, h: r.height, dpr: window.devicePixelRatio || 1, visible: visible && r.width > 20 && r.height > 20, radius: theater ? 0 : radius };
    const key = JSON.stringify(rect);
    if (key === last.current) return;
    last.current = key;
    stream.rect(rect).catch(() => undefined);
  }, [visible, theater, radius]);

  useLayoutEffect(() => {
    send();
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(send);
    ro.observe(el);
    window.addEventListener("resize", send);
    window.addEventListener("scroll", send, true);
    // animations / layout shifts that ResizeObserver does not see (the box moves without resizing)
    const t = window.setInterval(send, 400);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", send);
      window.removeEventListener("scroll", send, true);
      window.clearInterval(t);
    };
  }, [send]);

  useEffect(
    () => () => {
      last.current = "";
      stream.rect({ x: 0, y: 0, w: 0, h: 0, dpr: 1, visible: false, radius: 0 }).catch(() => undefined);
    },
    [],
  );

  return (
    <div ref={ref} className={clsx("st-video", theater && "st-video-theater", className)} onDoubleClick={() => useStream.getState().setTheater(!theater)}>
      {!now && (idle ?? <div className="st-video-idle"><Tv size={34} /><span>Choisis une chaîne</span></div>)}
      {now && covers > 0 && <div className="st-video-idle"><span>{now.title}</span></div>}
      {children}
    </div>
  );
}

// ------------------------------------------------------------------ virtual list
/** Fixed-height rows; renders only what is on screen (tens of thousands of channels stay smooth). */
export function VirtualList<T>({ items, rowHeight, render, className, onEnd, scrollTo }: { items: T[]; rowHeight: number; render: (item: T, i: number) => ReactNode; className?: string; onEnd?: () => void; scrollTo?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [top, setTop] = useState(0);
  const [h, setH] = useState(600);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setH(el.clientHeight));
    ro.observe(el);
    setH(el.clientHeight);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (!el || scrollTo == null || scrollTo < 0) return;
    const y = scrollTo * rowHeight;
    if (y < el.scrollTop || y + rowHeight > el.scrollTop + el.clientHeight) el.scrollTop = Math.max(0, y - el.clientHeight / 2);
  }, [scrollTo, rowHeight]);
  const onScroll = (e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    setTop(el.scrollTop);
    if (onEnd && el.scrollTop + el.clientHeight > el.scrollHeight - rowHeight * 10) onEnd();
  };
  const first = Math.max(0, Math.floor(top / rowHeight) - 6);
  const last = Math.min(items.length, Math.ceil((top + h) / rowHeight) + 6);
  return (
    <div ref={ref} className={clsx("st-vlist", className)} onScroll={onScroll}>
      <div style={{ height: items.length * rowHeight, position: "relative" }}>
        {items.slice(first, last).map((it, k) => (
          <div key={first + k} style={{ position: "absolute", top: (first + k) * rowHeight, left: 0, right: 0, height: rowHeight }}>
            {render(it, first + k)}
          </div>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ PIN
export function PinField({ value, onChange, autoFocus }: { value: string; onChange: (v: string) => void; autoFocus?: boolean }) {
  return (
    <input
      className="input st-pin"
      type="password"
      inputMode="numeric"
      autoComplete="off"
      maxLength={8}
      autoFocus={autoFocus}
      value={value}
      placeholder="••••"
      onChange={(e) => onChange(e.target.value.replace(/\D/g, ""))}
    />
  );
}

/** Asks for the parental code, then unlocks adult content for 2 hours. */
export function UnlockModal({ open, onClose }: { open: boolean; onClose: (ok: boolean) => void }) {
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (open) {
      setPin("");
      setErr("");
    }
  }, [open]);
  const go = async () => {
    setBusy(true);
    try {
      await stream.unlock(pin);
      await useStream.getState().load();
      useStream.getState().bump();
      onClose(true);
    } catch (e: any) {
      setErr(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} onClose={() => onClose(false)} eyebrow={<><Lock size={12} /> Contrôle parental</>} title="Afficher les contenus adultes"
      actions={<><Button variant="ghost" onClick={() => onClose(false)}>Annuler</Button><Button variant="primary" loading={busy} onClick={go}>Déverrouiller 2 h</Button></>}>
      <p>Entre le code parental. Les catégories adultes restent visibles 2 heures, puis se verrouillent toutes seules.</p>
      <form onSubmit={(e) => { e.preventDefault(); go(); }} style={{ marginTop: 14 }}>
        <PinField value={pin} onChange={setPin} autoFocus />
      </form>
      {err && <p className="st-err">{err}</p>}
    </Modal>
  );
}

export async function toggleFav(type: string, id: string, on: boolean, after?: () => void): Promise<void> {
  try {
    await stream.favorite(type, id, on);
    after?.();
  } catch (e) {
    toastError(e);
  }
}
