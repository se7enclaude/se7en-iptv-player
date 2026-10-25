import clsx from "clsx";
import { AlarmClock, CalendarDays, ChevronLeft, ChevronRight, CircleDot, History, Play } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Modal, Select } from "../../components/ui";
import { useApp } from "../../store/app";
import { Category, Item, Programme, stream } from "../../stream/api";
import { Logo, day, hm, useNow } from "../../stream/components";
import { toastError, useStream } from "../../stream/store";

const PX_MIN = 5; // pixels per minute
const HOURS = 6;
const ROW = 62;
const NAME_W = 230;

const halfHour = (t: number) => Math.floor(t / 1800) * 1800;

export function GuidePage() {
  const now = useNow(30000);
  const version = useStream((s) => s.version);
  const play = useStream((s) => s.play);
  const setPage = useApp((s) => s.setPage);
  const toast = useApp((s) => s.toast);
  const [cats, setCats] = useState<Category[]>([]);
  const [cat, setCat] = useState(useStream.getState().liveCategory);
  const [start, setStart] = useState(() => halfHour(Date.now() / 1000) - 3600);
  const [rows, setRows] = useState<Item[]>([]);
  const [total, setTotal] = useState(0);
  const [pick, setPick] = useState<{ ch: Item; p: Programme } | null>(null);
  const busy = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);
  const end = start + HOURS * 3600;

  useEffect(() => {
    stream.categories("live").then((r) => setCats(r.categories.filter((c) => c.id !== "__recent"))).catch(toastError);
  }, [version]);

  const load = useCallback(async (offset: number) => {
    if (busy.current) return;
    busy.current = true;
    try {
      const r = await stream.guide({ category: cat, start, hours: HOURS, offset, limit: 60 });
      setTotal(r.total);
      setRows((cur) => (offset ? [...cur, ...r.channels] : r.channels));
    } catch (e) {
      toastError(e);
    } finally {
      busy.current = false;
    }
  }, [cat, start]);

  useEffect(() => {
    load(0);
  }, [load, version]);

  // keep "now" in view when the window contains it
  useEffect(() => {
    const el = scroller.current;
    if (el && now >= start && now < end) el.scrollLeft = Math.max(0, ((now - start) / 60) * PX_MIN - 160);
  }, [start]); // eslint-disable-line react-hooks/exhaustive-deps

  const days = useMemo(() => {
    const out = [];
    const today = new Date(new Date().toDateString()).getTime() / 1000;
    for (let d = -7; d <= 7; d++) out.push({ value: String(today + d * 86400), label: d === 0 ? "Aujourd'hui" : day(today + d * 86400 + 3600) });
    return out;
  }, []);
  const dayValue = String(new Date(new Date(start * 1000).toDateString()).getTime() / 1000);
  const ticks = [];
  for (let t = start; t < end; t += 1800) ticks.push(t);
  const width = HOURS * 60 * PX_MIN;

  const replayable = (ch: Item, p: Programme) => !!ch.archive && p.stop <= now && p.start >= now - (ch.archive_days || 1) * 86400;

  return (
    <div className="st-guide">
      <div className="row st-guide-bar">
        <div style={{ width: 230 }}>
          <Select value={cat} onChange={(v) => setCat(String(v))} options={cats.map((c) => ({ value: c.id, label: c.id === "__all" ? "Toutes les chaînes" : c.id === "__fav" ? "Favoris" : c.name }))} />
        </div>
        <div style={{ width: 230 }}>
          <Select value={dayValue} onChange={(v) => setStart(Number(v) + 18 * 3600)} options={days.map((d) => ({ ...d, icon: <CalendarDays size={14} /> }))} />
        </div>
        <span className="spacer" />
        <Button iconOnly icon={<ChevronLeft size={17} />} tip="3 h plus tôt" onClick={() => setStart(start - 3 * 3600)} />
        <Button onClick={() => setStart(halfHour(Date.now() / 1000) - 3600)}>Maintenant</Button>
        <Button iconOnly icon={<ChevronRight size={17} />} tip="3 h plus tard" onClick={() => setStart(start + 3 * 3600)} />
      </div>
      <div className="st-epg glass-panel" ref={scroller} onScroll={(e) => {
        const el = e.currentTarget;
        if (rows.length < total && el.scrollTop + el.clientHeight > el.scrollHeight - ROW * 8) load(rows.length);
      }}>
        <div className="st-epg-inner" style={{ width: NAME_W + width }}>
          <div className="st-epg-times">
            <span className="st-epg-corner" style={{ width: NAME_W, padding: 0, border: 0 }} />
            {ticks.map((t) => (
              <span key={t} style={{ width: 30 * PX_MIN }} className="mono">{hm(t)}</span>
            ))}
          </div>
          {now >= start && now < end && <div className="st-epg-nowline" style={{ left: NAME_W + ((now - start) / 60) * PX_MIN }} />}
          {rows.map((ch) => (
            <div key={ch.id} className="st-epg-row" style={{ height: ROW }}>
              <button className="st-epg-ch" style={{ width: NAME_W }} onClick={async () => { if (await play({ type: "live", id: ch.id })) setPage("live" as any); }}>
                <span className="mono st-ch-num">{ch.num ?? ""}</span>
                <Logo src={ch.logo} name={ch.name} size={34} />
                <span className="st-epg-chname">{ch.name}</span>
              </button>
              <div className="st-epg-progs" style={{ width }}>
                {(ch.programmes ?? []).map((p) => {
                  const a = Math.max(p.start, start);
                  const b = Math.min(p.stop, end);
                  if (b <= a) return null;
                  const live = p.start <= now && now < p.stop;
                  return (
                    <button key={p.start} className={clsx("st-epg-prog", live && "now", p.stop <= now && "past", replayable(ch, p) && "replay")}
                      style={{ left: ((a - start) / 60) * PX_MIN, width: Math.max(4, ((b - a) / 60) * PX_MIN - 3) }}
                      onClick={() => setPick({ ch, p })} title={`${hm(p.start)} ${p.title}`}>
                      <span className="st-epg-title">{p.title}</span>
                      <span className="st-epg-time mono">{hm(p.start)}</span>
                    </button>
                  );
                })}
                {!ch.programmes?.length && <span className="st-epg-none">Pas de programme</span>}
              </div>
            </div>
          ))}
        </div>
      </div>
      <Modal open={!!pick} onClose={() => setPick(null)} width={560} eyebrow={pick ? `${pick.ch.name} · ${day(pick.p.start)}` : ""} title={pick?.p.title}>
        {pick && (
          <div className="col" style={{ gap: 12 }}>
            <div className="st-meta">
              <span>{hm(pick.p.start)} – {hm(pick.p.stop)}</span>
              <span>{Math.round((pick.p.stop - pick.p.start) / 60)} min</span>
              {pick.p.category && <span>{pick.p.category}</span>}
            </div>
            {pick.p.descr && <p className="st-plot">{pick.p.descr}</p>}
            <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
              {pick.p.start <= now && now < pick.p.stop && (
                <Button variant="primary" icon={<Play size={15} />} onClick={async () => { const c = pick.ch; setPick(null); if (await play({ type: "live", id: c.id })) setPage("live" as any); }}>Regarder en direct</Button>
              )}
              {replayable(pick.ch, pick.p) && (
                <Button variant="primary" icon={<History size={15} />} onClick={async () => {
                  const { ch, p } = pick;
                  setPick(null);
                  if (await play({ type: "replay", id: ch.id, start: p.start, stop: p.stop, title: p.title })) setPage("watch" as any);
                }}>Regarder en replay</Button>
              )}
              {(pick.p.stop > now || replayable(pick.ch, pick.p)) && (
                <Button icon={<CircleDot size={15} />} onClick={async () => {
                  const { ch, p } = pick;
                  try {
                    await stream.record({ channel: ch.id, title: p.title, start: p.start, stop: p.stop, source: p.stop <= now ? "replay" : "live" });
                    toast({ kind: "success", title: p.start > now ? "Enregistrement programmé" : "Enregistrement lancé", body: p.title });
                    setPick(null);
                  } catch (e) { toastError(e); }
                }}>Enregistrer</Button>
              )}
              {pick.p.start > now && (
                <Button icon={<AlarmClock size={15} />} onClick={async () => {
                  const { ch, p } = pick;
                  try {
                    await stream.remind(ch.id, p.title, p.start);
                    toast({ kind: "success", title: "Rappel ajouté", body: "Une notification 2 minutes avant." });
                    setPick(null);
                  } catch (e) { toastError(e); }
                }}>Me prévenir</Button>
              )}
            </div>
            {!pick.ch.archive && pick.p.stop <= now && <p className="st-small muted">Cette chaîne n'a pas de replay.</p>}
          </div>
        )}
      </Modal>
    </div>
  );
}
