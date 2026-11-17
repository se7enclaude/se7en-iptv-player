import clsx from "clsx";
import { ChevronDown, Languages, Play, Star } from "lucide-react";
import { ReactNode, useEffect, useState } from "react";
import { CastMember, pic, SimilarItem, stream, Variant, WorkVersion } from "./api";
import { ItemMeta, Poster, QualityPicker } from "./components";
import { Button } from "../components/ui";

/** Film / series sheet head: backdrop, poster, facts, genres, the versions (SD / HD / 4K), plot, people, actions. */
export function DetailHead({ name, poster, backdrop, tagline, facts, genres, plot, director, directorLabel = "Réalisation", cast, variants, current, onVariant, actions, tmdbError }: {
  name: string;
  poster?: string | null;
  backdrop?: string | null;
  tagline?: string;
  facts: ReactNode[];
  genres?: string[];
  plot?: string;
  director?: string;
  directorLabel?: string;
  cast?: CastMember[];
  variants?: Variant[];
  current?: string | null;
  onVariant?: (id: string) => void;
  actions: ReactNode;
  tmdbError?: string;
}) {
  return (
    <div className="st-rich">
      {backdrop && <div className="st-rich-backdrop" style={{ backgroundImage: `url("${pic(backdrop, 1280)}")` }} aria-hidden />}
      <div className="st-detail">
        <Poster src={poster} name={name} />
        <div className="col" style={{ gap: 10, minWidth: 0 }}>
          {tagline && <p className="st-tagline">{tagline}</p>}
          <div className="st-meta">{facts.filter(Boolean).map((f, k) => <span key={k}>{f}</span>)}</div>
          {!!genres?.length && (
            <div className="st-genres">{genres.map((g) => <span key={g}>{g}</span>)}</div>
          )}
          {!!variants?.length && onVariant && <QualityPicker variants={variants} current={current ?? null} onPick={onVariant} label="Version" />}
          {plot && <p className="st-plot">{plot}</p>}
          {director && <p className="st-small"><b>{directorLabel}</b> {director}</p>}
          <div className="row" style={{ gap: 10, flexWrap: "wrap", marginTop: 4 }}>{actions}</div>
          {tmdbError && <p className="st-small muted">{tmdbError}</p>}
        </div>
      </div>
      {!!cast?.length && <CastRow cast={cast} />}
    </div>
  );
}

function Face({ p }: { p: CastMember }) {
  const [bad, setBad] = useState(false);
  const initials = p.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  return p.photo && !bad ? (
    <img className="st-face" src={pic(p.photo, 96)} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setBad(true)} />
  ) : (
    <span className="st-face st-face-none">{initials}</span>
  );
}

/** La note : IMDb quand on la connaît (avec le nombre de votes), sinon celle du fournisseur. */
export function RatingFact({ imdb, votes, rating }: { imdb?: number | null; votes?: number | null; rating?: number | null }) {
  if (imdb) {
    const v = votes ? (votes >= 1e6 ? `${(votes / 1e6).toFixed(1).replace(".", ",")} M` : votes >= 1e3 ? `${Math.round(votes / 1e3)} k` : `${votes}`) : "";
    return <span className="st-imdb" title={votes ? `${votes.toLocaleString("fr-FR")} votes sur IMDb` : "Note IMDb"}><b>IMDb</b> {imdb.toFixed(1).replace(".", ",")}{v && <i> · {v}</i>}</span>;
  }
  if (rating) return <span className="st-meta-rating"><Star size={12} fill="currentColor" strokeWidth={0} />{Number(rating).toFixed(1).replace(".", ",")}</span>;
  return null;
}

/** Bande-annonce YouTube, dans une fenêtre de l'appli. */
export function TrailerButton({ id, title }: { id?: string | null; title?: string }) {
  if (!id || !/^[A-Za-z0-9_-]{6,20}$/.test(id)) return null;
  return <Button icon={<Play size={15} />} onClick={() => stream.trailer(id, title).catch(() => undefined)}>Bande-annonce</Button>;
}

