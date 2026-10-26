import clsx from "clsx";
import { Baby, Heart, Lock, Pencil, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { Button, Empty, Modal } from "../../components/ui";
import { useApp } from "../../store/app";
import { Item, Profile, stream } from "../../stream/api";
import { Logo, PinField, Poster } from "../../stream/components";
import { profileColor } from "../../stream/Shell";
import { toastError, useStream } from "../../stream/store";
import { MovieModal, SeriesModal } from "./VodPage";

/** « Ma liste » : favourite channels, films and series of the current profile. */
export function MyListPage() {
  const version = useStream((s) => s.version);
  const play = useStream((s) => s.play);
  const setPage = useApp((s) => s.setPage);
  const [live, setLive] = useState<Item[]>([]);
  const [movies, setMovies] = useState<Item[]>([]);
  const [series, setSeries] = useState<Item[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [movie, setMovie] = useState<string | null>(null);
  const [serie, setSerie] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      stream.items("live", { category: "__fav", limit: 200 }),
      stream.items("movie", { category: "__fav", limit: 200 }),
      stream.items("series", { category: "__fav", limit: 200 }),
    ]).then(([l, m, s]) => {
      setLive(l.items);
      setMovies(m.items);
      setSeries(s.items);
      setLoaded(true);
    }).catch(toastError);
  }, [version]);

  const empty = loaded && !live.length && !movies.length && !series.length;
  return (
    <div className="st-page">
      <div className="page-head"><h1 className="page-title">Ma liste</h1></div>
      {empty && <Empty icon={<Heart size={22} />} title="Ta liste est vide" body="Ajoute des chaînes avec l'étoile, et des films ou des séries depuis leur fiche." />}
      {live.length > 0 && (
        <section className="st-row">
          <h2 className="st-row-title">Chaînes</h2>
          <div className="sx-chan-row">
            {live.map((c) => (
              <button key={c.id} type="button" className="st-tile" onClick={async () => { if (await play({ type: "live", id: c.id })) setPage("live"); }}>
                <Logo src={c.logo} name={c.name} size={40} />
                <span className="st-tile-text"><span>{c.name}</span><span className="st-tile-now">{c.now?.title ?? "En direct"}</span></span>
              </button>
            ))}
          </div>
        </section>
      )}
      {movies.length > 0 && (
        <section className="st-row">
          <h2 className="st-row-title">Films</h2>
          <div className="sx-cards">{movies.map((m) => <Poster key={m.id} src={m.logo} name={m.name} sub={m.year ?? undefined} progress={m.progress} onClick={() => setMovie(m.id)} />)}</div>
        </section>
      )}
      {series.length > 0 && (
        <section className="st-row">
          <h2 className="st-row-title">Séries</h2>
          <div className="sx-cards">{series.map((m) => <Poster key={m.id} src={m.logo} name={m.name} sub={m.year ?? undefined} onClick={() => setSerie(m.id)} />)}</div>
        </section>
      )}
      <MovieModal id={movie} onClose={() => setMovie(null)} />
      <SeriesModal id={serie} onClose={() => setSerie(null)} />
    </div>
  );
}

/** « Qui regarde ? » */
/** « Qui regarde ? ». At launch (gate) it comes first, before the app : a protected profile always asks its code there,
 * and choosing a profile opens the app on its subscription. */
export function ProfilesPage({ gate, onPicked }: { gate?: boolean; onPicked?: () => void } = {}) {
  const state = useStream((s) => s.state);
  const setPage = useApp((s) => s.setPage);
  const goSettingsRaw = useApp((s) => s.goSettings);
  const goSettings = (section: string) => {
    onPicked?.();
    goSettingsRaw(section as any);
  };
  const toast = useApp((s) => s.toast);
  const [locked, setLocked] = useState<Profile | null>(null);
  const [pin, setPin] = useState("");
  const [err, setErr] = useState("");

  const choose = async (p: Profile, code = "") => {
    try {
      await stream.selectProfile(p.id, code);
      await useStream.getState().load();
      useStream.getState().bump();
      setLocked(null);
      toast({ kind: "success", title: `Bonjour ${p.name}` });
      setPage("home");
      onPicked?.();
    } catch (e: any) {
      if (locked) setErr(e?.message ?? "Code incorrect");
      else toastError(e);
    }
  };
  return (
    <div className={clsx("sx-who", gate && "sx-who--gate")}>
      {gate && <span className="eyebrow">se7en IPTV Player</span>}
      <h1>Qui regarde ?</h1>
      <div className="sx-who-list">
        {state?.profiles.map((p, i) => (
          <button key={p.id} type="button" className={clsx("ip-profile", p.id === state.profile && "is-focused")}
            onClick={() => (p.locked && (gate || p.id !== state.profile) ? (setLocked(p), setPin(""), setErr("")) : choose(p))}>
            <span className="ip-profile__tile" style={{ background: `linear-gradient(160deg, ${profileColor(p.color, i)} 0%, rgba(20,22,26,.9) 130%)` }}>
              {p.kids ? <Baby size={44} strokeWidth={1.5} /> : p.name.slice(0, 1).toUpperCase()}
              {p.locked && <span className="ip-profile__lock"><Lock size={14} /></span>}
            </span>
            <span className="ip-profile__name">{p.name}</span>
          </button>
        ))}
        <button type="button" className="ip-profile ip-profile--add" onClick={() => goSettings("profiles")}>
          <span className="ip-profile__tile"><Plus size={40} strokeWidth={1.5} /></span>
          <span className="ip-profile__name">Ajouter</span>
        </button>
      </div>
      {gate && state && state.accounts.length > 1 && (
        <div className="sx-who-accounts">
          <span className="muted st-small">Abonnement</span>
          {state.accounts.map((a) => (
            <button key={a.id} type="button" className={clsx("ip-chip ip-chip--sm", a.id === state.account && "is-selected")}
              onClick={async () => {
                try {
                  await stream.selectAccount(a.id);
                  await useStream.getState().load();
                } catch (e) {
                  toastError(e);
                }
              }}>{a.name}</button>
          ))}
        </div>
      )}
      <button type="button" className="ip-btn ip-btn--glass" onClick={() => goSettings("profiles")}><Pencil size={15} /> Gérer les profils</button>
      <Modal open={!!locked} onClose={() => setLocked(null)} eyebrow={<><Lock size={12} /> Profil protégé</>} title={`Code de « ${locked?.name} »`}
        actions={<><Button variant="ghost" onClick={() => setLocked(null)}>Annuler</Button><Button variant="primary" disabled={pin.length < 4} onClick={() => choose(locked!, pin)}>Ouvrir</Button></>}>
        <form onSubmit={(e) => { e.preventDefault(); choose(locked!, pin); }} style={{ marginTop: 12 }}>
          <PinField value={pin} onChange={setPin} autoFocus />
        </form>
        {err && <p className="st-err">{err}</p>}
      </Modal>
    </div>
  );
}
