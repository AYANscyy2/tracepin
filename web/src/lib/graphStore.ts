"use client";

import { createContext, useContext } from "react";
import { createStore, useStore, type StoreApi } from "zustand";

/**
 * Selection for one Graph view. It lives here, NOT on the React Flow nodes array: each node
 * subscribes to a derived boolean (`highlighted.has(id)`, `selectedSpan === id`), so zustand
 * re-renders only the nodes whose own boolean flipped. The nodes array never changes with
 * selection. One store per GraphView, so nothing leaks across client-side navigations.
 */
export interface GraphSelection {
  /** span_ids of the selected finding */
  highlighted: ReadonlySet<string>;
  /** the span clicked in the graph; its attributes show under the canvas */
  selectedSpan: string | null;
  /** the explorer's handler: select the first finding that references the span */
  onSelectSpan: (id: string) => void;
  /** click or keyboard activation of a node */
  activate: (id: string) => void;
}

export type GraphStore = StoreApi<GraphSelection>;

export function createGraphStore(highlighted: ReadonlySet<string>, onSelectSpan: (id: string) => void): GraphStore {
  return createStore<GraphSelection>((set, get) => ({
    highlighted,
    selectedSpan: null,
    onSelectSpan,
    activate: (id) => {
      set({ selectedSpan: id });
      get().onSelectSpan(id);
    },
  }));
}

export const GraphStoreContext = createContext<GraphStore | null>(null);

export function useGraph<T>(selector: (s: GraphSelection) => T): T {
  const store = useContext(GraphStoreContext);
  if (!store) throw new Error("useGraph outside a GraphView");
  return useStore(store, selector);
}