export function CastRow({ cast }: { cast: CastMember[] }) {
  return (
    <div className="st-cast">
      <div className="st-row-title">Distribution</div>
      <div className="st-cast-list">
        {cast.map((p, k) => (
          <div key={`${p.name}-${k}`} className="st-cast-item" title={p.role ? `${p.name} · ${p.role}` : p.name}>
            <Face p={p} />
            <span className="st-cast-name">{p.name}</span>
            {p.role && <span className="st-cast-role">{p.role}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

export function SimilarRow({ items, onOpen, title = "Titres similaires" }: { items?: SimilarItem[]; onOpen: (id: string) => void; title?: string }) {
  if (!items?.length) return null;
  return (
    <div className="st-similar">
      <div className="st-row-title">{title}</div>
      <div className="st-similar-list">
        {items.map((s) => (
          <div key={s.id} className="st-similar-item">
            <Poster src={s.logo} name={s.name} progress={s.progress ?? undefined} onClick={() => onOpen(s.id)}
              sub={s.year || s.rating ? <ItemMeta year={s.year} rating={s.rating} /> : undefined} />
          </div>
        ))}
      </div>
    </div>
  );
}


/**
 * « Toutes les versions » : le même film (ou la même série) dans toutes les langues et qualités du catalogue,
 * rangé par langue (la tienne d'abord). Un clic ouvre cette version, ▶ la lance tout de suite.
 */
export function AllVersions({ type, id, current, onPick, onPlay }: { type: "movie" | "series"; id: string; current: string; onPick: (id: string) => void; onPlay?: (id: string) => void }) {
  const [list, setList] = useState<WorkVersion[] | null>(null);
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    setErr("");
    stream.versions(type, id).then((r) => alive && setList(r.versions)).catch((e) => alive && (setList([]), setErr(e?.message ?? String(e))));
    return () => { alive = false; };
  }, [type, id]);
  const langs = new Map<string, WorkVersion[]>();
  for (const v of list ?? []) langs.set(v.lang_label, [...(langs.get(v.lang_label) ?? []), v]);
  const others = (list ?? []).filter((v) => v.id !== current).length;
  if (list && others === 0 && !err) return null;
  return (
    <div className={clsx("st-versions", open && "open")}>
      <button type="button" className="st-versions-toggle" onClick={() => setOpen(!open)} disabled={!list}>
        <Languages size={15} />
        {list ? <>Toutes les versions <b>{list.length}</b> · {langs.size} langue{langs.size > 1 ? "s" : ""}</> : "Recherche des autres langues et qualités…"}
        {list && <ChevronDown size={15} className="st-versions-chev" />}
      </button>
      {open && list && (
        <div className="st-versions-list">
          {[...langs.entries()].map(([lang, vs]) => (
            <div key={lang} className="st-versions-lang">
              <span className="st-versions-lang-name">{lang}</span>
              <div className="st-versions-chips">
                {vs.map((v) => (
                  <span key={v.id} role="button" tabIndex={0} className={clsx("st-version", v.id === current && "on")} title={v.name + (v.category ? ` · ${v.category}` : "")}
                    onClick={() => onPick(v.id)} onKeyDown={(e) => e.key === "Enter" && onPick(v.id)}>
                    <span className="st-version-q">{[v.quality, ...v.tags].filter(Boolean).join(" · ") || "Standard"}</span>
                    {v.video && <span className="st-version-video">{v.video}</span>}
                    {onPlay && (
                      <button type="button" className="st-version-play" aria-label={`Lancer ${v.name}`} onClick={(e) => { e.stopPropagation(); onPlay(v.id); }}>
                        <Play size={12} />
                      </button>
                    )}
                  </span>
                ))}
              </div>
            </div>
          ))}
          {err && <p className="st-small muted">{err}</p>}
        </div>
      )}
    </div>
  );
}
