import clsx from "clsx";
import { AlarmClock, Archive, CircleDot, EyeOff, History, Lock, Search, Star, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Empty } from "../../components/ui";
import { useApp } from "../../store/app";
import { Category, isOf, Item, Programme, stream } from "../../stream/api";
import { QualityPicker } from "../../stream/components";
import { Bar, Logo, UnlockModal, VideoSurface, VirtualList, day, hm, progressOf, toggleFav, useNow } from "../../stream/components";
import { PlayerControls, RecordNow, usePlayerState } from "../../stream/PlayerControls";
import { toastError, useStream } from "../../stream/store";

const ROW = 64;

export function CategoryList({ cats, value, onChange, onLocked }: { cats: Category[]; value: string; onChange: (id: string) => void; onLocked?: () => void }) {
  const state = useStream((s) => s.state);
  const special: Record<string, string> = { __all: "Toutes", __fav: "Favoris", __recent: "Ajouts récents" };
  return (
    <div className="st-cats">
      {cats.map((c) => (
        <button key={c.id} className={clsx("st-cat", value === c.id && "on")} onClick={() => onChange(c.id)}>
          {c.id === "__fav" && <Star size={13} />}
          <span className="st-cat-name">{special[c.id] ?? c.name}</span>
          {c.n != null && <span className="st-cat-n">{c.n.toLocaleString("fr-FR")}</span>}
        </button>
      ))}
      {state && !state.adult && onLocked && (
        <button className="st-cat st-cat-locked" onClick={onLocked}>
          <Lock size={13} />
          <span className="st-cat-name">Contenus adultes</span>
        </button>
      )}
    </div>
  );
}

function ChannelRow({ ch, on, onClick, onFav, now }: { ch: Item; on: boolean; onClick: () => void; onFav: () => void; now: number }) {
  return (
    <div className={clsx("st-ch", on && "on")} onClick={onClick} role="button">
      <span className="st-ch-num mono">{ch.num ?? ""}</span>
      <Logo src={ch.logo} name={ch.name} size={40} />
      <span className="st-ch-text">
        <span className="st-ch-name">
          {ch.name}
          {!!ch.variants?.length && <span className="st-q-mini">{ch.variants.map((v) => v.quality || "?").join(" · ")}</span>}
        </span>
        {ch.now ? (
          <>
            <span className="st-ch-now">{ch.now.title}</span>
            <Bar value={progressOf(ch.now, now)} className="st-ch-bar" />
          </>
        ) : (
          <span className="st-ch-now muted">{ch.archive ? "Replay disponible" : " "}</span>
        )}
      </span>
      {!!ch.archive && <Archive size={13} className="st-ch-icon" />}
      <button className={clsx("st-star", ch.fav && "on")} onClick={(e) => { e.stopPropagation(); onFav(); }} aria-label="Favori">
        <Star size={15} />
      </button>
    </div>
  );
}

