import { ArrowDownUp, ChevronLeft, ChevronRight, Clapperboard, Compass, Ellipsis, Flame, Heart, Play, RefreshCw, Star, Tv } from "lucide-react";
import { ReactNode, useEffect, useMemo, useState } from "react";
import { useApp } from "../../store/app";
import { Category, Home, Item, Programme, stream } from "../../stream/api";
import { AccountForm } from "../../stream/AccountForm";
import { Bar, Logo, PlayDisc, Poster, progressOf, toggleFav, useNow } from "../../stream/components";
import { toastError, useStream } from "../../stream/store";
import { MovieModal, SeriesModal, playEpisode } from "./VodPage";

/** First launch: no source is bundled, the user adds their own subscription. */
export function Welcome() {
  return (
    <div className="st-welcome">
      <div className="st-welcome-text rise">
        <span className="eyebrow">se7en IPTV Player</span>
        <h1 className="st-welcome-title">Ajoute ton abonnement</h1>
        <p>
          Entre les identifiants Xtream Codes donnés par ton fournisseur, ou le lien de ta liste M3U. se7en IPTV Player ne fournit
          aucune chaîne : il lit l'abonnement que tu as déjà.
        </p>
        <ul className="st-welcome-list">
          <li><Tv size={16} /> TV en direct avec le guide des programmes</li>
          <li><Clapperboard size={16} /> Films et séries, reprise là où tu t'es arrêté</li>
          <li><RefreshCw size={16} /> Replay et enregistrements</li>
        </ul>
      </div>
      <div className="st-welcome-card glass-panel rise">
        <AccountForm onDone={() => useApp.getState().setPage("home")} />
      </div>
    </div>
  );
}

type Featured = Item & { kind: "movie" | "series" };
interface HeroInfo { art?: string; poster?: string; plot?: string; genres: string[]; fav?: boolean }

const img = (src?: string | null) => (src ? src : undefined);

function Hero({ items, index, setIndex, info, onOpen, onPlay, onFav }: { items: Featured[]; index: number; setIndex: (i: number) => void; info: HeroInfo | null; onOpen: () => void; onPlay: () => void; onFav: () => void }) {
  const m = items[index];
  if (!m) return <div className="sx-hero" />;
  const art = info?.art;
  const poster = info?.poster ?? m.logo;
  return (
    <div className="sx-hero">
      {poster && <div className="sx-hero-blur" style={{ backgroundImage: `url("${poster}")` }} />}
      <div key={m.kind + m.id} className={`sx-hero-art ${art ? "" : "is-poster"}`} style={{ backgroundImage: art || poster ? `url("${art || poster}")` : undefined }} />
      <div className="sx-hero-scrim" />
      <span className="ip-badge ip-badge--glass sx-hero-trend"><Flame size={13} color="var(--trending)" fill="rgba(255,138,61,.4)" /> {m.kind === "movie" ? "Nouveau film" : "Nouvelle série"}</span>
      <div className="sx-hero-body">
        {!!info?.genres.length && (
          <div className="sx-hero-chips">
            {info.genres.slice(0, 3).map((g) => <span key={g} className="ip-chip ip-chip--sm ip-chip--static">{g}</span>)}
          </div>
        )}
        <h1 className="sx-hero-title" onClick={onOpen} style={{ cursor: "pointer" }}>{m.name}</h1>
        {(info?.plot || m.plot) && <p className="sx-hero-desc">{info?.plot || m.plot}</p>}
        <div className="sx-hero-actions">
          <button type="button" className="ip-btn ip-btn--primary" onClick={onPlay}><Play size={16} /> Regarder</button>
          <button type="button" className="ip-btn ip-btn--outline" onClick={onFav}>
            <Heart size={16} fill={info?.fav ? "currentColor" : "none"} /> {info?.fav ? "Dans ma liste" : "Ma liste"}
          </button>
          <button type="button" className="ip-iconbtn ip-iconbtn--outline" aria-label="Plus d'infos" onClick={onOpen}><Ellipsis size={18} /></button>
        </div>
      </div>
      {items.length > 1 && (
        <>
          <div className="sx-hero-nav">
            <button type="button" className="ip-iconbtn ip-iconbtn--scrim ip-iconbtn--sm" aria-label="Précédent" onClick={() => setIndex((index - 1 + items.length) % items.length)}><ChevronLeft size={18} /></button>
            <button type="button" className="ip-iconbtn ip-iconbtn--scrim ip-iconbtn--sm" aria-label="Suivant" onClick={() => setIndex((index + 1) % items.length)}><ChevronRight size={18} /></button>
          </div>
          <div className="sx-hero-dots">{items.map((it, i) => <i key={it.kind + it.id} className={i === index ? "on" : ""} />)}</div>
        </>
      )}
    </div>
  );
}

