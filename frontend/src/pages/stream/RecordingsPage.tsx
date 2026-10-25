import { AlarmClock, CircleDot, FolderOpen, Play, Square, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { events } from "../../api/ws";
import { Badge, Button, Empty, Modal, PageHead } from "../../components/ui";
import { useApp } from "../../store/app";
import { Recording, stream } from "../../stream/api";
import { day, hm } from "../../stream/components";
import { toastError, useStream } from "../../stream/store";

const STATUS: Record<Recording["status"], { label: string; tone?: "accent" | "success" | "warning" | "info" }> = {
  scheduled: { label: "Programmé", tone: "info" },
  recording: { label: "En cours", tone: "accent" },
  done: { label: "Terminé", tone: "success" },
  failed: { label: "Échec", tone: "warning" },
  missed: { label: "Manqué", tone: "warning" },
  canceled: { label: "Annulé" },
  interrupted: { label: "Interrompu", tone: "warning" },
};

const size = (b?: number) => (!b ? "" : b > 1e9 ? `${(b / 1e9).toFixed(1)} Go` : `${Math.round(b / 1e6)} Mo`);

export function RecordingsPage() {
  const setPage = useApp((s) => s.setPage);
  const [recs, setRecs] = useState<Recording[]>([]);
  const [folder, setFolder] = useState("");
  const [reminders, setReminders] = useState<{ id: string; channel_name: string; title: string; start: number }[]>([]);
  const [del, setDel] = useState<Recording | null>(null);

  const load = useCallback(() => {
    stream.recordings().then((r) => { setRecs(r.recordings); setFolder(r.folder); }).catch(toastError);
    stream.reminders().then((r) => setReminders(r.reminders)).catch(() => undefined);
  }, []);
  useEffect(() => {
    load();
    const off = events.on((ev) => (ev.type as string) === "stream_recordings" && load());
    const t = window.setInterval(load, 10000);
    return () => { off?.(); window.clearInterval(t); };
  }, [load]);

  const upcoming = recs.filter((r) => r.status === "scheduled" || r.status === "recording");
  const past = recs.filter((r) => r.status !== "scheduled" && r.status !== "recording");

  const card = (r: Recording) => (
    <div key={r.id} className="st-rec glass-panel">
      <div className="st-rec-icon">{r.status === "recording" ? <CircleDot size={18} className="st-rec-live" /> : <CircleDot size={18} />}</div>
      <div className="st-rec-text">
        <div className="st-rec-title">{r.title}</div>
        <div className="muted st-small">
          {r.channel_name} · {day(r.start)} {hm(r.start)} – {hm(r.stop)}{r.source === "replay" ? " · replay" : ""}{r.size ? ` · ${size(r.size)}` : ""}
        </div>
        {r.error && <div className="st-err st-small">{r.error.split("\n")[0]}</div>}
      </div>
      <Badge tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Badge>
      <div className="row" style={{ gap: 6 }}>
        {r.status === "done" || r.status === "interrupted" || (r.status === "recording" && r.file) ? (
          <Button size="sm" iconOnly icon={<Play size={15} />} tip="Regarder" onClick={async () => {
            try {
              await stream.playRecording(r.id);
              useStream.setState({ now: { kind: "recording", id: r.id, title: r.title, subtitle: r.channel_name } });
              setPage("watch" as any);
            } catch (e) { toastError(e); }
          }} />
        ) : null}
        {r.file && <Button size="sm" iconOnly icon={<FolderOpen size={15} />} tip="Afficher le fichier" onClick={() => api.post("/api/system/open", { path: r.file, reveal: true }).catch(toastError)} />}
        {(r.status === "recording" || r.status === "scheduled") && (
          <Button size="sm" iconOnly icon={r.status === "recording" ? <Square size={13} /> : <X size={15} />} tip={r.status === "recording" ? "Arrêter" : "Annuler"}
            onClick={() => stream.stopRecording(r.id).then(load).catch(toastError)} />
        )}
        {r.status !== "recording" && <Button size="sm" iconOnly icon={<Trash2 size={15} />} tip="Supprimer" onClick={() => setDel(r)} />}
      </div>
    </div>
  );

  return (
    <div className="st-page">
      <PageHead eyebrow="Enregistrements" title="Tes enregistrements" sub={folder ? <>Dossier : <button className="st-link" onClick={() => api.post("/api/system/open", { path: folder }).catch(toastError)}>{folder}</button></> : undefined} />
      {upcoming.length > 0 && (<><h2 className="st-row-title">En cours et à venir</h2><div className="col" style={{ gap: 10 }}>{upcoming.map(card)}</div></>)}
      {reminders.length > 0 && (
        <>
          <h2 className="st-row-title" style={{ marginTop: 24 }}>Rappels</h2>
          <div className="col" style={{ gap: 10 }}>
            {reminders.map((r) => (
              <div key={r.id} className="st-rec glass-panel">
                <div className="st-rec-icon"><AlarmClock size={18} /></div>
                <div className="st-rec-text">
                  <div className="st-rec-title">{r.title}</div>
                  <div className="muted st-small">{r.channel_name} · {day(r.start)} {hm(r.start)}</div>
                </div>
                <Button size="sm" iconOnly icon={<X size={15} />} tip="Retirer" onClick={() => stream.unremind(r.id).then(load).catch(toastError)} />
              </div>
            ))}
          </div>
        </>
      )}
      {past.length > 0 && (<><h2 className="st-row-title" style={{ marginTop: 24 }}>Terminés</h2><div className="col" style={{ gap: 10 }}>{past.map(card)}</div></>)}
      {!recs.length && !reminders.length && (
        <Empty icon={<CircleDot size={22} />} title="Aucun enregistrement" body="Dans la TV en direct ou le guide, le bouton ● enregistre la chaîne, un programme à venir ou un replay." />
      )}
      <Modal open={!!del} onClose={() => setDel(null)} title="Supprimer cet enregistrement ?" eyebrow={del?.title}
        actions={<>
          <Button variant="ghost" onClick={() => setDel(null)}>Annuler</Button>
          <Button onClick={() => { stream.deleteRecording(del!.id, false).then(load).catch(toastError); setDel(null); }}>Retirer de la liste</Button>
          {del?.file && <Button variant="danger" onClick={() => { stream.deleteRecording(del!.id, true).then(load).catch(toastError); setDel(null); }}>Supprimer le fichier aussi</Button>}
        </>}>
        <p>Le fichier peut rester sur ton PC, ou être supprimé avec.</p>
      </Modal>
    </div>
  );
}