function ChannelGuide({ ch, now }: { ch: Item; now: number }) {
  const toast = useApp((s) => s.toast);
  const play = useStream((s) => s.play);
  const setPage = useApp((s) => s.setPage);
  const [progs, setProgs] = useState<Programme[] | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setProgs(null);
    stream.channelEpg(ch.id).then((r) => setProgs(r.programmes)).catch(() => setProgs([]));
  }, [ch.id]);
  useEffect(() => {
    const el = listRef.current?.querySelector(".st-prog.now") as HTMLElement | null;
    if (el && listRef.current) listRef.current.scrollTop = el.offsetTop - 60;
  }, [progs]);
  const archiveFrom = ch.archive ? now - (ch.archive_days || 1) * 86400 : Infinity;
  if (progs === null) return <div className="st-guide-list muted" style={{ padding: 20 }}>Chargement du guide…</div>;
  if (!progs.length) return <div className="st-guide-list muted" style={{ padding: 20 }}>Pas de guide des programmes pour cette chaîne.</div>;
  let lastDay = "";
  return (
    <div className="st-guide-list" ref={listRef}>
      {progs.map((p) => {
        const d = day(p.start);
        const head = d !== lastDay ? d : "";
        lastDay = d;
        const isNow = p.start <= now && now < p.stop;
        const past = p.stop <= now;
        const replay = past && p.start >= archiveFrom;
        return (
          <div key={p.start}>
            {head && <div className="st-day">{head}</div>}
            <div className={clsx("st-prog", isNow && "now", past && "past")}>
              <span className="st-prog-time mono">{hm(p.start)}</span>
              <span className="st-prog-text">
                <span className="st-prog-title">{p.title}</span>
                {isNow && <Bar value={progressOf(p, now)} />}
                {p.descr && <span className="st-prog-desc">{p.descr}</span>}
              </span>
              <span className="st-prog-actions">
                {replay && (
                  <Button size="sm" icon={<History size={14} />} onClick={async () => {
                    if (await play({ type: "replay", id: ch.id, start: p.start, stop: p.stop, title: p.title })) setPage("watch" as any);
                  }}>Replay</Button>
                )}
                {replay && (
                  <Button size="sm" iconOnly icon={<CircleDot size={14} />} tip="Enregistrer ce replay" onClick={async () => {
                    try {
                      await stream.record({ channel: ch.id, title: p.title, start: p.start, stop: p.stop, source: "replay" });
                      toast({ kind: "success", title: "Enregistrement du replay lancé", body: p.title });
                    } catch (e) { toastError(e); }
                  }} />
                )}
                {!past && (
                  <Button size="sm" iconOnly icon={<CircleDot size={14} />} tip="Programmer l'enregistrement" onClick={async () => {
                    try {
                      await stream.record({ channel: ch.id, title: p.title, start: p.start, stop: p.stop, source: "live" });
                      toast({ kind: "success", title: isNow ? "Enregistrement lancé" : "Enregistrement programmé", body: `${p.title} · ${hm(p.start)}` });
                    } catch (e) { toastError(e); }
                  }} />
                )}
                {p.start > now && (
                  <Button size="sm" iconOnly icon={<AlarmClock size={14} />} tip="Me prévenir avant" onClick={async () => {
                    try {
                      await stream.remind(ch.id, p.title, p.start);
                      toast({ kind: "success", title: "Rappel ajouté", body: `${p.title} · ${day(p.start)} ${hm(p.start)}` });
                    } catch (e) { toastError(e); }
                  }} />
                )}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function LivePage() {
  const nowTs = useNow(30000);
  const category = useStream((s) => s.liveCategory);
  const setCategory = useStream((s) => s.setLiveCategory);
  const version = useStream((s) => s.version);
  const playing = useStream((s) => s.now);
  const play = useStream((s) => s.play);
  const theater = useStream((s) => s.theater);
  const st = usePlayerState();
  const [cats, setCats] = useState<Category[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Item | null>(null);
  const [unlock, setUnlock] = useState(false);
  const loading = useRef(false);

  useEffect(() => {
    stream.categories("live").then((r) => setCats(r.categories)).catch(toastError);
  }, [version]);

  const load = useCallback(async (offset: number) => {
    if (loading.current) return;
    loading.current = true;
    try {
      const r = await stream.items("live", { category, q, offset, limit: 300 });
      setTotal(r.total);
      setItems((cur) => (offset ? [...cur, ...r.items] : r.items));
    } catch (e) {
      toastError(e);
    } finally {
      loading.current = false;
    }
  }, [category, q]);

  useEffect(() => {
    const t = window.setTimeout(() => load(0), q ? 180 : 0);
    return () => window.clearTimeout(t);
  }, [load, q, version]);

  // refresh "now playing" lines every minute
  useEffect(() => {
    const t = window.setInterval(() => load(0), 120000);
    return () => window.clearInterval(t);
  }, [load]);

  const current = useMemo(() => (playing?.kind === "live" ? playing.id : null), [playing]);
  const index = items.findIndex((c) => (selected ? c.id === selected.id : isOf(c, current)));
  useEffect(() => {
    if (current && !isOf(selected, current)) {
      const c = items.find((x) => isOf(x, current));
      if (c) setSelected(c);
    }
  }, [current, items]); // eslint-disable-line react-hooks/exhaustive-deps

  const open = (c: Item) => {
    setSelected(c);
    if (!isOf(c, current)) play({ type: "live", id: c.id });
  };

  // keyboard: ↑ ↓ zap in the list, digits, backspace = previous channel, F fullscreen, space pause
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA") || e.ctrlKey || e.altKey) return;
      if (useStream.getState().covers > 0) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "PageDown" || e.key === "PageUp") {
        e.preventDefault();
        const d = e.key === "ArrowDown" || e.key === "PageDown" ? 1 : -1;
        const i = index < 0 ? 0 : (index + d + items.length) % items.length;
        if (items[i]) open(items[i]);
      } else if (/^[0-9]$/.test(e.key)) {
        stream.cmd("digit", e.key).catch(() => undefined);
      } else if (e.key === "Backspace") {
        stream.cmd("previous").catch(() => undefined);
      } else if (e.key === " ") {
        e.preventDefault();
        stream.cmd("pause").catch(() => undefined);
      } else if (e.key.toLowerCase() === "f" || e.key === "Enter") {
        if (useStream.getState().now) useStream.getState().setTheater(!useStream.getState().theater);
      } else if (e.key === "Escape" && useStream.getState().theater) {
        useStream.getState().setTheater(false);
      } else if (e.key.toLowerCase() === "m") {
        stream.cmd("mute").catch(() => undefined);
      } else if (e.key.toLowerCase() === "i") {
        stream.cmd("osd", 6).catch(() => undefined);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }); // re-bound every render: always sees the current list

  const sel = selected;
  return (
    <div className={clsx("st-live", theater && "st-live-theater")}>
      <aside className="st-col st-col-cats glass-panel">
        <div className="st-col-head">TV en direct</div>
        <CategoryList cats={cats} value={category} onChange={(id) => { setCategory(id); setQ(""); }} onLocked={() => setUnlock(true)} />
      </aside>
      <section className="st-col st-col-list glass-panel">
        <div className="st-search">
          <Search size={15} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Chercher parmi ${total.toLocaleString("fr-FR")} chaînes`} spellCheck={false} />
          {q && <button onClick={() => setQ("")} aria-label="Effacer"><X size={14} /></button>}
        </div>
        {items.length ? (
          <VirtualList
            items={items}
            rowHeight={ROW}
            scrollTo={index}
            onEnd={() => items.length < total && load(items.length)}
            render={(c) => (
              <ChannelRow ch={c} now={nowTs} on={sel ? c.id === sel.id : isOf(c, current)} onClick={() => open(c)}
                onFav={() => toggleFav("live", c.id, !c.fav, () => setItems((cur) => cur.map((x) => (x.id === c.id ? { ...x, fav: !c.fav } : x))))} />
            )}
          />
        ) : (
          <div className="muted" style={{ padding: 24, textAlign: "center" }}>{q ? "Aucune chaîne trouvée." : category === "__fav" ? "Ajoute des chaînes en favori avec l'étoile." : "Aucune chaîne ici."}</div>
        )}
      </section>
      <section className="st-col st-col-main">
        <VideoSurface className="st-live-video" />
        <PlayerControls st={st} live extra={sel && <RecordNow channel={sel.id} title={sel.now?.title} />} />
        {sel ? (
          <div className="st-now glass-panel">
            <div className="st-now-head">
              <Logo src={sel.logo} name={sel.name} size={46} />
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="st-now-name">{sel.num ? <span className="mono st-now-num">{sel.num}</span> : null}{sel.name}</div>
                {sel.now && <div className="st-now-title">{sel.now.title} <span className="muted">· {hm(sel.now.start)} – {hm(sel.now.stop)}</span></div>}
                {sel.next && <div className="st-now-next muted">Ensuite : {sel.next.title} à {hm(sel.next.start)}</div>}
              </div>
              <Button iconOnly size="sm" icon={<Star size={16} />} active={sel.fav} tip={sel.fav ? "Retirer des favoris" : "Ajouter aux favoris"}
                onClick={() => toggleFav("live", sel.id, !sel.fav, () => { setSelected({ ...sel, fav: !sel.fav }); setItems((cur) => cur.map((x) => (x.id === sel.id ? { ...x, fav: !sel.fav } : x))); })} />
              <Button iconOnly size="sm" icon={<EyeOff size={16} />} tip="Masquer cette chaîne"
                onClick={async () => { await stream.hide("live", sel.id, true).catch(toastError); setItems((cur) => cur.filter((x) => x.id !== sel.id)); }} />
            </div>
            {!!sel.variants?.length && (
              <QualityPicker variants={sel.variants} current={isOf(sel, current) ? current : null} onPick={(id) => play({ type: "live", id })} />
            )}
            <ChannelGuide ch={sel} now={nowTs} />
          </div>
        ) : (
          <Empty icon={<Star size={22} />} title="Choisis une chaîne" body="↑ ↓ pour zapper, les chiffres pour aller à un numéro, F pour le plein écran." />
        )}
      </section>
      <UnlockModal open={unlock} onClose={() => setUnlock(false)} />
    </div>
  );
}
