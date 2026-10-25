import clsx from "clsx";
import { Check, Clapperboard, Play, RotateCcw, Search, Star, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { Button, Empty, Modal, Select } from "../../components/ui";
import { useApp } from "../../store/app";
import { Category, Item, SeriesInfo, VodInfo, stream } from "../../stream/api";
import { Bar, Poster, UnlockModal, clock, toggleFav } from "../../stream/components";
import { toastError, useStream } from "../../stream/store";
import { CategoryList } from "./LivePage";

const SORTS = [
  { value: "default", label: "Ordre du fournisseur" },
  { value: "recent", label: "Ajouts récents" },
  { value: "name", label: "Nom" },
  { value: "rating", label: "Note" },
  { value: "year", label: "Année" },
];

async function playMovie(id: string, resume = true): Promise<void> {
  if (await useStream.getState().play({ type: "movie", id, resume })) useApp.getState().setPage("watch" as any);
}

export async function playEpisode(series: string, ep: { id: string; episode: number | null; title: string }, season: string, resume = true): Promise<void> {
  const title = `S${season} E${ep.episode ?? "?"}${ep.title ? ` · ${ep.title}` : ""}`;
  if (await useStream.getState().play({ type: "episode", id: ep.id, series, title, resume })) useApp.getState().setPage("watch" as any);
}

export function MovieModal({ id, onClose }: { id: string | null; onClose: () => void }) {
  const [info, setInfo] = useState<VodInfo | null>(null);
  useEffect(() => {
    setInfo(null);
    if (id) stream.vod(id).then(setInfo).catch((e) => { toastError(e); onClose(); });
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  const i = info;
  return (
    <Modal open={!!id} onClose={onClose} width={760} eyebrow={<><Clapperboard size={12} /> Film</>} title={i?.name ?? "…"}>
      {i && (
        <div className="st-detail">
          <Poster src={i.poster || i.logo} name={i.name} />
          <div className="col" style={{ gap: 10, minWidth: 0 }}>
            <div className="st-meta">
              {i.year && <span>{i.year}</span>}
              {i.duration && <span>{i.duration}</span>}
              {i.rating ? <span><Star size={12} /> {Number(i.rating).toFixed(1)}</span> : null}
              {i.genre && <span>{i.genre}</span>}
            </div>
            {i.plot && <p className="st-plot">{i.plot}</p>}
            {i.director && <p className="st-small"><b>Réalisation</b> {i.director}</p>}
            {i.cast && <p className="st-small"><b>Avec</b> {i.cast}</p>}
            <div className="row" style={{ gap: 10, flexWrap: "wrap", marginTop: 6 }}>
              {i.resume > 0 ? (
                <>
                  <Button variant="primary" icon={<Play size={15} />} onClick={() => { onClose(); playMovie(i.id, true); }}>Reprendre à {clock(i.resume)}</Button>
                  <Button icon={<RotateCcw size={15} />} onClick={() => { onClose(); playMovie(i.id, false); }}>Depuis le début</Button>
                </>
              ) : (
                <Button variant="primary" icon={<Play size={15} />} onClick={() => { onClose(); playMovie(i.id, false); }}>Regarder</Button>
              )}
              <Button icon={<Star size={15} />} active={(i as any).fav} onClick={() => toggleFav("movie", i.id, !(i as any).fav, () => setInfo({ ...i, fav: !(i as any).fav } as VodInfo))}>
                {(i as any).fav ? "Dans les favoris" : "Favori"}
              </Button>
              {i.trailer && <Button onClick={() => api.post("/api/stream/trailer", { id: i.trailer }).catch(toastError)}>Bande-annonce</Button>}
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}

export function SeriesModal({ id, onClose }: { id: string | null; onClose: () => void }) {
  const [info, setInfo] = useState<SeriesInfo | null>(null);
  const [season, setSeason] = useState("");
  const load = useCallback((refresh = false) => {
    if (!id) return;
    stream.series(id, refresh).then((r) => {
      setInfo(r);
      setSeason((cur) => {
        if (cur && r.seasons.some((s) => s.season === cur)) return cur;
        // the first season that still has unwatched episodes
        return (r.seasons.find((s) => s.episodes.some((e) => e.progress > 0.01 && !e.watched)) ?? r.seasons.find((s) => s.episodes.some((e) => !e.watched)) ?? r.seasons[0])?.season ?? "";
      });
    }).catch((e) => { toastError(e); onClose(); });
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    setInfo(null);
    setSeason("");
    load();
  }, [load]);
  const i = info;
  const eps = i?.seasons.find((s) => s.season === season)?.episodes ?? [];
  // where to pick up: an episode started and not finished, else the first unwatched one after the last watched
  const flat = i?.seasons.flatMap((s) => s.episodes.map((e) => ({ e, s: s.season }))) ?? [];
  const lastWatched = flat.map((x) => x.e.watched).lastIndexOf(true);
  const nextUp = flat.find(({ e }) => e.progress > 0.01 && !e.watched) ?? flat.slice(lastWatched + 1).find(({ e }) => !e.watched) ?? flat.find(({ e }) => !e.watched);
  return (
    <Modal open={!!id} onClose={onClose} width={860} eyebrow={<><Clapperboard size={12} /> Série</>} title={i?.name ?? "…"}>
      {i && (
        <div className="col" style={{ gap: 14 }}>
          <div className="st-detail">
            <Poster src={i.logo} name={i.name} />
            <div className="col" style={{ gap: 10, minWidth: 0 }}>
              <div className="st-meta">
                {i.year && <span>{i.year}</span>}
                {i.rating ? <span><Star size={12} /> {Number(i.rating).toFixed(1)}</span> : null}
                {i.genre && <span>{i.genre}</span>}
                <span>{i.seasons.length} saison{i.seasons.length > 1 ? "s" : ""}</span>
              </div>
              {i.plot && <p className="st-plot">{i.plot}</p>}
              {i.cast && <p className="st-small"><b>Avec</b> {i.cast}</p>}
              <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
                {nextUp && (
                  <Button variant="primary" icon={<Play size={15} />} onClick={() => { onClose(); playEpisode(i.id, nextUp.e, nextUp.s); }}>
                    {nextUp.e.progress > 0 ? "Reprendre" : "Regarder"} S{nextUp.s} E{nextUp.e.episode ?? "?"}
                  </Button>
                )}
                <Button icon={<Star size={15} />} active={(i as any).fav} onClick={() => toggleFav("series", i.id, !(i as any).fav, () => setInfo({ ...i, fav: !(i as any).fav } as SeriesInfo))}>
                  {(i as any).fav ? "Dans les favoris" : "Favori"}
                </Button>
              </div>
            </div>
          </div>
          {i.seasons.length > 0 ? (
            <>
              <div className="st-seasons">
                {i.seasons.map((s) => (
                  <button key={s.season} className={clsx("st-chip", s.season === season && "on")} onClick={() => setSeason(s.season)}>
                    Saison {s.season}
                  </button>
                ))}
              </div>
              <div className="st-episodes">
                {eps.map((e) => (
                  <div key={e.id} className={clsx("st-ep", e.watched && "watched")}>
                    <button className="st-ep-main" onClick={() => { onClose(); playEpisode(i.id, e, season); }}>
                      <span className="st-ep-n mono">{e.episode ?? "·"}</span>
                      <span className="st-ep-text">
                        <span className="st-ep-title">{e.title || `Épisode ${e.episode ?? ""}`}</span>
                        {e.plot && <span className="st-ep-plot">{e.plot}</span>}
                        {e.progress > 0.01 && !e.watched && <Bar value={e.progress} />}
                      </span>
                      {e.duration && <span className="st-ep-dur muted mono">{e.duration}</span>}
                    </button>
                    <button className={clsx("st-ep-check", e.watched && "on")} data-tip={e.watched ? "Marquer comme non vu" : "Marquer comme vu"}
                      onClick={async () => { await stream.watched("episode", e.id, !e.watched).catch(toastError); load(); }}>
                      <Check size={14} />
                    </button>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="muted">Aucun épisode pour le moment. <button className="st-link" onClick={() => load(true)}>Actualiser</button></p>
          )}
        </div>
      )}
    </Modal>
  );
}

export function VodPage({ kind }: { kind: "movie" | "series" }) {
  const version = useStream((s) => s.version);
  const [cats, setCats] = useState<Category[]>([]);
  const [cat, setCat] = useState("__all");
  const [sort, setSort] = useState("default");
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [total, setTotal] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const [unlock, setUnlock] = useState(false);
  const busy = useRef(false);
  const grid = useRef<HTMLDivElement>(null);

  useEffect(() => {
    stream.categories(kind).then((r) => setCats(r.categories)).catch(toastError);
  }, [kind, version]);

  const load = useCallback(async (offset: number) => {
    if (busy.current) return;
    busy.current = true;
    try {
      const r = await stream.items(kind, { category: cat, q, offset, limit: 120, sort });
      setTotal(r.total);
      setItems((cur) => (offset ? [...cur, ...r.items] : r.items));
    } catch (e) {
      toastError(e);
    } finally {
      busy.current = false;
    }
  }, [kind, cat, q, sort]);

  useEffect(() => {
    const t = window.setTimeout(() => {
      load(0);
      grid.current?.scrollTo({ top: 0 });
    }, q ? 200 : 0);
    return () => window.clearTimeout(t);
  }, [load, q, version]);

  const word = kind === "movie" ? "films" : "séries";
  return (
    <div className="st-vod">
      <aside className="st-col st-col-cats glass-panel">
        <div className="st-col-head">{kind === "movie" ? "Films" : "Séries"}</div>
        <CategoryList cats={cats} value={cat} onChange={(c) => { setCat(c); setQ(""); }} onLocked={() => setUnlock(true)} />
      </aside>
      <section className="st-vod-main">
        <div className="row st-vod-bar">
          <div className="st-search" style={{ flex: 1 }}>
            <Search size={15} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Chercher parmi ${total.toLocaleString("fr-FR")} ${word}`} spellCheck={false} />
            {q && <button onClick={() => setQ("")} aria-label="Effacer"><X size={14} /></button>}
          </div>
          <div style={{ width: 210 }}>
            <Select value={sort} onChange={(v) => setSort(String(v))} options={SORTS} />
          </div>
        </div>
        {items.length ? (
          <div className="st-grid" ref={grid} onScroll={(e) => {
            const el = e.currentTarget;
            if (items.length < total && el.scrollTop + el.clientHeight > el.scrollHeight - 600) load(items.length);
          }}>
            {items.map((it) => (
              <Poster key={it.id} src={it.logo} name={it.name} progress={it.progress} onClick={() => setOpen(it.id)}
                badge={it.rating ? <><Star size={10} /> {Number(it.rating).toFixed(1)}</> : undefined}
                sub={it.year ?? undefined} />
            ))}
          </div>
        ) : (
          <Empty icon={<Clapperboard size={22} />} title={q ? "Rien trouvé" : cat === "__fav" ? "Pas encore de favoris" : `Aucun ${kind === "movie" ? "film" : "série"} ici`}
            body={cat === "__fav" ? "Ouvre une fiche et ajoute-la en favori." : undefined} />
        )}
      </section>
      {kind === "movie" ? <MovieModal id={open} onClose={() => { setOpen(null); }} /> : <SeriesModal id={open} onClose={() => setOpen(null)} />}
      <UnlockModal open={unlock} onClose={() => setUnlock(false)} />
    </div>
  );
}
