import { CalendarClock, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Empty } from "../../components/ui";
import { useApp } from "../../store/app";
import { Item, Programme, stream } from "../../stream/api";
import { Logo, Poster, day, hm } from "../../stream/components";
import { toastError, useStream } from "../../stream/store";
import { MovieModal, SeriesModal } from "./VodPage";

type Results = { live: Item[]; movie: Item[]; series: Item[]; programmes: (Programme & { channel_id: string; channel_name: string; logo?: string })[] };

/** One box for everything: channels, films, series and programmes of the guide. */
export function SearchPage() {
  const setPage = useApp((s) => s.setPage);
  const play = useStream((s) => s.play);
  const q = useStream((s) => s.query);
  const [res, setRes] = useState<Results | null>(null);
  const [movie, setMovie] = useState<string | null>(null);
  const [series, setSeries] = useState<string | null>(null);

  useEffect(() => {
    (document.querySelector(".sx-search input") as HTMLInputElement | null)?.focus();
  }, []);
  useEffect(() => {
    if (q.trim().length < 2) {
      setRes(null);
      return;
    }
    const t = window.setTimeout(() => stream.search(q.trim()).then(setRes).catch(toastError), 220);
    return () => window.clearTimeout(t);
  }, [q]);

  const live = async (id: string) => {
    if (await play({ type: "live", id })) setPage("live" as any);
  };
  const now = Date.now() / 1000;
  const nothing = res && !res.live.length && !res.movie.length && !res.series.length && !res.programmes.length;

  return (
    <div className="st-page">

      <div className="page-head"><h1 className="page-title">{q ? `« ${q} »` : "Rechercher"}</h1></div>
      {!res && <p className="muted" style={{ marginTop: 8 }}>Tape au moins deux lettres. Les accents et les majuscules ne comptent pas.</p>}
      {nothing && <Empty icon={<Search size={22} />} title="Rien trouvé" body={`Aucun résultat pour « ${q} ».`} />}
      {res?.live.length ? (
        <section className="st-row">
          <h2 className="st-row-title">Chaînes</h2>
          <div className="st-row-items">
            {res.live.map((c) => (
              <button key={c.id} className="st-tile" onClick={() => live(c.id)}>
                <Logo src={c.logo} name={c.name} size={56} />
                <span>{c.name}</span>
                {c.now && <span className="st-tile-now">{c.now.title}</span>}
              </button>
            ))}
          </div>
        </section>
      ) : null}
      {res?.programmes.length ? (
        <section className="st-row">
          <h2 className="st-row-title">Programmes</h2>
          <div className="col" style={{ gap: 8 }}>
            {res.programmes.map((p) => (
              <button key={`${p.channel_id}-${p.start}`} className="st-rec glass-panel st-prog-hit" onClick={() => live(p.channel_id)}>
                <Logo src={p.logo} name={p.channel_name} size={38} />
                <div className="st-rec-text">
                  <div className="st-rec-title">{p.title}</div>
                  <div className="muted st-small"><CalendarClock size={12} /> {p.channel_name} · {day(p.start)} {hm(p.start)}{p.start <= now && now < p.stop ? " · en ce moment" : ""}</div>
                </div>
              </button>
            ))}
          </div>
        </section>
      ) : null}
      {res?.movie.length ? (
        <section className="st-row">
          <h2 className="st-row-title">Films</h2>
          <div className="st-row-items">{res.movie.map((m) => <Poster key={m.id} src={m.logo} name={m.name} progress={m.progress} sub={m.year ?? undefined} onClick={() => setMovie(m.id)} />)}</div>
        </section>
      ) : null}
      {res?.series.length ? (
        <section className="st-row">
          <h2 className="st-row-title">Séries</h2>
          <div className="st-row-items">{res.series.map((m) => <Poster key={m.id} src={m.logo} name={m.name} sub={m.year ?? undefined} onClick={() => setSeries(m.id)} />)}</div>
        </section>
      ) : null}
      <MovieModal id={movie} onClose={() => setMovie(null)} />
      <SeriesModal id={series} onClose={() => setSeries(null)} />
    </div>
  );
}
