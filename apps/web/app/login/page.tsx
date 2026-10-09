"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useSession } from "@/lib/session";
import { supabase } from "@/lib/supabase";

/** Email magic link: the user types their address, Supabase Auth emails a one-time link. Unknown addresses get no account. */
export default function LoginPage() {
  const { session } = useSession();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent">("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (session) router.replace("/");
  }, [session, router]);

  async function send() {
    setError(null);
    setState("sending");
    const { error: err } = await supabase().auth.signInWithOtp({
      email: email.trim(),
      options: { shouldCreateUser: false, emailRedirectTo: `${window.location.origin}/` },
    });
    if (err) {
      setState("idle");
      setError(err.message);
    } else {
      setState("sent");
    }
  }

  return (
    <main style={{ maxWidth: 420, margin: "10vh auto" }}>
      <h1>Sign in</h1>
      {state === "sent" ? (
        <p role="status">Check your email for the sign-in link, then open it in this browser.</p>
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); void send(); }}>
          <label>
            Email address
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
                   style={{ display: "block", width: "100%", margin: "4px 0 12px" }} />
          </label>
          <button type="submit" disabled={state === "sending" || email.trim() === ""}>Send sign-in link</button>
        </form>
      )}
      {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
    </main>
  );
}
