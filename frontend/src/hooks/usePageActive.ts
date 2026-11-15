import { createContext, useContext } from "react";

/* Les pages gardées en mémoire (App.tsx, KEEP_ALIVE) restent montées quand on change d'onglet : y revenir est
 * instantané, avec la même position dans la liste. Une page cachée reçoit `false` ici et met ses minuteurs en pause. */
export const PageActive = createContext(true);
export const usePageActive = (): boolean => useContext(PageActive);
