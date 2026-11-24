import { createContext, useContext, useState, type ReactNode } from "react";

type Ctx = { storeId: number | null; setStoreId: (id: number | null) => void };
const ActiveStore = createContext<Ctx>({ storeId: null, setStoreId: () => {} });
const KEY = "activeStoreId";

/** A global "active store" used to scope the inbox, board and reports. null = all stores. */
export function ActiveStoreProvider({ children }: { children: ReactNode }) {
  const [storeId, set] = useState<number | null>(() => {
    const v = localStorage.getItem(KEY);
    return v ? Number(v) : null;
  });
  const setStoreId = (id: number | null) => {
    if (id === null) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, String(id));
    set(id);
  };
  return <ActiveStore.Provider value={{ storeId, setStoreId }}>{children}</ActiveStore.Provider>;
}

export const useActiveStore = () => useContext(ActiveStore);