function LikeCard({ it, onOpen, onPlay }: { it: Featured; onOpen: () => void; onPlay: () => void }) {
  const [bad, setBad] = useState(false);
  const chip = it.kind === "movie" ? "Film" : "Série";
  const desc = it.plot || [it.year, it.rating ? `★ ${Number(it.rating).toFixed(1)}` : null].filter(Boolean).join(" · ");
  return (
    <button type="button" className="sx-like" onClick={onOpen} title={it.name}>
      {it.logo && !bad ? <img src={it.logo} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBad(true)} /> : <span className="st-poster-fallback">{it.name}</span>}
      <span className="st-poster-scrim" />
      <span className="ip-iconbtn ip-iconbtn--scrim ip-iconbtn--sm sx-like-more" aria-hidden><Ellipsis size={15} /></span>
      <span className="sx-like-body">
        <span className="ip-chip ip-chip--sm ip-chip--static sx-like-chip">{chip}</span>
        <span className="sx-like-row">
          <span className="sx-like-text">
            <span className="sx-like-title">{it.name}{it.year ? ` (${it.year})` : ""}</span>
            {desc && <span className="sx-like-desc">{desc}</span>}
          </span>
          <PlayDisc onClick={onPlay} />
        </span>
      </span>
      {!!it.progress && it.progress > 0.01 && <span className="st-bar st-poster-bar"><i style={{ width: `${it.progress * 100}%` }} /></span>}
    </button>
  );
}

/** A horizontal row of posters (films or series). */
function Rail({ title, icon, items, kind, onOpen, onPlay, more }: { title: string; icon?: ReactNode; items: Item[]; kind: "movie" | "series"; onOpen: (it: Featured) => void; onPlay: (it: Featured) => void; more?: () => void }) {
  if (!items.length) return null;
  return (
    <section>
      <div className="sx-section-head">
        <h2 className="sx-section-title">{icon}{title}</h2>
        {more && <button type="button" className="ip-btn ip-btn--glass ip-btn--sm" onClick={more}>Tout voir</button>}
      </div>
      <div className="st-row-items sx-rail">
        {items.map((it) => (
          <Poster key={it.id} src={it.logo} name={it.name} progress={it.progress}
            sub={[it.year, it.rating ? `★ ${Number(it.rating).toFixed(1)}` : null].filter(Boolean).join(" · ") || undefined}
            onClick={() => onOpen({ ...it, kind })} onPlay={() => onPlay({ ...it, kind })} />
        ))}
      </div>
    </section>
  );
}

