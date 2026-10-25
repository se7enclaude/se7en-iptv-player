import { ArrowLeft, Film } from "lucide-react";
import { useEffect } from "react";
import { Button, Empty } from "../../components/ui";
import { useApp } from "../../store/app";
import { stream } from "../../stream/api";
import { VideoSurface } from "../../stream/components";
import { PlayerControls, usePlayerState } from "../../stream/PlayerControls";
import { useStream } from "../../stream/store";

/** Films, episodes, replays and recordings: a big player with the usual controls. */
export function WatchPage() {
  const now = useStream((s) => s.now);
  const theater = useStream((s) => s.theater);
  const back = useApp((s) => s.setPage);
  const st = usePlayerState();
  const item = st?.item ?? now;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if ((t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) || e.ctrlKey || e.altKey || useStream.getState().covers > 0) return;
      const k = e.key;
      if (k === " ") {
        e.preventDefault();
        stream.cmd("pause").catch(() => undefined);
      } else if (k === "ArrowLeft" || k === "ArrowRight") {
        e.preventDefault();
        stream.cmd("seek", (k === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 60 : 10)).catch(() => undefined);
      } else if (k === "ArrowUp" || k === "ArrowDown") {
        e.preventDefault();
        const v = (st?.volume ?? 100) + (k === "ArrowUp" ? 5 : -5);
        stream.cmd("volume", Math.max(0, Math.min(130, v))).catch(() => undefined);
      } else if (k.toLowerCase() === "f" || k === "Enter") {
        useStream.getState().setTheater(!useStream.getState().theater);
      } else if (k === "Escape") {
        if (useStream.getState().theater) useStream.getState().setTheater(false);
      } else if (k.toLowerCase() === "m") {
        stream.cmd("mute").catch(() => undefined);
      } else if (k.toLowerCase() === "i") {
        stream.cmd("osd", 5).catch(() => undefined);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [st?.volume]);

  if (!now)
    return (
      <Empty icon={<Film size={22} />} title="Rien en lecture" body="Choisis un film, un épisode, un replay ou un enregistrement."
        action={<Button onClick={() => back("movies" as any)}>Films</Button>} />
    );
  const backTo = now.kind === "episode" ? "series" : now.kind === "movie" ? "movies" : now.kind === "recording" ? "recordings" : "live";
  return (
    <div className="st-watch">
      {!theater && (
        <div className="row st-watch-head">
          <Button iconOnly icon={<ArrowLeft size={17} />} tip="Retour" onClick={() => back(backTo as any)} />
          <div style={{ minWidth: 0 }}>
            <div className="st-watch-title">{item?.title}</div>
            {item?.subtitle && <div className="muted st-watch-sub">{item.subtitle}</div>}
          </div>
        </div>
      )}
      <VideoSurface className="st-watch-video" />
      <PlayerControls st={st} live={false} />
    </div>
  );
}
