"use client";
import type { Session } from "@supabase/supabase-js";
import { useEffect, useState } from "react";
import { supabase } from "./supabase";

/** The signed-in session (null when signed out); `loading` is true until the first answer, so a guard never flashes. */
export function useSession(): { session: Session | null; loading: boolean } {
  const [state, setState] = useState<{ session: Session | null; loading: boolean }>({ session: null, loading: true });
  useEffect(() => {
    const sb = supabase();
    let live = true;
    void sb.auth.getSession().then(({ data }) => live && setState({ session: data.session, loading: false }));
    const { data } = sb.auth.onAuthStateChange((_event, session) => live && setState({ session, loading: false }));
    return () => { live = false; data.subscription.unsubscribe(); };
  }, []);
  return state;
}