export function HomePage() {
  const version = useStream((s) => s.version);
  const play = useStream((s) => s.play);
  const setPage = useApp((s) => s.setPage);
  const [home, setHome] = useState<Home | null>(null);
  const [movies, setMovies] = useState<Item[]>([]);
  const [series, setSeries] = useState<Item[]>([]);
  const [topM, setTopM] = useState<Item[]>([]);
  const [topS, setTopS] = useState<Item[]>([]);
  const [cats, setCats] = useState<{ movie: Category[]; series: Category[] }>({ movie: [], series: [] });
  const [onAir, setOnAir] = useState<Record<string, Programme | null>>({});
  const clockNow = useNow(30000);
  const [idx, setIdx] = useState(0);
  const [info, setInfo] = useState<HeroInfo | null>(null);
  const [movie, setMovie] = useState<string | null>(null);
  const [serie, setSerie] = useState<string | null>(null);

  useEffect(() => {
    stream.home().then(setHome).catch(toastError);
    stream.items("movie", { category: "__recent", limit: 16 }).then((r) => setMovies(r.items)).catch(() => undefined);
    stream.items("series", { category: "__recent", limit: 16 }).then((r) => setSeries(r.items)).catch(() => undefined);
    stream.items("movie", { sort: "rating", limit: 30 }).then((r) => setTopM(r.items)).catch(() => undefined);
    stream.items("series", { sort: "rating", limit: 30 }).then((r) => setTopS(r.items)).catch(() => undefined);
    // the biggest categories, as chips to explore (adult ones stay hidden)
    const big = (c: Category[]) => c.filter((x) => !x.adult && !x.id.startsWith("__") && (x.n ?? 0) > 0).sort((a, b) => (b.n ?? 0) - (a.n ?? 0)).slice(0, 10);
    Promise.all([stream.categories("movie"), stream.categories("series")])
      .then(([m, sr]) => setCats({ movie: big(m.categories), series: big(sr.categories) }))
      .catch(() => undefined);
    // the first page of Films, Séries and TV en direct, made ready in the background: those tabs open at once
    const pre = window.setTimeout(() => {
      const live = useStream.getState().liveCategory;
      for (const k of ["movie", "series"] as const) stream.items(k, { category: "__all", q: "", offset: 0, limit: 120, sort: "default" }).catch(() => undefined);
      stream.categories("live").catch(() => undefined);
      stream.items("live", { category: live, q: "", offset: 0, limit: 300 }).catch(() => undefined);
    }, 1200);
    return () => window.clearTimeout(pre);
  }, [version]);

  // the hero: newest titles that have artwork, films and series in turn
  const featured = useMemo<Featured[]>(() => {
    const m = movies.filter((x) => x.logo).slice(0, 3).map((x) => ({ ...x, kind: "movie" as const }));
    const s = series.filter((x) => x.logo).slice(0, 2).map((x) => ({ ...x, kind: "series" as const }));
    const out: Featured[] = [];
    for (let i = 0; i < Math.max(m.length, s.length); i++) {
      if (m[i]) out.push(m[i]);
      if (s[i]) out.push(s[i]);
    }
    return out;
  }, [movies, series]);
  const fkeys = new Set(featured.map((f) => f.kind + f.id));
  const trailers: Featured[] = useMemo(() => {
    const out: Featured[] = [];
    const m = movies.map((x) => ({ ...x, kind: "movie" as const }));
    const s = series.map((x) => ({ ...x, kind: "series" as const }));
    for (let i = 0; i < 16; i++) {
      if (s[i]) out.push(s[i]);
      if (m[i]) out.push(m[i]);
    }
    return out.filter((x) => !fkeys.has(x.kind + x.id)).slice(0, 8);
  }, [movies, series]); // eslint-disable-line react-hooks/exhaustive-deps
  const likes: Featured[] = useMemo(() => {
    const out: Featured[] = [];
    for (let i = 0; i < 12; i++) {
      if (topM[i]) out.push({ ...topM[i], kind: "movie" });
      if (topS[i]) out.push({ ...topS[i], kind: "series" });
    }
    return out.filter((x) => !fkeys.has(x.kind + x.id)).slice(0, 10);
  }, [topM, topS]); // eslint-disable-line react-hooks/exhaustive-deps

  const cur = featured[idx % Math.max(1, featured.length)];
  useEffect(() => {
    if (!cur) return;
    let alive = true;
    setInfo(null);
    useStream.setState({ ambient: cur.logo ?? null });
    const req = cur.kind === "movie" ? stream.vod(cur.id) : stream.series(cur.id);
    req.then((d: any) => {
      if (!alive) return;
      const genres = String(d.genre || "").split(/[,/|]/).map((g: string) => g.trim()).filter(Boolean);
      setInfo({ art: d.backdrop || undefined, poster: d.poster || cur.logo, plot: d.plot, genres, fav: !!d.fav });
      if (d.backdrop) useStream.setState({ ambient: d.backdrop });
    }).catch(() => alive && setInfo({ genres: [] }));
    return () => {
      alive = false;
    };
  }, [cur?.kind, cur?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // the hero moves on by itself every 9 s
  useEffect(() => {
    if (featured.length < 2) return;
    const t = window.setTimeout(() => setIdx((i) => (i + 1) % featured.length), 9000);
    return () => window.clearTimeout(t);
  }, [idx, featured.length]);

  const open = (it: Featured) => (it.kind === "movie" ? setMovie(it.id) : setSerie(it.id));
  const playItem = async (it: Featured) => {
    if (it.kind === "series") return setSerie(it.id);
    if (await play({ type: "movie", id: it.id })) setPage("watch");
  };
  const resume = async (h: Home["continue"][number]) => {
    if (h.type === "movie") {
      if (await play({ type: "movie", id: h.item })) setPage("watch");
    } else if (h.extra.series) {
      const s = /^S(\S+) E/.exec(h.title);
      playEpisode(h.extra.series, { id: h.item, episode: null, title: h.title }, s?.[1] ?? "");
    }
  };
  const live = async (id: string) => {
    if (await play({ type: "live", id })) setPage("live");
  };
  const channels = home?.favorites.length ? home.favorites : home?.recent ?? [];
  // recent channels come without their programme: ask the guide for what is on now
  const chanKey = channels.slice(0, 10).map((c) => c.id).join(",");
  useEffect(() => {
    let alive = true;
    channels.slice(0, 10).forEach((c: any) => {
      if (c.now) return;
      stream.channelEpg(c.id).then((r) => {
        const t = Date.now() / 1000;
        const cur = r.programmes.find((p) => p.start <= t && t < p.stop) ?? null;
        if (alive) setOnAir((m) => ({ ...m, [c.id]: cur }));
      }).catch(() => undefined);
    });
    return () => {
      alive = false;
    };
  }, [chanKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const likeKeys = new Set(likes.map((l) => l.kind + l.id));
  const topFilms = topM.filter((x) => !likeKeys.has("movie" + x.id) && !fkeys.has("movie" + x.id)).slice(0, 16);
  const topSeries = topS.filter((x) => !likeKeys.has("series" + x.id) && !fkeys.has("series" + x.id)).slice(0, 16);
  const explore = (kind: "movie" | "series", id: string) => {
    useStream.setState({ vodCategory: id });
    setPage(kind === "movie" ? "movies" : "series");
  };

  return (
    <div className="sx-home">
      <aside className="sx-side">
        <section className="glass-panel sx-side-panel" style={{ flex: "1 1 55%" }}>
          <div className="sx-side-head">
            <h2 className="sx-side-title"><Flame size={16} color="var(--trending)" fill="rgba(255,138,61,.35)" /> Nouveautés</h2>
            <span className="sx-side-meta">Tri : <b>Récents</b> <ArrowDownUp size={12} /></span>
          </div>
          <div className="sx-side-scroll">
            {trailers.map((t) => (
              <button key={t.kind + t.id} type="button" className="sx-trailer" onClick={() => open(t)} title={t.name}>
                {img(t.logo) ? <img src={t.logo} alt="" loading="lazy" referrerPolicy="no-referrer" style={{ objectPosition: "center 22%" }} /> : <span className="st-poster-fallback">{t.name}</span>}
                <span className="st-poster-scrim" />
                <span className="sx-trailer-info">
                  <span className="sx-trailer-title">{t.name}</span>
                  <PlayDisc small onClick={() => playItem(t)} />
                </span>
              </button>
            ))}
            {!trailers.length && <p className="muted st-small">Le catalogue se charge…</p>}
          </div>
        </section>
        <section className="glass-panel sx-side-panel" style={{ flex: "1 1 45%" }}>
          <div className="sx-side-head">
            <h2 className="sx-side-title">{home?.continue.length ? "Continuer à regarder" : "Chaînes récentes"}</h2>
          </div>
          <div className="sx-side-scroll" style={{ gap: 10 }}>
            {home?.continue.length
              ? home.continue.map((h) => (
                  <button key={h.type + h.item} type="button" className="sx-cw" onClick={() => resume(h)}>
                    <span className="sx-cw-thumb">
                      {h.logo && <img src={h.logo} alt="" loading="lazy" referrerPolicy="no-referrer" />}
                      <Bar value={h.progress} />
                    </span>
                    <span className="sx-cw-text">
                      <span className="sx-cw-title">{h.title}</span>
                      <span className="sx-cw-sub">{h.type === "movie" ? `Film · ${Math.max(1, Math.round((h.duration - h.position) / 60))} min restantes` : "Épisode en cours"}</span>
                    </span>
                    <PlayDisc small />
                  </button>
                ))
              : (home?.recent ?? []).map((c) => (
                  <button key={c.id} type="button" className="sx-cw" onClick={() => live(c.id)}>
                    <Logo src={c.logo} name={c.name} size={44} />
                    <span className="sx-cw-text"><span className="sx-cw-title">{c.name}</span><span className="sx-cw-sub">En direct</span></span>
                    <PlayDisc small />
                  </button>
                ))}
            {home && !home.continue.length && !home.recent.length && <p className="muted st-small">Ce que tu commences à regarder apparaîtra ici.</p>}
          </div>
        </section>
      </aside>

      <div className="sx-feed">
        <Hero items={featured} index={idx % Math.max(1, featured.length)} setIndex={setIdx} info={info}
          onOpen={() => cur && open(cur)} onPlay={() => cur && playItem(cur)}
          onFav={() => cur && toggleFav(cur.kind, cur.id, !info?.fav, () => setInfo((i) => (i ? { ...i, fav: !i.fav } : i)))} />

        {likes.length > 0 && (
          <section>
            <div className="sx-section-head">
              <h2 className="sx-section-title">Tu pourrais aimer</h2>
              <button type="button" className="ip-btn ip-btn--glass ip-btn--sm" onClick={() => setPage("movies")}>Tout voir</button>
            </div>
            <div className="sx-cards">
              {likes.map((it) => <LikeCard key={it.kind + it.id} it={it} onOpen={() => open(it)} onPlay={() => playItem(it)} />)}
            </div>
          </section>
        )}

        {channels.length > 0 && (
          <section>
            <div className="sx-section-head">
              <h2 className="sx-section-title"><Tv size={17} /> {home?.favorites.length ? "Tes chaînes en ce moment" : "En ce moment sur tes chaînes"}</h2>
              <button type="button" className="ip-btn ip-btn--glass ip-btn--sm" onClick={() => setPage("live")}>TV en direct</button>
            </div>
            <div className="sx-chan-row">
              {channels.slice(0, 10).map((c: any) => {
                const p: Programme | null | undefined = c.now ?? onAir[c.id];
                return (
                  <button key={c.id} type="button" className="st-tile sx-onair" onClick={() => live(c.id)}>
                    <Logo src={c.logo} name={c.name} size={40} />
                    <span className="st-tile-text">
                      <span>{c.name}</span>
                      <span className="st-tile-now">{p?.title ?? "En direct"}</span>
                      {p && <Bar value={progressOf(p, clockNow)} />}
                    </span>
                  </button>
                );
              })}
            </div>
          </section>
        )}

        <Rail title="Films ajoutés récemment" icon={<Clapperboard size={17} />} items={movies} kind="movie" onOpen={open} onPlay={playItem} more={() => setPage("movies")} />
        <Rail title="Séries ajoutées récemment" icon={<Tv size={17} />} items={series} kind="series" onOpen={open} onPlay={playItem} more={() => setPage("series")} />
        <Rail title="Films les mieux notés" icon={<Star size={17} />} items={topFilms} kind="movie" onOpen={open} onPlay={playItem} />
        <Rail title="Séries les mieux notées" icon={<Star size={17} />} items={topSeries} kind="series" onOpen={open} onPlay={playItem} />

        {(cats.movie.length > 0 || cats.series.length > 0) && (
          <section className="sx-explore">
            <div className="sx-section-head"><h2 className="sx-section-title"><Compass size={17} /> Explorer</h2></div>
            {([["movie", "Films"], ["series", "Séries"]] as const).map(([k, label]) => cats[k].length > 0 && (
              <div key={k} className="sx-explore-row">
                <span className="sx-explore-label">{label}</span>
                {cats[k].map((c) => (
                  <button key={c.id} type="button" className="ip-chip" onClick={() => explore(k, c.id)}>
                    {c.name}<span className="sx-explore-n">{c.n}</span>
                  </button>
                ))}
              </div>
            ))}
          </section>
        )}
      </div>
      <MovieModal id={movie} onClose={() => setMovie(null)} />
      <SeriesModal id={serie} onClose={() => setSerie(null)} />
    </div>
  );
}
