import { Star } from "lucide-react";
import { ReactNode, useState } from "react";
import { CastMember, SimilarItem, Variant } from "./api";
import { Poster, QualityPicker } from "./components";

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
      {backdrop && <div className="st-rich-backdrop" style={{ backgroundImage: `url("${backdrop}")` }} aria-hidden />}
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
    <img className="st-face" src={p.photo} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBad(true)} />
  ) : (
    <span className="st-face st-face-none">{initials}</span>
  );
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
              sub={[s.year, s.rating ? <><Star size={10} /> {Number(s.rating).toFixed(1)}</> : null].filter(Boolean).map((x, k) => <span key={k}>{k ? " · " : ""}{x}</span>)} />
          </div>
        ))}
      </div>
    </div>
  );
}
