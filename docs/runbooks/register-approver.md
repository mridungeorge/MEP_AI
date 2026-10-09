# Register an approver

The Gate 3 approver signs with a **registration number**. A person cannot type their own number into the app: it is set by the
service, after someone has checked it on the public register, and the change (with what was checked) goes into the audit ledger.
At sign-off the approver must type the number again and it must match the one on file.

## Who may be an approver

A registered engineer who is responsible for the design in the state concerned:

- **NER** (National Engineering Register, Engineers Australia): search the register by name.
- **RPEQ** (Queensland): the Board of Professional Engineers Queensland public register.
- Other state schemes (for example Victoria's Registered Building Practitioner or engineer registration, NSW, ...): use that scheme's
  public register. Choose `OTHER` and write which one in the evidence.

## Steps (about 5 minutes, by an administrator you trust, not by the person themselves)

1. **Verify on the register.** Search the person by name and number on the register's own website. Check: the name matches, the number
   matches, the status is current/not suspended, and the area of practice covers mechanical services. Take a screenshot or PDF.
   Write down: the register, the date, the exact number, what you saw.
2. **Make sure the person has an account**: they have signed in once, or `scripts/seed_demo.py` / the Supabase dashboard (Authentication >
   Users > Invite) created them, and `app_user` has them as role `approver` in the right firm.
3. **Set the number with the service script** (needs `MEP_DB_URL` for the project; see `deploy/env.api.example`):

   ```
   uv run python scripts/admin_users.py register-approver \
       --email jo@firm.example --number "RPEQ 12345" --register RPEQ \
       --verified-by "A. Admin" \
       --evidence "RPEQ register search 12 Oct 2026: name and number match, status current, mechanical"
   ```

   The script refuses a malformed number, a user who is not an approver, and evidence shorter than a sentence. It writes two ledger
   entries: `app_user_changed` (before/after, by the database trigger) and `approver_registration_verified` (register, who checked,
   your evidence).
4. **Keep the screenshot** with the firm's records (the ledger holds your sentence, not the file).
5. **Check**: `uv run python scripts/admin_users.py show --firm "<firm name>"` lists the number next to the person.

## Changes and removal

- A number changes only through the same script (the old number is kept in the ledger entry as `previous`).
- Registrations lapse. Re-check every 12 months, and whenever a signed package is questioned. If a registration is suspended, change the
  person's role or number the same day; packages signed while it was valid stay as they were (they are immutable).
- The demo number `DEMO-0001` is a placeholder and is never valid for a real signature. Replace it before a real firm signs anything.

## Small firms (one engineer holds every gate)

If the firm has a single engineer, an administrator can switch the firm to `small_firm` mode (the engineer then acts as designer,
checker and approver under their own login): `admin_users.py set-signer-mode --firm "<name>" --mode small_firm` and
`admin_users.py grant-roles --email ... --roles designer,checker`. Every package, PDF and ledger entry of that firm then says
**NOT INDEPENDENTLY CHECKED**, and the mode change itself is ledgered. Independent checking is the default; use small_firm only when
there is genuinely nobody else, and tell the certifier.
